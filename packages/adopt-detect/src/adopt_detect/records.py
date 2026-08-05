"""The storage port the boundary writer runs through.

Declared here rather than imported from `adopt_store`, following CR-34 and the
precedent `adopt_scope.records` set: `no-raw-sqlite` names `adopt_detect` as a
source module and import-linter follows the chain, so a dependency on
`adopt_store` would reach `sqlite3` transitively and break the contract.

**The port writes a row; it does not decide what the row says.** The tier, the
plane locations, the permitted categories and the unavailable capabilities are
all `adopt_detect.boundary`'s -- computed from the archetype and the negotiated
answers before anything is written. Pushing any of that into a realization would
mean the boundary a store reported depended on which store answered, and the
boundary is the artifact a client signs.

**Ids and scope are the realization's**, exactly as contracts §10.3 requires of
every facade: there is no `id` parameter on `declare` through which a caller
could supply one.
"""

import datetime as _dt
from collections.abc import Sequence
from typing import Protocol

from adopt_model import ObservabilityBoundary
from adopt_model._enums import ControlPlane, KnowledgePlane, Tier
from adopt_scope import Scope

__all__ = ["BoundaryRecords"]


class BoundaryRecords(Protocol):
    """`observability_boundary`. No SQL, connection or cursor crosses this boundary."""

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
        """Write one boundary row and return it.

        A re-negotiation writes a **new** row rather than updating one. Nothing
        on this port updates, and that is deliberate: `observability_boundary` is
        what the engagement was told it may observe, and a boundary that could be
        edited in place cannot answer "what did we claim in March".
        """
        ...

    def current(
        self, *, system_id: str, environment_id: str | None = None
    ) -> ObservabilityBoundary | None:
        """The most recently declared boundary for the scope, or `None`.

        `environment_id` of `None` means *the boundary declared for the system as
        a whole* -- the column is nullable precisely so a boundary may span
        environments -- and never "the environment that happens to be null in the
        first row found".
        """
        ...
