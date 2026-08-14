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
