"""The hard gates, over the three archetypes S1.6 adds -- `05` S1.6, `03` §8.

`05` S1.6's QA workstream: *"Extend the perf baseline and **every gate** to all
five archetypes."* The four hard gates already run on `web` and `ai`; this module
is the same four claims over `platform`, `lowcode` and `data`, parameterized so a
sixth archetype is one row rather than a fourth copy.

**Why these need to run per archetype at all**, rather than once on the web
fixture: every one of the four is a property of *what the extractors emit*, and
S1.6's three packs emit differently from anything before them. Two of them read
an **export bundle** rather than a source tree, which is a different subject
entirely; the data pack is the first to emit `derives_from` **relations**, which
travel into `body_md` and therefore into the digest (`02` §4.2, B1-CR-44). A
lineage edge that reordered between runs would break idempotence on a tree nobody
touched, and no earlier fixture could have shown it.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from adopt_extractors_common import pack as common_pack
from adopt_extractors_data import pack as data_pack
from adopt_extractors_lowcode import pack as lowcode_pack
from adopt_extractors_platform import pack as platform_pack
from adopt_map.orchestrator import run as run_map
from adopt_map.plugins import ExtractorRegistry
from adopt_map.report import RunResult
from adopt_map.writer import SurfaceWriter

from tests.build1_conftest import build_scoped_store, surface_writer_for

pytestmark = pytest.mark.integration


class Archetype(NamedTuple):
    """One archetype, its fixture, and whether that fixture is a bundle."""

    name: str
    pack: object
    tree: Path
    is_bundle: bool


ARCHETYPES: tuple[Archetype, ...] = (
    Archetype("platform", platform_pack, Path("fixtures/repos/sf-metadata-bundle"), True),
    Archetype("lowcode", lowcode_pack, Path("fixtures/repos/powerapps-export"), True),
    Archetype("data", data_pack, Path("fixtures/repos/dbt-warehouse"), False),
)
_IDS = [entry.name for entry in ARCHETYPES]


class Harness(NamedTuple):
    run: object
    writer: SurfaceWriter
    scopes: dict[str, object]


@pytest.fixture(params=ARCHETYPES, ids=_IDS)
def harness(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Archetype, object]]:
    """A store, a registry and a callable that runs the archetype's fixture.

    One fixture rather than one per gate: four gates over three archetypes is
    twelve stores, and a store per gate is what keeps each gate honest about its
    own starting state.
    """
    entry: Archetype = request.param
    work = tmp_path_factory.mktemp(f"s16-{entry.name}")
    handle, scopes = build_scoped_store(
        work, environments=("prod", "staging"), archetype=entry.name
    )
    registry = ExtractorRegistry(enabled_packs=frozenset({"common", entry.name}))
    registry.register_all(common_pack())
    registry.register_all(entry.pack())  # type: ignore[operator]
    writer = surface_writer_for(handle)
    counter = {"n": 0}

    def run(environment: str = "prod", *, write: bool = True) -> RunResult:
        counter["n"] += 1
        return run_map(
            resolved=scopes[environment],
            root=Path() if entry.is_bundle else entry.tree,
            export_bundle=entry.tree if entry.is_bundle else None,
            registry=registry,
            adopt_version="test",
            writer=writer if write else None,
            out_dir=work / f"out{counter['n']}",
            sequential=True,
        )

    try:
        yield entry, run
    finally:
        handle.close()


@pytest.mark.idempotence
def test_a_second_run_writes_no_revision(harness: tuple[Archetype, object]) -> None:
    """*Defect sentence.* Fails when a second run over an unchanged bundle or
    project writes a revision; matters because `01` F4 is the criterion this
    rebuild exists for and every downstream delta becomes noise the moment it
    breaks; no other instrument catches it -- one run looks perfect, and the
    per-extractor determinism cases pass on either side of a merge that
    alternates (B1-CR-68).
    """
    entry, run = harness
    first = run()  # type: ignore[operator]
    assert first.total_facts() > 0, f"{entry.name} emitted nothing; the gate would be vacuous"

    for index in (2, 3):
        again = run()  # type: ignore[operator]
        assert again.write_result is not None
        assert dict(again.write_result.revisions_written) == {
            "identity": 0,
            "knowledge": 0,
            "binding": 0,
        }, f"{entry.name} run {index} wrote revisions on an unchanged subject"


@pytest.mark.determinism
def test_two_runs_mint_one_identity_set(harness: tuple[Archetype, object]) -> None:
    """`01` N4: identical URI set **and** identical `source_version` values.

    *Defect sentence.* Fails when emission order or digest content depends on
    anything but the subject -- hash seeding, filesystem order, a set iterated
    somewhere; matters because two runs disagreeing is indistinguishable from a
    client's system having changed; no other instrument catches the digest half,
    because the URI set can be stable while a relation list reorders inside
    `body_md`.
    """
    _, run = harness
    first = run(write=False)  # type: ignore[operator]
    second = run(write=False)  # type: ignore[operator]
    assert [entry_.uri for entry_ in first.minted()] == [entry_.uri for entry_ in second.minted()]
    assert [
        [(relation.predicate, relation.target_local_key) for relation in entry_.fact.relations]
        for entry_ in first.minted()
    ] == [
        [(relation.predicate, relation.target_local_key) for relation in entry_.fact.relations]
        for entry_ in second.minted()
    ]


@pytest.mark.env_isolation
def test_a_staging_run_emits_no_production_uri(harness: tuple[Archetype, object]) -> None:
    """`01` F6, CUJ-6: staging cannot name production.

    *Defect sentence.* Fails when an environment segment comes from anywhere but
    `ResolvedScope`; matters because the whole tenancy story rests on it and the
    failure is silent -- a staging run writing production URIs looks like a
    successful run; no other instrument catches it per archetype, because the
    fuzz suite proves the *mechanism* on synthetic facts and this proves it on
    the facts these packs really emit.
    """
    entry, run = harness
    staging = run("staging", write=False)  # type: ignore[operator]
    uris = [entry_.uri for entry_ in staging.minted()]
    assert uris, f"{entry.name} emitted nothing from staging"
    assert all("/staging/" in uri for uri in uris)
    assert not any("/prod/" in uri for uri in uris)


def test_every_kind_in_the_artifact_is_a_closed_enum_member(
    harness: tuple[Archetype, object], tmp_path: Path
) -> None:
    """`05` S1.6's third validation line, in CI rather than at a prompt.

    That line is `jq -r '.facts[].identity_kind' … | sort -u`, and **`jq` is not
    installed on the authoring machine** -- the fourth sprint to record it. The
    command was run through a Python equivalent over the identical artefact; this
    is the same assertion where it cannot rot.

    *Defect sentence.* Fails when a pack emits a kind outside Build 0's closed
    enum; matters because `02` §3.1 rule 1 makes that enum Build 0's and closed,
    and a new member invented by an extractor is a referent no downstream build
    can interpret; no other instrument catches it at the **artefact** level --
    the conformance suite compares emitted kinds against each extractor's own
    manifest, which a pack that widened both would satisfy perfectly.
    """
    from typing import get_args

    from adopt_map.emit import render_surface_json

    from adopt_model._enums import IdentityKind

    entry, run = harness
    result = run(write=False)  # type: ignore[operator]
    # The artefact's own bytes, through the emitter the run wrote them with --
    # not a recount off `RunResult`, which would assert the same thing one layer
    # before the file anybody actually reads.
    payload = json.loads(render_surface_json(result))
    kinds = {fact["identity_kind"] for fact in payload["facts"]}
    assert kinds, f"{entry.name} emitted no facts; the sweep would be vacuous"
    assert kinds <= set(get_args(IdentityKind)), sorted(kinds - set(get_args(IdentityKind)))


@pytest.mark.append_only
def test_the_run_writes_no_update_and_no_delete(harness: tuple[Archetype, object]) -> None:
    """`01` N5, through a real run over each new archetype.

    *Defect sentence.* Fails when any statement this run issues updates a
    `*_revision` row or deletes anything; matters because append-only is what
    makes provenance survivable and there is no delete path to add one back; no
    other instrument catches it here, because the SQL-trace suite runs on the web
    fixture and these packs write through the same writer with different facts.
    """
    entry, run = harness
    statements: list[str] = []
    from adopt_store.sqlite import store as sqlite_store

    original = sqlite_store.SqliteStore.execute

    def spy(self: object, sql: str, *args: object, **kwargs: object) -> object:
        statements.append(sql)
        return original(self, sql, *args, **kwargs)  # type: ignore[arg-type]

    sqlite_store.SqliteStore.execute = spy  # type: ignore[assignment,method-assign]
    try:
        run()  # type: ignore[operator]
    finally:
        sqlite_store.SqliteStore.execute = original  # type: ignore[method-assign]

    assert statements, f"{entry.name}: no SQL observed; the trace would be vacuous"
    for statement in statements:
        # **The same definition `test_surface_writer` uses, not a second one.**
        # The first version of this assertion matched `"_revision" in sql`, which
        # flags `UPDATE knowledge_item SET current_revision_id = ?` -- a *parent
        # pointer* advance, which `02` §6 requires. A gate with its own looser
        # definition of the rule is a gate that fails on correct code and gets
        # relaxed until it fails on nothing.
        normalized = " ".join(statement.split()).upper()
        assert "DELETE " not in normalized, statement
        assert " DROP " not in f" {normalized} ", statement
        if normalized.startswith("UPDATE "):
            target = normalized.split()[1].strip('"`[]').lower()
            assert not target.endswith("_revision"), statement
            assert target in {"identity", "knowledge_item", "binding"}, statement
