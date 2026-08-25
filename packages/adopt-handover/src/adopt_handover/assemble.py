"""Assembly: the query half of `adopt pack`. Produces values, never bytes.

v6.1 §6 Build 4: *"Assembly is a query."* This module runs it -- sections select
confirmed knowledge by audience, kind and scope; the map contributes the
inventory; the coverage join contributes the gap appendix; the boundary row is
embedded so every pack states its own limits.

**Nothing here renders and nothing here writes.** `render` turns an `AssembledPack`
into bytes and `sidecar` turns the same value into lineage, so the selection
rules can be tested without asserting on Markdown and the byte-stability test has
one input it fully controls.
"""

from dataclasses import dataclass, field

from adopt_handover.ports import (
    BoundaryReader,
    BoundaryView,
    FreshnessReader,
    GapView,
    IdentityReader,
    IdentityView,
    KnowledgeReader,
    KnowledgeView,
)
from adopt_handover.sections import (
    SECTIONS,
    Section,
    banner_for,
    select,
    select_drafts,
    stamp_for,
)

__all__ = ["AssembledPack", "AssembledSection", "StampedRevision", "assemble"]


@dataclass(frozen=True, slots=True)
class StampedRevision:
    """One revision as it will render: its body, its stamp and its date."""

    revision: KnowledgeView
    stamp: str

    @property
    def banner(self) -> str | None:
        return banner_for(self.stamp)


@dataclass(frozen=True, slots=True)
class AssembledSection:
    """One section, selected and stamped, ready to render.

    Confirmed content and drafts arrive in **one** tuple, already ordered, and
    the renderer cannot tell them apart -- it emits each body under the stamp it
    was handed. That is deliberate: a renderer that knew which were drafts is a
    renderer with a branch that could forget the banner. `stamp_for` decided
    that, once, before any of these got here.
    """

    section: Section
    revisions: tuple[StampedRevision, ...] = ()

    @property
    def key(self) -> str:
        return self.section.key

    @property
    def is_empty(self) -> bool:
        return not self.revisions

    @property
    def drafted(self) -> int:
        """How many of them nobody has confirmed. For `--json`, never for bytes."""
        return sum(1 for stamped in self.revisions if stamped.revision.verification != "verified")


@dataclass(frozen=True, slots=True)
class AssembledPack:
    """Everything a pack renders from, and nothing about how it renders."""

    audience: str
    sections: tuple[AssembledSection, ...]
    identities: tuple[IdentityView, ...] = ()
    gaps: tuple[GapView, ...] = ()
    boundary: BoundaryView | None = None
    #: Kind -> count, for the overview. Derived here so the renderer counts
    #: nothing and two renderings of one pack cannot disagree.
    kind_counts: tuple[tuple[str, int], ...] = field(default_factory=tuple)


def assemble(
    *,
    audience: str,
    knowledge: KnowledgeReader,
    identities: IdentityReader,
    freshness: FreshnessReader,
    boundary: BoundaryReader,
    gaps: tuple[GapView, ...],
    drafts: tuple[KnowledgeView, ...] = (),
) -> AssembledPack:
    """Select, stamp and order everything the pack will contain.

    Args:
        audience: The audience tag sections filter on. Free text by design --
            `audience_tag` declares no enum, so a firm's fifth audience needs no
            code change and a typo shows up as an empty pack rather than an
            error nobody can act on.
        gaps: The derived gaps already joined to their dispositions. Passed in
            rather than read here because existence is `recompute_coverage()`'s
            answer and the join belongs to the composition root -- this module
            must not be able to produce a gap list of its own.
        drafts: Unverified revisions a drafting run produced, to render **below**
            each section's confirmed content under their UNVERIFIED banners
            (Build 4, v6.1 §6). Passed separately from `knowledge` rather than
            filtered out of it, because "which unverified revisions are drafts"
            is a provenance question only the composition root can answer -- a
            harvest candidate is unverified too and does not belong in a client's
            document. Empty by default, so every no-model path is unchanged.

    Returns:
        An `AssembledPack` whose every ordering is deterministic, so `render`
        can be a pure function of it.
    """
    revisions = tuple(knowledge.knowledge_for_pack())

    assembled: list[AssembledSection] = []
    for section in SECTIONS:
        if not section.kinds:
            assembled.append(AssembledSection(section=section))
            continue
        # Confirmed first, then drafts. The order is the reader's: what a human
        # stands behind comes before what nobody has checked yet, and a section
        # whose confirmed half is empty opens with the banner rather than
        # burying it.
        chosen = select(section, revisions, audience) + select_drafts(section, drafts, audience)
        assembled.append(
            AssembledSection(
                section=section,
                revisions=tuple(
                    StampedRevision(
                        revision=revision,
                        stamp=stamp_for(revision, freshness.freshness_of(revision.item_id)),
                    )
                    for revision in chosen
                ),
            )
        )

    inventory = tuple(sorted(identities.identities_for_pack(), key=lambda row: (row.kind, row.uri)))
    counts: dict[str, int] = {}
    for row in inventory:
        counts[row.kind] = counts.get(row.kind, 0) + 1

    return AssembledPack(
        audience=audience,
        sections=tuple(assembled),
        identities=inventory,
        gaps=tuple(sorted(gaps, key=lambda gap: (gap.kind, gap.uri))),
        boundary=boundary.boundary_for_pack(),
        kind_counts=tuple(sorted(counts.items())),
    )
