"""`recompute_coverage` -- one row per input of contracts §6, each toggled alone.

*Fails when* any of the six inputs stops being consulted, or when one of them
starts deciding on its own. *Matters because* `recompute_coverage` is **the
authority**: `identity.covered_cache` is derived from it and every downstream gap
report reads it, so an input silently dropped here becomes a coverage claim
nobody can check. *No other instrument catches it because* the equivalence
property proves the function agrees with a reference implementation without
proving either consults the boundary, and the CUJ proves one journey rather than
six independent conditions.

The table is built by taking a **fully covered** world and breaking exactly one
input per row. A row that broke two would pass while the function ignored one of
them.
"""

import datetime as _dt
import io
import json
from collections.abc import Callable

import pytest

from adopt_const import COVERAGE_ALARM_SAMPLE_MAX
from adopt_coverage import (
    REASON_AUDIENCE_OR_ENVIRONMENT,
    REASON_IDENTITY_NOT_ACTIVE,
    REASON_NO_ACTIVE_KNOWLEDGE_REVISION,
    REASON_NO_LIVE_BINDING,
    REASON_NO_OBSERVABILITY_BOUNDARY,
    REASON_NOT_VERIFIED,
    REASON_VERIFICATION_CONFLICTED,
    rebuild_cache,
    recompute_coverage,
)
from adopt_obs import LogLevel, ManualClock, set_sink
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft, doctor
from adopt_store.api import SqliteStoreHandle


def _covered_world(
    store: SqliteStoreHandle,
    scope: Scope,
    add_boundary: Callable[..., str],
    add_audience: Callable[..., None],
    *,
    verification: str | None = "verified",
) -> tuple[str, str, str]:
    """A world in which the one identity is covered on all six counts.

    Returns `(identity_id, item_id, binding_id)`.
    """
    assert scope.system is not None
    identity = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )
    item_id, _ = store.items().create(
        scope=scope,
        kind="answer",
        title="How a refund is issued",
        revision=KnowledgeRevisionDraft(
            authority_class="human_confirmed",
            body_md="v1",
            verification=verification,  # type: ignore[arg-type]
        ),
    )
    add_audience(item_id=item_id)
    binding_id, _ = store.bindings().create(
        item_id=item_id,
        identity_id=identity.id,
        is_load_bearing=True,
        revision=BindingRevisionDraft(status="active", locator_rung=1),
    )
    add_boundary(system_id=scope.system.id)
    return identity.id, item_id, binding_id


@pytest.mark.unit
class TestTheSixInputs:
    def test_a_world_satisfying_all_six_is_covered(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        """The control. Without it every negative row below could pass because
        the world was never coverable in the first place."""
        assert s4_scope.system is not None
        identity_id, _, _ = _covered_world(s4_store, s4_scope, add_boundary, add_audience)

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.covered == 1
        assert result.uncovered == 0
        assert result.identities[0].reasons == ()
        assert result.verdict(identity_id) is True

    def test_input_1_a_dead_identity_revision_is_not_covered(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        assert s4_scope.system is not None
        identity_id, _, _ = _covered_world(s4_store, s4_scope, add_boundary, add_audience)
        s4_store.identities().retire(identity_id=identity_id, reason="referent removed")

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity_id) is False
        assert REASON_IDENTITY_NOT_ACTIVE in result.identities[0].reasons

    def test_input_2_a_retired_binding_is_not_a_live_binding(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        assert s4_scope.system is not None
        identity_id, _, binding_id = _covered_world(s4_store, s4_scope, add_boundary, add_audience)
        s4_store.bindings().retire(binding_id=binding_id, reason="no longer describes it")

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity_id) is False
        assert REASON_NO_LIVE_BINDING in result.identities[0].reasons

    def test_input_2_an_identity_with_no_binding_at_all_is_not_covered(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
    ) -> None:
        """The `UNBOUND-NEW` case the cache alarm exists to keep visible."""
        assert s4_scope.system is not None
        identity = s4_store.identities().observe(
            scope=s4_scope, kind="endpoint", namespace=None, key="GET /v1/orders"
        )
        add_boundary(system_id=s4_scope.system.id)

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity.id) is False
        assert REASON_NO_LIVE_BINDING in result.identities[0].reasons

    def test_input_3_a_retired_item_has_no_active_knowledge_revision(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        """Knowledge carries its terminal state on the parent (contracts §5
        obligation 4), so this is where "the revision is not active" is read."""
        assert s4_scope.system is not None
        identity_id, item_id, _ = _covered_world(s4_store, s4_scope, add_boundary, add_audience)
        s4_store.revisions().retire(parent_id=item_id, reason="superseded by policy")

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity_id) is False
        assert REASON_NO_ACTIVE_KNOWLEDGE_REVISION in result.identities[0].reasons

    def test_input_4_an_item_with_no_audience_tag_is_not_covered(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
    ) -> None:
        assert s4_scope.system is not None
        identity = s4_store.identities().observe(
            scope=s4_scope, kind="endpoint", namespace=None, key="POST /v1/orders"
        )
        item_id, _ = s4_store.items().create(
            scope=s4_scope,
            kind="answer",
            title="Untagged",
            revision=KnowledgeRevisionDraft(authority_class="human_confirmed", body_md="v1"),
        )
        s4_store.bindings().create(
            item_id=item_id,
            identity_id=identity.id,
            is_load_bearing=True,
            revision=BindingRevisionDraft(status="active", locator_rung=1),
        )
        add_boundary(system_id=s4_scope.system.id)

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity.id) is False
        assert REASON_AUDIENCE_OR_ENVIRONMENT in result.identities[0].reasons

    def test_input_5_a_scope_with_no_boundary_is_not_covered(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        """Nothing has declared what may be observed here, so coverage would be
        a claim about a system nobody agreed to look at."""
        assert s4_scope.system is not None
        identity = s4_store.identities().observe(
            scope=s4_scope, kind="endpoint", namespace=None, key="POST /v1/orders"
        )
        item_id, _ = s4_store.items().create(
            scope=s4_scope,
            kind="answer",
            title="How a refund is issued",
            revision=KnowledgeRevisionDraft(authority_class="human_confirmed", body_md="v1"),
        )
        add_audience(item_id=item_id)
        s4_store.bindings().create(
            item_id=item_id,
            identity_id=identity.id,
            is_load_bearing=True,
            revision=BindingRevisionDraft(status="active", locator_rung=1),
        )

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity.id) is False
        assert REASON_NO_OBSERVABILITY_BOUNDARY in result.identities[0].reasons

    def test_input_5_a_system_wide_boundary_governs_every_environment(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        """A boundary with a NULL environment is the system-wide declaration.
        Reading it as "applies to no environment" would leave every system-wide
        declaration covering nothing."""
        assert s4_scope.system is not None
        identity_id, _, _ = _covered_world(s4_store, s4_scope, add_boundary, add_audience)

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity_id) is True

    def test_input_5_a_boundary_for_another_environment_does_not_govern_this_one(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        assert s4_scope.system is not None and s4_scope.environment is not None
        other = s4_store.scope().create_environment(
            system_id=s4_scope.system.id, slug="staging", name="Staging"
        )
        identity = s4_store.identities().observe(
            scope=s4_scope, kind="endpoint", namespace=None, key="POST /v1/orders"
        )
        item_id, _ = s4_store.items().create(
            scope=s4_scope,
            kind="answer",
            title="How a refund is issued",
            revision=KnowledgeRevisionDraft(authority_class="human_confirmed", body_md="v1"),
        )
        add_audience(item_id=item_id)
        s4_store.bindings().create(
            item_id=item_id,
            identity_id=identity.id,
            is_load_bearing=True,
            revision=BindingRevisionDraft(status="active", locator_rung=1),
        )
        add_boundary(system_id=s4_scope.system.id, environment_id=other.id)

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity.id) is False
        assert REASON_NO_OBSERVABILITY_BOUNDARY in result.identities[0].reasons

    def test_input_6_a_conflicted_verification_blocks_coverage(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        """Bet 4 working as designed: intent and reality disagree, the
        disagreement is representable, and nothing claims to be covered while it
        stands."""
        assert s4_scope.system is not None
        identity_id, _, _ = _covered_world(
            s4_store, s4_scope, add_boundary, add_audience, verification="conflicted"
        )

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity_id) is False
        assert REASON_VERIFICATION_CONFLICTED in result.identities[0].reasons

    def test_input_6_unverified_blocks_coverage(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        """Only `verified` knowledge counts (v6.1 §6 B2 / F6, Build 2 D5).

        **This assertion is the inverse of the one Build 0 shipped**, and the
        inversion is the point rather than a correction. Build 0's rule was
        right for Build 0: nothing could make an item verified, so requiring
        verification would have made coverage unreachable by construction. Build
        2 supplies both doors -- ingest writes a human's document as `verified`,
        and confirming in `adopt review` promotes a mined candidate -- so the
        rule v6.1 requires became enforceable in the same build that made it
        satisfiable.

        Fails when an unreviewed harvest candidate is allowed to carry coverage;
        matters because that is precisely how `adopt gaps` stops asking for the
        knowledge a system is actually missing; no other instrument catches it
        because the store is perfectly consistent either way -- the identity has
        a live binding to a real item, and only this rule distinguishes a
        machine's guess from something a person stood behind.
        """
        assert s4_scope.system is not None
        identity_id, _, _ = _covered_world(
            s4_store, s4_scope, add_boundary, add_audience, verification="unverified"
        )

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity_id) is False
        assert REASON_NOT_VERIFIED in result.identities[0].reasons

    def test_input_6_a_missing_verification_blocks_exactly_as_unverified_does(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        """A NULL `verification` is not permission.

        Fails when absence is read as assent; matters because `verification` is
        nullable and every writer that simply omits it would otherwise
        manufacture coverage; no other instrument catches it because the column
        is legitimately empty on rows no one has reviewed.
        """
        assert s4_scope.system is not None
        identity_id, _, _ = _covered_world(
            s4_store, s4_scope, add_boundary, add_audience, verification=None
        )

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity_id) is False
        assert REASON_NOT_VERIFIED in result.identities[0].reasons

    def test_a_second_live_binding_carries_coverage_when_the_first_cannot(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        """Contracts §6 asks for *at least one* non-retired binding, not for all
        of them. A conjunction over every binding would make an identity lose
        coverage the moment any one of its items was retired."""
        assert s4_scope.system is not None
        identity_id, _, binding_id = _covered_world(s4_store, s4_scope, add_boundary, add_audience)
        second_item, _ = s4_store.items().create(
            scope=s4_scope,
            kind="procedure",
            title="How a refund is reversed",
            revision=KnowledgeRevisionDraft(
                authority_class="human_confirmed", body_md="v1", verification="verified"
            ),
        )
        add_audience(item_id=second_item)
        s4_store.bindings().create(
            item_id=second_item,
            identity_id=identity_id,
            is_load_bearing=False,
            revision=BindingRevisionDraft(status="active", locator_rung=1),
        )
        s4_store.bindings().retire(binding_id=binding_id, reason="superseded")

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity_id) is True


@pytest.mark.unit
class TestTheCacheAlarms:
    """PRD F7.3: a disagreement is a defect signal and must alarm.

    *Fails when* the cache drifts and nothing says so, or when looking at the
    drift repairs it. *Matters because* a quietly self-healing cache reintroduces
    exactly the invisible coverage decay this rebuild exists to delete. *No other
    instrument catches it because* the six-input table proves what coverage *is*
    without ever comparing it to what was cached.
    """

    def test_an_injected_stale_cache_row_produces_a_disagreement(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
        inject_cache: Callable[..., None],
    ) -> None:
        assert s4_scope.system is not None
        identity_id, _, _ = _covered_world(s4_store, s4_scope, add_boundary, add_audience)
        inject_cache(identity_id=identity_id, covered=False)

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert len(result.disagreements) == 1
        disagreement = result.disagreements[0]
        assert disagreement.identity_id == identity_id
        assert disagreement.cached is False
        assert disagreement.recomputed is True

    def test_the_disagreement_is_emitted_at_alarm_level(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
        inject_cache: Callable[..., None],
    ) -> None:
        """`LogLevel.ALARM`, not `ERROR`: a defect signal that must page.

        The URI is deliberately **absent** from the line. Identity ids are minted
        by us and carry no client-derived text; a URI carries the referent's own
        name.
        """
        assert s4_scope.system is not None
        identity_id, _, _ = _covered_world(s4_store, s4_scope, add_boundary, add_audience)
        inject_cache(identity_id=identity_id, covered=True)
        s4_store.bindings().retire(
            binding_id=s4_store.bindings().for_identity(identity_id)[0].id, reason="gone"
        )

        sink = io.StringIO()
        set_sink(sink, min_level=LogLevel.DEBUG)
        try:
            recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)
        finally:
            set_sink(io.StringIO())

        line = sink.getvalue()
        assert '"level": "alarm"' in line
        assert '"event": "coverage_cache_disagreement"' in line
        assert "COVERAGE_CACHE_DISAGREEMENT" in line
        assert identity_id in line
        assert "onboard-v1://" not in line

    def test_recompute_does_not_write_the_cache(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
        inject_cache: Callable[..., None],
    ) -> None:
        """Computing and writing are two calls. If recompute wrote, `doctor`
        could not report a disagreement without destroying its own evidence."""
        assert s4_scope.system is not None
        identity_id, _, _ = _covered_world(s4_store, s4_scope, add_boundary, add_audience)
        inject_cache(identity_id=identity_id, covered=False)

        recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        still = s4_store.identities().get(identity_id)
        assert still is not None
        assert still.covered_cache is False, "the recompute repaired the cache it should report"

    def test_doctor_reports_the_disagreement_and_leaves_it_standing(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
        inject_cache: Callable[..., None],
    ) -> None:
        """*The* S4 assertion. Implementation spec §8: rebuilding the cache first
        destroys the evidence, and the writer that drifted it is never found."""
        assert s4_scope.system is not None
        identity_id, _, _ = _covered_world(s4_store, s4_scope, add_boundary, add_audience)
        inject_cache(identity_id=identity_id, covered=False)

        findings = doctor(s4_store)

        assert [f for f in findings if f.subject_id == identity_id], findings
        assert findings[0].code == "COVERAGE_CACHE_DISAGREEMENT"
        after = s4_store.identities().get(identity_id)
        assert after is not None
        assert after.covered_cache is False, "doctor repaired instead of reporting"

    def test_rebuild_cache_writes_the_recomputed_value_and_clears_the_alarm(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
        inject_cache: Callable[..., None],
    ) -> None:
        """The direction that is allowed: result -> cache, never cache -> result."""
        assert s4_scope.system is not None
        identity_id, _, _ = _covered_world(s4_store, s4_scope, add_boundary, add_audience)
        inject_cache(identity_id=identity_id, covered=False)
        records = s4_store.coverage_records()

        written = rebuild_cache(
            s4_store.backend, recompute_coverage(records, s4_scope.system.id, clock=s4_clock)
        )

        assert written == 1
        after = s4_store.identities().get(identity_id)
        assert after is not None
        assert after.covered_cache is True
        assert after.covered_cache_at is not None
        assert recompute_coverage(records, s4_scope.system.id).disagreements == ()

    def test_the_alarm_samples_identity_ids_rather_than_listing_all_of_them(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        inject_cache: Callable[..., None],
    ) -> None:
        """*Fails when* the alarm's `identity_ids` field grows with the store.

        A cold cache over a 50k-identity store disagrees on every row. An
        uncapped field puts a megabyte of ULIDs on one line, which is how an
        alarm takes down the sink that was meant to carry it. The **count stays
        complete** and `store doctor` enumerates every affected identity, so the
        cap costs nothing an operator needs.
        """
        assert s4_scope.system is not None
        add_boundary(system_id=s4_scope.system.id)
        over_cap = COVERAGE_ALARM_SAMPLE_MAX + 3
        for index in range(over_cap):
            identity = s4_store.identities().observe(
                scope=s4_scope, kind="endpoint", namespace=None, key=f"GET /v1/r{index}"
            )
            # Every one is uncovered (no binding) but cached as covered.
            inject_cache(identity_id=identity.id, covered=True)

        sink = io.StringIO()
        set_sink(sink, min_level=LogLevel.DEBUG)
        try:
            result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)
        finally:
            set_sink(io.StringIO())

        line = json.loads(sink.getvalue().strip())
        assert len(result.disagreements) == over_cap
        assert line["disagreement_count"] == over_cap
        assert len(line["identity_ids"]) == COVERAGE_ALARM_SAMPLE_MAX
        assert line["identity_ids_truncated"] is True

    def test_the_cache_stamp_records_when_the_recompute_ran(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        """`covered_cache_at` is written for **every** identity in scope, not
        only the disagreeing ones -- otherwise it would claim a confirmation time
        for rows nobody re-confirmed."""
        assert s4_scope.system is not None
        _covered_world(s4_store, s4_scope, add_boundary, add_audience)
        s4_clock.advance(_dt.timedelta(hours=3))
        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id, clock=s4_clock)

        rebuild_cache(s4_store.backend, result)

        stored = s4_store.identities().get(result.identities[0].identity_id)
        assert stored is not None
        assert stored.covered_cache_at == result.computed_at
