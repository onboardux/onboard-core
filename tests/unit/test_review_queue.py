"""The review queue's guards, and the gap ranking that reads its results.

The queue holds dispositions, so its correctness is about *what may be recorded
and once*. Each test names the defect it catches.
"""

import pytest
from adopt_knowledge import Gap, rank_gaps

from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle


def _item(store: SqliteStoreHandle, scope: Scope, title: str = "Doc") -> str:
    item_id, _ = store.items().record(
        scope=scope,
        kind="procedure",
        title=title,
        body_md="body",
        authority_class="artifact_observed",
        verification="verified",
    )
    return item_id


@pytest.mark.unit
class TestTheQueueGuards:
    def test_an_item_is_resolved_once(self, s4_store: SqliteStoreHandle, s4_scope: Scope) -> None:
        """*Fails when* a second disposition is accepted. *Matters because*
        confirming twice binds twice, and rejecting something already confirmed
        leaves a binding whose review says it was rejected -- a store that
        contradicts its own audit trail. *No other instrument catches it
        because* each write is individually valid."""
        assert s4_scope.system is not None
        _, item_ids = s4_store.governance().open_batch(
            system_id=str(s4_scope.system.id),
            batch_key="ingest:1-of-1",
            items=[(_item(s4_store, s4_scope), None)],
        )
        s4_store.governance().resolve(review_item_id=item_ids[0], resolution="confirmed")

        with pytest.raises(AdoptError) as raised:
            s4_store.governance().resolve(review_item_id=item_ids[0], resolution="rejected")

        assert raised.value.code is ErrorCode.REVIEW_ITEM_RESOLVED

    def test_an_unknown_item_is_refused_with_its_own_code(
        self, s4_store: SqliteStoreHandle
    ) -> None:
        """ "There is no such item" and "that was already decided" are different
        sentences sending an operator to different places (CR-38's precedent)."""
        with pytest.raises(AdoptError) as raised:
            s4_store.governance().resolve(review_item_id="ri_nope", resolution="confirmed")

        assert raised.value.code is ErrorCode.REVIEW_ITEM_NOT_FOUND

    def test_a_batch_closes_only_when_every_item_is_resolved(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* a batch closes early. *Matters because* a closed batch
        leaves the queue, so its unresolved items would become invisible work.
        *No other instrument catches it because* the items keep their own
        correct state."""
        assert s4_scope.system is not None
        batch_id, item_ids = s4_store.governance().open_batch(
            system_id=str(s4_scope.system.id),
            batch_key="ingest:2-of-2",
            items=[(_item(s4_store, s4_scope, "A"), None), (_item(s4_store, s4_scope, "B"), None)],
        )

        s4_store.governance().resolve(review_item_id=item_ids[0], resolution="confirmed")
        batch = s4_store.governance().get_batch(batch_id)
        assert batch is not None
        assert batch.resolution is None
        assert batch.resolved_at is None

        s4_store.governance().resolve(review_item_id=item_ids[1], resolution="confirmed")
        closed = s4_store.governance().get_batch(batch_id)
        assert closed is not None
        assert closed.resolution == "confirmed"

    def test_a_mixed_batch_does_not_close_as_confirmed(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* a batch summarises itself by its last item. *Matters
        because* "confirmed" on a session where half the items were rejected is
        the review-queue equivalent of a green build with a failing test in it,
        and Build 8 reads these rows to measure review outcomes. *No other
        instrument catches it because* every item's own resolution is right."""
        assert s4_scope.system is not None
        batch_id, item_ids = s4_store.governance().open_batch(
            system_id=str(s4_scope.system.id),
            batch_key="ingest:2-of-2",
            items=[(_item(s4_store, s4_scope, "A"), None), (_item(s4_store, s4_scope, "B"), None)],
        )

        s4_store.governance().resolve(review_item_id=item_ids[0], resolution="rejected")
        s4_store.governance().resolve(review_item_id=item_ids[1], resolution="confirmed")

        batch = s4_store.governance().get_batch(batch_id)
        assert batch is not None
        assert batch.resolution == "corrected"

    def test_an_empty_batch_is_refused_as_a_caller_mistake(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """An empty batch opens, shows a reviewer nothing and can never be
        resolved. Refused as a programming error rather than with an error code,
        because no operator input can produce it."""
        assert s4_scope.system is not None
        with pytest.raises(ValueError, match="at least one item"):
            s4_store.governance().open_batch(
                system_id=str(s4_scope.system.id), batch_key="empty", items=[]
            )


class _Entry:
    """A coverage verdict, structurally -- `rank_gaps` takes the protocol."""

    def __init__(self, uri: str, covered: bool, reasons: tuple[str, ...]) -> None:
        self.identity_id = f"idn_{uri[-6:]}"
        self.uri = uri
        self.covered = covered
        self.reasons = reasons


@pytest.mark.unit
class TestGapRanking:
    def test_covered_identities_are_not_listed(self) -> None:
        """The report is the elicitation queue; work already done is not work."""
        entries = [
            _Entry("onboard-v1://acme/platform/api/prod/endpoint/-/one", True, ()),
            _Entry(
                "onboard-v1://acme/platform/api/prod/endpoint/-/two", False, ("no_live_binding",)
            ),
        ]

        assert [gap.uri.rsplit("/", 1)[-1] for gap in rank_gaps(entries)] == ["two"]

    def test_the_worst_gaps_come_first_and_ties_are_stable(self) -> None:
        """*Fails when* ranking becomes input order. *Matters because* an FDE
        works down this list across days, and a list that reshuffles between
        runs cannot be worked down at all. *No other instrument catches it
        because* every ordering contains the same rows."""
        entries = [
            _Entry(
                "onboard-v1://acme/platform/api/prod/endpoint/-/zebra", False, ("no_live_binding",)
            ),
            _Entry(
                "onboard-v1://acme/platform/api/prod/config_key/-/alpha",
                False,
                ("no_live_binding", "no_observability_boundary"),
            ),
            _Entry(
                "onboard-v1://acme/platform/api/prod/endpoint/-/apple", False, ("no_live_binding",)
            ),
        ]

        ranked = rank_gaps(entries)

        assert [gap.uri.rsplit("/", 1)[-1] for gap in ranked] == ["alpha", "apple", "zebra"]
        assert rank_gaps(entries) == ranked
        assert rank_gaps(list(reversed(entries))) == ranked

    def test_a_gap_carries_its_kind(self) -> None:
        """The kind is what makes the report scannable; a URI alone is not."""
        ranked = rank_gaps(
            [_Entry("onboard-v1://acme/platform/api/prod/job/-/nightly", False, ("x",))]
        )

        assert ranked[0].kind == "job"
        assert isinstance(ranked[0], Gap)
