"""Re-run idempotence -- PRD F4, N3, M3; contracts §4.3, §10 C10; `03` §8.

**This is an acceptance criterion, not an optimization** (PRD F4's own words).
`03` §10 puts it first in the alarm table: *"`revisions_written` non-zero on an
unchanged re-run — idempotence broken — stop the line; every downstream delta
becomes noise."* Everything Builds 3, 5 and 10 compute is a query over these
revisions, so a writer that appends on every scan does not produce a noisy
signal, it produces no signal at all.

| Behavior | Tier | Defect it catches |
|---|---|---|
| A second run writes zero revisions on all three tables | **T1** | The whole of F4 |
| ...and the third and fourth runs too | **T1** | A rule that settles one run late |
| `identity.last_seen` still advances | **T1** | "Zero revisions" achieved by not looking |
| A semantic change writes exactly one identity + knowledge revision | **T1** | A change that vanishes, or one that duplicates |
| The chain is linked and the head advances | **T1** | An orphan revision or a stale pointer |
| A presentation-only change writes a revision and keeps `sem` | **T1** | The render-only signal Build 10 depends on |
| An extractor-version bump is named as the cause | T2 | An operator unable to tell a tool change from a real one |
"""

import datetime as _dt
from collections.abc import Callable, Sequence

import pytest
from adopt_map.schemas import ExtractorManifest, SurfaceFact
from adopt_map.scope_resolve import ResolvedScope
from adopt_map.sourceversion import parse_source_version
from adopt_map.writer import SurfaceWriter, SurfaceWriteResult

from adopt_model import Identity, IdentityRevision, KnowledgeItem, KnowledgeRevision
from adopt_obs import ManualClock
from adopt_store.api import SqliteStoreHandle

pytestmark = [pytest.mark.integration, pytest.mark.idempotence]

_MANIFEST = ExtractorManifest(
    id="common.fixture", version="1.0.0", pack="common", kinds=["endpoint"], method="grammar"
)

_ZERO = {"identity": 0, "knowledge": 0, "binding": 0}


def _facts(*, path: str = "/api/v1/orders", summary: str | None = None) -> list[SurfaceFact]:
    return [
        SurfaceFact(
            identity_kind="endpoint",
            namespace="http",
            local_key="GET /api/v1/orders",
            title="GET /api/v1/orders",
            attributes={"http_method": "GET", "path": path, "summary": summary},
        )
    ]


@pytest.fixture
def run(
    surface_writer: SurfaceWriter, resolved_scope: ResolvedScope
) -> Callable[..., SurfaceWriteResult]:
    def _run(
        facts: Sequence[SurfaceFact] | None = None,
        *,
        manifest: ExtractorManifest = _MANIFEST,
        vcs_revision: str | None = None,
    ) -> SurfaceWriteResult:
        return surface_writer.write_run(
            resolved=resolved_scope,
            manifest=manifest,
            facts=list(_facts()) if facts is None else list(facts),
            vcs_revision=vcs_revision,
        )

    return _run


def _rows[TModel](handle: SqliteStoreHandle, table: str, model: type[TModel]) -> list[TModel]:
    return list(handle.export_records().table_rows(table, model))


def test_a_second_run_on_an_unchanged_tree_writes_zero_revisions(
    run: Callable[..., SurfaceWriteResult], s4_store: SqliteStoreHandle
) -> None:
    """M3: **0, always.** The single most load-bearing assertion in the build."""
    first = run()
    assert first.revisions_written == {"identity": 1, "knowledge": 1, "binding": 1}
    before = {
        table: len(_rows(s4_store, table, model))
        for table, model in (
            ("identity_revision", IdentityRevision),
            ("knowledge_revision", KnowledgeRevision),
        )
    }

    second = run()

    assert second.revisions_written == _ZERO
    after = {
        table: len(_rows(s4_store, table, model))
        for table, model in (
            ("identity_revision", IdentityRevision),
            ("knowledge_revision", KnowledgeRevision),
        )
    }
    assert after == before, "the counters said zero; the tables must agree"


def test_further_unchanged_runs_stay_at_zero(
    run: Callable[..., SurfaceWriteResult],
) -> None:
    """Fails when the rule settles one run late.

    Matters because a writer that is quiet on run two and noisy on run three has
    a comparison that depends on how many revisions exist rather than on what
    they say -- and CUJ-2 is every run after the first, not the second one. No
    other instrument catches it: a two-run test passes perfectly.
    """
    run()
    for _ in range(3):
        assert run().revisions_written == _ZERO


def test_an_unchanged_run_still_advances_last_seen(
    run: Callable[..., SurfaceWriteResult],
    s4_store: SqliteStoreHandle,
    s4_clock: ManualClock,
) -> None:
    """`02` §4.3 row 1: *"touch `identity.last_seen` only"* -- CUJ-2 step 3.

    Fails when zero revisions is achieved by not looking at all; matters because
    `last_seen` is how absence is expressed (PRD F5.3) and a run that skips the
    observation makes every identity look abandoned; no other instrument catches
    it because the revision counters are zero either way -- which is exactly what
    a broken run and a correct run have in common.
    """
    run()
    first_seen = _rows(s4_store, "identity", Identity)[0].last_seen

    s4_clock.advance(_dt.timedelta(seconds=60))
    run()

    assert _rows(s4_store, "identity", Identity)[0].last_seen > first_seen


def test_a_semantic_change_writes_one_revision_per_changed_table(
    run: Callable[..., SurfaceWriteResult], s4_store: SqliteStoreHandle
) -> None:
    """Exactly one, and only where that table's content moved (B1-CR-47).

    The binding stays at zero: a `binding_revision` records an extractor, its
    version, a confidence and a locator rung, and a changed path moves none of
    them. Appending one anyway would make the chain a log of scans.
    """
    run()
    result = run(_facts(path="/api/v2/orders"))

    assert result.revisions_written == {"identity": 1, "knowledge": 1, "binding": 0}

    revisions = _rows(s4_store, "knowledge_revision", KnowledgeRevision)
    assert len(revisions) == 2
    head = next(row for row in revisions if row.supersedes_revision_id is not None)
    root = next(row for row in revisions if row.supersedes_revision_id is None)
    assert head.supersedes_revision_id == root.id, "the chain is linked"

    item = _rows(s4_store, "knowledge_item", KnowledgeItem)[0]
    assert item.current_revision_id == head.id, "the head pointer advanced"


def test_a_presentation_only_change_writes_a_revision_and_holds_sem(
    run: Callable[..., SurfaceWriteResult], s4_store: SqliteStoreHandle
) -> None:
    """`02` §4.3 row 2 -- the render-only signal, without a model call.

    Fails when a cosmetic edit either writes nothing or moves the semantic
    digest; matters because this equality is the deterministic answer Build 10's
    cascade asks for at step 3, and both failures make it useless -- one hides
    the change, the other calls a label change a behaviour change.
    """
    run()
    result = run(_facts(summary="Lists orders"))

    assert result.revisions_written["knowledge"] == 1
    digests = [
        parse_source_version(row.source_version)
        for row in _rows(s4_store, "knowledge_revision", KnowledgeRevision)
        if row.source_version is not None
    ]
    assert len({digest.sem for digest in digests}) == 1, "sem must not move"
    assert len({digest.ren for digest in digests}) == 2, "ren must move"


def test_an_extractor_version_bump_is_reported_as_the_cause(
    run: Callable[..., SurfaceWriteResult],
) -> None:
    """PRD F4.5 and CUJ-2's failure branch.

    Fails when revisions appear with no stated cause; matters because an operator
    seeing `revisions_written > 0` on a tree they did not touch has to be able to
    tell a tooling change from a real one without reading the diff; no other
    instrument catches it because the revisions themselves are legitimate.
    """
    run()
    bumped = _MANIFEST.model_copy(update={"version": "1.1.0"})
    result = run(manifest=bumped)

    assert result.extractor_version_changes == {"common.fixture": ("1.0.0", "1.1.0")}
    assert result.revisions_written["identity"] == 1
    assert result.revisions_written["binding"] == 1, "the binding records the version too"


def test_a_new_commit_alone_does_not_write_a_revision(
    run: Callable[..., SurfaceWriteResult],
) -> None:
    """B1-CR-43, end to end.

    Fails when `src` re-enters the equality test; matters because `02` §4.3 row 1
    said the *whole* composite decides and §4.1 makes `src` the tree's commit sha
    -- so committing anything anywhere would rewrite every identity in the store
    on the next run, and F4 could never hold on a repository anybody was working
    in; no other instrument catches it because every fixture-level test passes
    `vcs_revision=None`.
    """
    run(vcs_revision="a" * 40)
    assert run(vcs_revision="b" * 40).revisions_written == _ZERO


# --------------------------------------------------------------------------- #
# S1.4: two extractors describing one referent.
#
# The table above was written when every referent had exactly one extractor. It
# does not cover the case S1.4 created, and that case broke F4 outright.
# --------------------------------------------------------------------------- #


def test_two_extractors_describing_one_endpoint_do_not_churn_the_chain(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """`02` §10 C1 and `01` F4 are in tension, and this is where they meet.

    *Fails when* one identity receives facts from two extractors and the writer
    treats them as two observations to append in turn.

    *Matters because* satisfying C1 is what creates the failure. `01` F2.3
    requires two extractors describing one referent to mint *"byte-identical
    URIs"*; S1.4 ships the first pair that genuinely does -- `web.django.routes`
    reads the route a service serves, `web.openapi` reads the contract it
    publishes about the same route -- and their attributes differ, so the chain
    **alternates between them forever**. Measured before the repair: six
    revisions in all three tables on *every* run after the first, on a tree
    nobody touched.

    *No other instrument catches it because* every earlier idempotence case has
    one extractor per referent, so the second observation it needs does not
    exist; the URI set, the fact count, the extractor attribution and the
    per-extractor determinism are all identical across runs, and the only visible
    symptom is a revision count nobody was comparing to zero.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, "tests")
    from adopt_extractors_common import pack as common_pack
    from adopt_extractors_web import pack as web_pack
    from adopt_map.orchestrator import run as run_map
    from adopt_map.plugins import DEFAULT_ENABLED_PACKS, ExtractorRegistry

    from build1_conftest import build_scoped_store, surface_writer_for

    work = tmp_path_factory.mktemp("reconcile")
    handle, scopes = build_scoped_store(work)
    registry = ExtractorRegistry(enabled_packs=DEFAULT_ENABLED_PACKS)
    registry.register_all(common_pack())
    registry.register_all(web_pack())
    writer = surface_writer_for(handle)
    fixture = Path("fixtures/repos/django-orders")

    try:
        first = run_map(
            resolved=scopes["prod"],
            root=fixture,
            registry=registry,
            adopt_version="test",
            writer=writer,
            out_dir=work / "out1",
            sequential=True,
        )
        # One fact per identity, which is the precondition rather than the point:
        # a duplicate here would make the zero below unreachable.
        uris = [entry.uri for entry in first.minted()]
        assert len(uris) == len(set(uris)), "one referent reached the writer twice"

        for index in (2, 3):
            again = run_map(
                resolved=scopes["prod"],
                root=fixture,
                registry=registry,
                adopt_version="test",
                writer=writer,
                out_dir=work / f"out{index}",
                sequential=True,
            )
            assert again.write_result is not None
            assert dict(again.write_result.revisions_written) == {
                "identity": 0,
                "knowledge": 0,
                "binding": 0,
            }, f"run {index} wrote revisions on an unchanged tree"
    finally:
        handle.close()


def test_the_merged_fact_carries_what_only_the_contract_artifact_saw(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Reconciliation **merges**; it does not pick a winner and discard the rest.

    *Fails when* the losing observation's fields are dropped.

    *Matters because* `02` §4.2's `endpoint` semantic projection names both
    *"request/response schema digest"* -- which only the OpenAPI document
    supplies -- and *"framework, handler symbol"* -- which only the route parse
    supplies. A "strongest evidence wins" rule would satisfy idempotence while
    silently deleting a field the contract asks the digest to cover.

    *No other instrument catches it because* the run stays idempotent either way,
    and a smaller attribute set still validates against a model whose fields are
    all optional.
    """
    import time
    from pathlib import Path

    from adopt_extractors_web import DjangoRoutesExtractor, OpenapiExtractor
    from adopt_map.context import Budget, ExtractorContext
    from adopt_map.fileindex import build_index
    from adopt_map.orchestrator import reconcile_batches
    from adopt_map.writer import FactBatch

    fixture = Path("fixtures/repos/django-orders")
    ctx = ExtractorContext(
        root=str(fixture),
        index=build_index(fixture),
        budget=Budget.starting_at(time.time(), stage1_s=900.0, total_s=3600.0),
        archetype="web",
        tier="T2",
    )
    import sys

    sys.path.insert(0, "tests")
    from build1_conftest import build_scoped_store

    handle, scopes = build_scoped_store(tmp_path_factory.mktemp("merge"))
    handle.close()

    django, openapi = DjangoRoutesExtractor(), OpenapiExtractor()
    batches = (
        FactBatch(manifest=django.manifest(), facts=tuple(django.extract(ctx))),
        FactBatch(manifest=openapi.manifest(), facts=tuple(openapi.extract(ctx))),
    )
    reconciled = reconcile_batches(batches, scopes["prod"])

    merged = [
        fact
        for batch in reconciled
        for fact in batch.facts
        if fact.attributes.get("framework") == "django"
        and fact.attributes.get("operation_name") is not None
    ]
    assert merged, "no endpoint carried both the route parse and the contract"
    sample = merged[0]
    assert sample.attributes["handler_symbol"] is not None, "the route parse's field survived"
    assert sample.attributes["status_codes"], "the contract's field was merged in"


def test_the_ai_pack_stays_idempotent_where_two_extractors_meet_one_symbol(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """S1.5's version of the case above, and the reason it is not redundant.

    *Fails when* `ai.graph` and `common.stub_tree` -- which both key a Python
    declaration `<module>.<name>` -- make the chain alternate on an unchanged
    tree.

    *Matters because* B1-CR-68's reconciliation was written against two *web*
    extractors, and the AI pack meets the same situation with a different pair
    and a different overlap: the graph extractor supplies `calls` **relations**
    where the tree extractor supplies a **signature**, so the merge has to union
    `relations` rather than only fill empty attributes. A merge that dropped one
    side's relations would still be idempotent; a merge that alternated would
    not, and only a second run tells them apart.

    *No other instrument catches it because* the per-extractor determinism cases
    pass either way -- each extractor is perfectly deterministic on its own --
    and the identity count is identical whichever observation wins.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, "tests")
    from adopt_extractors_ai import pack as ai_pack
    from adopt_extractors_common import pack as common_pack
    from adopt_map.orchestrator import run as run_map
    from adopt_map.plugins import ExtractorRegistry

    from build1_conftest import build_scoped_store, surface_writer_for

    work = tmp_path_factory.mktemp("ai-idem")
    handle, scopes = build_scoped_store(work, archetype="ai")
    registry = ExtractorRegistry(enabled_packs=frozenset({"common", "ai"}))
    registry.register_all(common_pack())
    registry.register_all(ai_pack())
    writer = surface_writer_for(handle)
    fixture = Path("fixtures/repos/langgraph-support")

    try:
        first = run_map(
            resolved=scopes["prod"],
            root=fixture,
            registry=registry,
            adopt_version="test",
            writer=writer,
            out_dir=work / "out1",
            sequential=True,
        )
        uris = [entry.uri for entry in first.minted()]
        assert len(uris) == len(set(uris)), "one referent reached the writer twice"

        # The merged symbol carries both halves: the declaration's signature and
        # the graph's edge. Asserted here rather than in a separate merge test,
        # because the point is that keeping both is what has to stay idempotent.
        merged = [
            entry
            for entry in first.minted()
            if entry.fact.identity_kind == "symbol" and entry.fact.relations
        ]
        assert merged, "no symbol carried a graph edge"
        assert any(entry.fact.attributes.get("signature") for entry in merged), (
            "the declaration's signature was discarded by the merge"
        )

        for index in (2, 3):
            again = run_map(
                resolved=scopes["prod"],
                root=fixture,
                registry=registry,
                adopt_version="test",
                writer=writer,
                out_dir=work / f"out{index}",
                sequential=True,
            )
            assert again.write_result is not None
            assert dict(again.write_result.revisions_written) == {
                "identity": 0,
                "knowledge": 0,
                "binding": 0,
            }, f"run {index} wrote revisions on an unchanged AI tree"
    finally:
        handle.close()
