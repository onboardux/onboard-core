"""`BoundaryFacade` -- `Store.boundary()`, contracts §10.3.

**Declaring a boundary appends; it never updates.** `observability_boundary` is
the record of what an engagement was told we may observe, and later builds put a
client signature against it (design docs v4 §X7, whose whole invariant is that a
compiled policy may never be more permissive than the signed statement). A row
that could be edited in place cannot answer *what did we claim in March*, so
re-negotiation writes a new row and `current()` reads the latest.

**This facade records a decision; it does not make one.** Which tier the answers
imply, which capabilities are unavailable and which outbound categories are
permitted are all computed in `adopt_detect` before anything arrives here. A
store that decided any of it would make the boundary a property of the
realization that answered -- and the Postgres realization would then have to
reproduce the judgement, unwritten and untested.
"""

import datetime as _dt
from collections.abc import Sequence

from adopt_model import ObservabilityBoundary
from adopt_model._enums import ControlPlane, KnowledgePlane, Tier
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, new_id, truncate_to_millisecond
from adopt_scope import Scope
from adopt_store.facades.records import ObservabilityBoundaryRecords

__all__ = ["BOUNDARY_ID_PREFIX", "BoundaryFacade"]

#: Registered in `adopt_obs.ids`; `new_id` refuses anything else.
BOUNDARY_ID_PREFIX = "ob"


class BoundaryFacade:
    """`Store.boundary()` -- contracts §10.3."""

    def __init__(
        self, records: ObservabilityBoundaryRecords, *, clock: Clock | None = None
    ) -> None:
        self._records = records
        self._clock: Clock = clock if clock is not None else SystemClock()

    def _now(self) -> _dt.datetime:
        return truncate_to_millisecond(self._clock.now())

    def declare(
        self,
        *,
        scope: Scope,
        tier: Tier,
        knowledge_plane_location: KnowledgePlane,
        control_plane_location: ControlPlane,
        permitted_outbound_categories: Sequence[str],
        covered: str | None = None,
        not_covered: str | None = None,
        last_successful_observation_at: _dt.datetime | None = None,
        safe_probe_status: str | None = None,
        owner_actor_id: str | None = None,
        contractual_approval_ref: str | None = None,
        contractual: bool = False,
    ) -> ObservabilityBoundary:
        """Write one boundary row for a system, optionally narrowed to an environment.

        Args:
            scope: Must resolve at least to a system; `system_id` is `NOT NULL`.
                An environment is optional, because a boundary may legitimately
                span every environment of a system.
            tier: The negotiated tier. Deterministic, and computed upstream.
            knowledge_plane_location: Where client knowledge lives.
            control_plane_location: Where routing and entitlement live.
            permitted_outbound_categories: The **authority** for what may leave.
                Contracts §8 rule 3 makes this list, not a caller's declaration,
                what an outbound envelope is validated against.
            covered: Human-readable summary of what is observable.
            not_covered: Human-readable summary of what is not.
            last_successful_observation_at: Last time observation actually worked.
            safe_probe_status: Whether a safe execution path exists, in the
                vocabulary the probe layer will use; free text at Build 0
                because item 8 owns probe execution.
            owner_actor_id: Who is accountable for the boundary.
            contractual_approval_ref: The contract amendment permitting anything
                beyond `metadata_only`. Contracts §8 rule 4: changing what may
                leave is an amendment, not a settings toggle.
            contractual: Whether this boundary has been contractually agreed.

        Returns:
            The stored row.

        Raises:
            AdoptError: ``SCOPE_VIOLATION`` when the scope resolves no system.
        """
        if scope.system is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message="an observability boundary needs a system",
                hint="Resolve the scope to at least `firm/engagement/system`. A boundary "
                "that did not say which system it bounds would hard-limit every "
                "downstream claim about a system nobody named.",
            )

        row = ObservabilityBoundary(
            id=new_id(BOUNDARY_ID_PREFIX),
            system_id=scope.system.id,
            environment_id=None if scope.environment is None else scope.environment.id,
            tier=tier,
            covered=covered,
            not_covered=not_covered,
            knowledge_plane_location=knowledge_plane_location,
            control_plane_location=control_plane_location,
            permitted_outbound_categories=list(permitted_outbound_categories),
            last_successful_observation_at=last_successful_observation_at,
            safe_probe_status=safe_probe_status,
            owner_actor_id=owner_actor_id,
            contractual_approval_ref=contractual_approval_ref,
            declared_at=self._now(),
            contractual=contractual,
        )
        with self._records.transaction():
            self._records.insert_boundary(row)
        return row

    def current(
        self, *, system_id: str, environment_id: str | None = None
    ) -> ObservabilityBoundary | None:
        """The most recently declared boundary for the scope, or `None`."""
        return self._records.latest_boundary(system_id=system_id, environment_id=environment_id)
