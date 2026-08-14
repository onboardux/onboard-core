"""The outside-VCS discipline, through a whole run -- `01` F8.6, F8.7, `05` S1.5.

*"A prompt in a hosted console, a model pin resolved at deploy time, a retrieval
index configured in a vendor UI: … Every such identity also produces a gap.
`surface.md` states the count on its first screen: these are the places behaviour
can change with no commit."*

**Through the orchestrator rather than against the extractors**, because the
claim is about what a *run* reports: the flag has to survive minting, the digest,
the writer and two emitters, and every one of those is a place it could be
dropped without any extractor changing.

*Defect sentence.* Fails when an outside-VCS identity stops producing a gap, when
an unreadable prompt acquires content or a non-null semantic digest, or when the
first screen stops naming the count; matters because these three sentences are
the only thing separating an honest AI map from one that implies a client's
behaviour is fully described by their repository; no other instrument catches it,
because every count stays right and every file is still written -- the map simply
stops saying what it cannot see.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from adopt_extractors_ai import pack as ai_pack
from adopt_extractors_common import pack as common_pack
from adopt_map.emit.json_report import SURFACE_JSON_NAME
from adopt_map.orchestrator import run as run_map
from adopt_map.plugins import ExtractorRegistry
from adopt_map.report import RunResult
from adopt_map.sourceversion import parse_source_version

from adopt_store.api import SqliteStoreHandle
from tests.build1_conftest import build_scoped_store, surface_writer_for

pytestmark = pytest.mark.integration

_TREE = Path("fixtures/repos/langgraph-support")
_LABELS = Path("fixtures/labeled/langgraph-support.identities.json")


class Run(NamedTuple):
    """One run, its artifacts, and the store it wrote to.

    The store is carried so the digest assertions can read what was **written**
    rather than recompute what would have been: a composite the writer never
    stored is a composite nobody depends on.
    """

    result: RunResult
    out_dir: Path
    handle: SqliteStoreHandle


@pytest.fixture
def run(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Run]:
    """One real run over the AI fixture, with the `ai` pack enabled.

    The scope's archetype is `ai`: the registry plans on it, so a `web` scope
    would skip all six extractors with `archetype_mismatch` and every assertion
    below would pass over an empty run.
    """
    work = tmp_path_factory.mktemp("ai")
    handle, scopes = build_scoped_store(work, archetype="ai")
    out_dir = work / "out"
    registry = ExtractorRegistry(enabled_packs=frozenset({"common", "ai"}))
    registry.register_all(common_pack())
    registry.register_all(ai_pack())
    try:
        result = run_map(
            resolved=scopes["prod"],
            root=_TREE,
            registry=registry,
            adopt_version="test",
            writer=surface_writer_for(handle),
            coverage_records=handle.coverage_records(),
            cache=handle.backend,
            out_dir=out_dir,
            formats=("md", "json"),
            sequential=True,
        )
        yield Run(result, out_dir, handle)
    finally:
        handle.close()


def _stored_source_versions(handle: SqliteStoreHandle) -> dict[str, str]:
    """`{identity uri: the composite the writer stored}`, from the knowledge chain.

    **Through `knowledge_revision`, not `identity_revision`, and that is B1-CR-48
    rather than a shortcut.** The *creating* identity revision is written by Build
    0's `IdentityFacade.observe`, whose signature has no `source_version`
    parameter and whose package Build 1 may not edit -- so on a first run the
    composite exists only on the knowledge revision, which is Build 1's own
    draft. A test that read `identity_revision` here would find nothing and would
    have to be "fixed" by asserting less.

    The join is `identity -> binding -> knowledge_item -> knowledge_revision`,
    which is the same path the writer's own comparison takes.
    """
    from adopt_model import Binding, Identity, KnowledgeItem, KnowledgeRevision

    records = handle.export_records()
    uri_of = {row.id: row.uri for row in records.table_rows("identity", Identity)}
    item_of = {row.identity_id: row.item_id for row in records.table_rows("binding", Binding)}
    head_of = {
        row.id: row.current_revision_id
        for row in records.table_rows("knowledge_item", KnowledgeItem)
    }
    composite_of = {
        row.id: row.source_version
        for row in records.table_rows("knowledge_revision", KnowledgeRevision)
    }

    versions: dict[str, str] = {}
    for identity_id, uri in uri_of.items():
        head = head_of.get(item_of.get(identity_id, ""))
        composite = composite_of.get(head or "")
        if composite is not None:
            versions[uri] = composite
    return versions


def _labeled_outside_vcs() -> int:
    payload = json.loads(_LABELS.read_text(encoding="utf-8"))
    return sum(1 for item in payload["identities"] if item.get("outside_vcs"))


def test_every_outside_vcs_identity_produces_a_gap(run: Run) -> None:
    """`01` F8.6's *"every such identity also produces a gap"*, as an equality.

    A subset assertion would pass an implementation that emitted one gap for the
    whole run, and a superset assertion would pass one that gapped everything.
    """
    assert run.result.write_result is not None
    gapped = {
        gap.identity_uri for gap in run.result.write_result.gaps if gap.reason == "outside_vcs"
    }
    assert gapped == set(run.result.outside_vcs())
    assert gapped, "the fixture declares outside-VCS identities and none was gapped"


def test_the_outside_vcs_count_matches_the_labeled_truth(run: Run) -> None:
    """`05` S1.5's validation line, in CI as well as by hand.

    The labeled set is the fixture's *specification*; the run is the observation.
    Comparing them is the only version of this assertion that can fail -- counting
    the run against itself cannot.
    """
    assert len(run.result.outside_vcs()) == _labeled_outside_vcs()


def test_an_unreadable_prompt_has_a_null_digest_a_gap_and_no_content(
    run: Run,
) -> None:
    """`01` F8.7, all three halves in one assertion, because they fail apart.

    A null digest with invented content is a lie that compares equal to itself
    forever; content with a real digest is an invention that looks like evidence;
    and either without a gap is a run that never mentions the prompt it could not
    read.
    """
    console = [
        entry
        for entry in run.result.minted()
        if entry.fact.identity_kind == "prompt" and entry.fact.namespace == "console"
    ]
    assert console, "the fixture declares a console prompt"
    stored = _stored_source_versions(run.handle)
    for entry in console:
        assert entry.fact.opaque is True
        assert entry.fact.attributes == {}, "content was invented for an unreadable prompt"
        # Read from the store rather than recomputed: a composite the writer
        # never wrote is one nothing downstream depends on.
        composite = stored[entry.uri]
        version = parse_source_version(composite)
        assert version.sem is None, f"an opaque prompt carried a semantic digest: {composite}"
        assert "opaque" in version.flags

    assert run.result.write_result is not None
    reasons = {
        gap.reason
        for gap in run.result.write_result.gaps
        if gap.identity_uri in {entry.uri for entry in console}
    }
    # `outside_vcs` must be among them; `provenance_unrecordable` legitimately
    # joins it here, because this fixture is not a checkout and B1-CR-36 records
    # that rather than claiming a commit nobody observed.
    assert "outside_vcs" in reasons


def test_the_first_screen_states_the_outside_vcs_count(run: Run) -> None:
    """`02` §9.1 item 7, with the sentence `01` F8.6 asks for.

    Asserted against the rendered artifact rather than the renderer, because the
    claim is that a reader who stops after one screen has the number.
    """
    out_dir = run.out_dir
    markdown = (out_dir / "surface.md").read_text(encoding="utf-8")
    first_screen = markdown.split("## 8. Inventory")[0]
    count = len(run.result.outside_vcs())
    assert (
        f"**{count} of {run.result.total_facts()} behaviour-bearing settings live outside version "
        "control; changes there produce no commit.**" in first_screen
    )


def test_the_floating_pin_gets_its_own_callout(run: Run) -> None:
    """`01` F8.8: *"the single highest-value finding this pack produces"*.

    On the first screen, above the inventory, naming the pin. A floating pin that
    appears only as one row among forty-two inventory lines is a finding nobody
    reads.
    """
    out_dir = run.out_dir
    markdown = (out_dir / "surface.md").read_text(encoding="utf-8")
    first_screen = markdown.split("## 8. Inventory")[0]
    assert "Floating model pins" in first_screen
    assert "gpt-4o-latest" in first_screen


def test_the_run_artifact_carries_the_flag_per_fact(run: Run) -> None:
    """`02` §9.2's `facts[].outside_vcs`, which `label_eval`'s M8 subset reads.

    Without this the M8 measurement has no input: `scripts/label_eval.py` scores
    the outside-VCS subset from the artifact, and a run that knew the flag but
    never wrote it would report M8 0.000 while `surface.md` printed the right
    count.
    """
    out_dir = run.out_dir
    payload = json.loads((out_dir / SURFACE_JSON_NAME).read_text(encoding="utf-8"))
    flagged = {fact["identity_uri"] for fact in payload["facts"] if fact["outside_vcs"]}
    assert flagged == set(run.result.outside_vcs())
