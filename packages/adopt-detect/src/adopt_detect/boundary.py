"""The observability boundary: one row, two renderings, one authority.

PRD F10.5-F10.8 and contracts §3. The boundary is the object that **hard-limits
every downstream claim**: what may be observed, where knowledge lives, and what
may leave. Three things about it are load-bearing.

**`BoundaryView` is the accessor every downstream item calls** (`05` S6). It is a
read-only projection of one stored row, and it is the type contracts §8 names in
`validate_envelope(env, boundary: BoundaryView)`. Its one home is here, so that
"what may leave" has one answer rather than one per caller.

**The boundary is the authority, never the caller's declaration** (contracts §8
rule 3, PRD F12.5). `permitted_outbound_categories` lives on the row precisely so
that widening it is a write to a client-signed artefact rather than a
configuration change -- and `contractual_approval_ref` is what records the
amendment that permitted it.

**Both rendered artifacts come from one row** (F10.8). `render.py` takes a
`BoundaryView` and nothing else; there is no path by which the human-readable
statement could say something the machine-readable one does not. Two renderers
reading two sources is how a client is shown a document that the system does not
enforce.
"""

import datetime as _dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from adopt_detect.negotiate import TierDecision, violates_archetype_floor
from adopt_detect.records import BoundaryRecords
from adopt_model import ObservabilityBoundary
from adopt_model._enums import Archetype, ControlPlane, KnowledgePlane, Tier
from adopt_scope import Scope

__all__ = [
    "DEFAULT_OUTBOUND_CATEGORIES",
    "METADATA_ONLY",
    "BoundaryView",
    "declare_boundary",
]

#: Contracts §8 rule 1 and the `observability_boundary` column default. The one
#: policy that needs no contract amendment, and the value everything falls back
#: to when nothing else was agreed.
METADATA_ONLY: Final[str] = "metadata_only"

#: PRD F10.5. The default is a *list* because the column is a JSON list, and it
#: is a one-element list rather than an empty one because an empty permitted set
#: would make every outbound envelope invalid -- including the metadata-only ones
#: the product is built to send.
DEFAULT_OUTBOUND_CATEGORIES: Final[tuple[str, ...]] = (METADATA_ONLY,)

#: Local-first is the default posture: client knowledge stays with the customer
#: and only routing lives with the vendor. Both are overridable per engagement,
#: and both are recorded on the row rather than inferred at read time.
DEFAULT_KNOWLEDGE_PLANE: Final[KnowledgePlane] = "customer"
DEFAULT_CONTROL_PLANE: Final[ControlPlane] = "vendor"


@dataclass(frozen=True, slots=True)
class BoundaryView:
    """A read-only projection of one `observability_boundary` row.

    Contracts §8's `validate_envelope` second parameter. Frozen, because a
    validator that could widen the boundary it is validating against is not a
    validator.
    """

    boundary_id: str
    system_id: str
    environment_id: str | None
    tier: Tier
    archetype: Archetype | None
    knowledge_plane_location: KnowledgePlane
    control_plane_location: ControlPlane
    permitted_outbound_categories: tuple[str, ...]
    unavailable_capabilities: tuple[str, ...]
    contractual_approval_ref: str | None
    declared_at: _dt.datetime
    decline_recommended: bool
    archetype_floor_violated: bool
    last_successful_observation_at: _dt.datetime | None = None
    safe_probe_status: str | None = None

    def permits(self, content_policy: str) -> bool:
        """Whether this boundary permits a policy. **The authority.**"""
        return content_policy in self.permitted_outbound_categories

    @classmethod
    def of(
        cls,
        row: ObservabilityBoundary,
        *,
        archetype: Archetype | None,
        unavailable_capabilities: Sequence[str] = (),
        decline_recommended: bool = False,
        archetype_floor_violated: bool = False,
    ) -> "BoundaryView":
        """Project a stored row.

        `archetype` is carried alongside rather than read from the row because
        `observability_boundary` has no archetype column -- it is `system`'s, and
        contracts §3 puts it there. Passing it explicitly keeps the view honest
        about where each field came from instead of implying a column that does
        not exist.
        """
        categories = row.permitted_outbound_categories
        return cls(
            boundary_id=row.id,
            system_id=row.system_id,
            environment_id=row.environment_id,
            tier=row.tier,
            archetype=archetype,
            knowledge_plane_location=row.knowledge_plane_location,
            control_plane_location=row.control_plane_location,
            permitted_outbound_categories=tuple(categories) if isinstance(categories, list) else (),
            unavailable_capabilities=tuple(unavailable_capabilities),
            contractual_approval_ref=row.contractual_approval_ref,
            declared_at=row.declared_at,
            decline_recommended=decline_recommended,
            archetype_floor_violated=archetype_floor_violated,
            last_successful_observation_at=row.last_successful_observation_at,
            safe_probe_status=row.safe_probe_status,
        )


def declare_boundary(
    records: BoundaryRecords,
    *,
    scope: Scope,
    decision: TierDecision,
    archetype: Archetype | None,
    knowledge_plane_location: KnowledgePlane = DEFAULT_KNOWLEDGE_PLANE,
    control_plane_location: ControlPlane = DEFAULT_CONTROL_PLANE,
    permitted_outbound_categories: Sequence[str] = DEFAULT_OUTBOUND_CATEGORIES,
    contractual_approval_ref: str | None = None,
    owner_actor_id: str | None = None,
    safe_probe_status: str | None = None,
) -> BoundaryView:
    """Write the boundary a negotiation implies and return its view.

    Args:
        records: The storage port. Writes the row; decides nothing.
        scope: Must resolve at least to a system.
        decision: The negotiated tier, from `negotiate`.
        archetype: What detection concluded, or `None` when it was ambiguous.
        knowledge_plane_location: Where client knowledge lives.
        control_plane_location: Where routing and entitlement live.
        permitted_outbound_categories: What may leave. Defaults to
            `["metadata_only"]`; anything wider needs `contractual_approval_ref`.
        contractual_approval_ref: The contract amendment reference.
        owner_actor_id: Who is accountable.
        safe_probe_status: Whether a safe execution path exists.

    Returns:
        The `BoundaryView` for the row just written.

    Raises:
        AdoptError: ``SCOPE_VIOLATION`` from the facade when the scope resolves
            no system.

    Note:
        **A `T0` decision still writes a boundary.** The decline recommendation
        is a *finding about* the engagement, not a refusal to record what was
        negotiated -- and an engagement that was declined is exactly the one
        whose boundary someone will want to read six months later.
    """
    categories = tuple(permitted_outbound_categories)
    row = records.declare(
        scope=scope,
        tier=decision.tier,
        knowledge_plane_location=knowledge_plane_location,
        control_plane_location=control_plane_location,
        permitted_outbound_categories=categories,
        covered=decision.rationale,
        not_covered=_not_covered(decision.unavailable),
        safe_probe_status=safe_probe_status,
        owner_actor_id=owner_actor_id,
        contractual_approval_ref=contractual_approval_ref,
        contractual=contractual_approval_ref is not None,
    )
    return BoundaryView.of(
        row,
        archetype=archetype,
        unavailable_capabilities=decision.unavailable,
        decline_recommended=decision.decline_recommended,
        archetype_floor_violated=violates_archetype_floor(archetype, decision.tier),
    )


def _not_covered(unavailable: Sequence[str]) -> str:
    """The human-readable summary of what this tier does not grant."""
    if not unavailable:
        return "Nothing: every capability the platform offers is available at this tier."
    return "Not available at this tier: " + ", ".join(unavailable) + "."
