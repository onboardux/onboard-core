"""The two boundary artifacts, rendered from one row.

PRD F10.8: the boundary is emitted as both machine-readable and human-readable
artifacts, **rendered from one row, so they cannot disagree.**

That is a structural claim, and this module is where it is kept: both functions
take a `BoundaryView` and nothing else. Neither reads a store, a config file or
a second source. The test that matters asserts the two agree field by field --
and it can only be written because there is one input to compare against.

**Why it matters more here than elsewhere.** The human-readable statement is what
a client reads and, in later builds, signs. The machine-readable one is what the
system enforces. A product where those two can drift is one that shows a customer
a promise it does not keep, and no amount of care at the call site fixes a design
that permits it.
"""

from typing import Any, Final

from adopt_detect.boundary import BoundaryView
from adopt_obs import format_timestamp

__all__ = ["render_json", "render_markdown"]

_ABSENT: Final[str] = "not recorded"


def render_json(view: BoundaryView) -> dict[str, Any]:
    """The machine-readable artifact -- contracts §14's `adopt boundary` payload.

    Keys are exactly §14's, plus the two the tier ladder makes observable:
    `archetype` (carried from detection) and `decline_recommended` (`T0`). Both
    are additive to the documented shape, which §14's stability rule permits.
    """
    return {
        "boundary_id": view.boundary_id,
        "tier": view.tier,
        "archetype": view.archetype,
        "knowledge_plane_location": view.knowledge_plane_location,
        "control_plane_location": view.control_plane_location,
        "permitted_outbound_categories": list(view.permitted_outbound_categories),
        "unavailable_capabilities": list(view.unavailable_capabilities),
        "decline_recommended": view.decline_recommended,
        "archetype_floor_violated": view.archetype_floor_violated,
        "contractual_approval_ref": view.contractual_approval_ref,
        "declared_at": format_timestamp(view.declared_at),
    }


def render_markdown(view: BoundaryView) -> str:
    """The human-readable artifact.

    Every fact in it comes from `view`, in the same order as `render_json`, so a
    reader comparing the two documents reads the same claims in the same
    sequence. Nothing is computed here that is not computed there.
    """
    lines = [
        "# Observability boundary",
        "",
        f"- **Boundary id:** {view.boundary_id}",
        f"- **Negotiated tier:** {view.tier}",
        f"- **Archetype:** {view.archetype or 'ambiguous -- not classified'}",
        f"- **Knowledge plane:** {view.knowledge_plane_location}",
        f"- **Control plane:** {view.control_plane_location}",
        f"- **Permitted outbound:** {', '.join(view.permitted_outbound_categories) or 'nothing'}",
        f"- **Unavailable capabilities:** "
        f"{', '.join(view.unavailable_capabilities) or 'none -- every capability is available'}",
        f"- **Contractual approval:** {view.contractual_approval_ref or _ABSENT}",
        f"- **Declared at:** {format_timestamp(view.declared_at)}",
        "",
    ]
    if view.decline_recommended:
        lines += [
            "## Recommendation: decline",
            "",
            "No artifact access was confirmed. There is nothing to extract, nothing to",
            "bind to and nothing to observe changing, so no claim this platform makes",
            "could be supported by evidence from this system.",
            "",
        ]
    if view.archetype_floor_violated:
        lines += [
            "## Boundary violation",
            "",
            f"This is an `ai` system negotiated at {view.tier}, below the floor its",
            "archetype requires. Without a safe interaction path a prompt or model",
            "change can be detected and cannot be evaluated. The following are named",
            "as unavailable rather than degraded:",
            "",
            *(f"- {capability}" for capability in view.unavailable_capabilities),
            "",
        ]
    lines += [
        "This statement is generated from one stored row. The machine-readable",
        "artifact and this document cannot disagree, because neither is authored.",
        "",
    ]
    return "\n".join(lines)
