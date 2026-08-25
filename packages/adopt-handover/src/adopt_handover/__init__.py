"""`adopt pack` — audience-scoped handover packs, assembled from the store.

Build 4 (v6.1 §6). The Handover Generator's deterministic half: sections select
**confirmed** knowledge by audience and kind, the map contributes the inventory,
the coverage join contributes the gap appendix, and Build 0's observability
boundary is embedded so every pack states its own limits. Every section carries
the verification status and date of the knowledge behind it.

**This package reads. It never writes and never calls a model.** Both absences
are structural rather than habitual: there is no writer protocol in `ports` to
reach for, and `adopt-agent` is not a dependency, so assembly runs identically
with no adapter configured -- which is what v6.1 R3 requires of every capability.
Build 4's drafting lives in `adopt_knowledge` (the package that writes
knowledge) and is composed with this one by the CLI.

Four modules, one direction:

    ports     what the store must answer, structurally
    sections  what a pack contains, and the stamp rule
    assemble  the query: select, stamp, order -> AssembledPack
    render    AssembledPack -> byte-stable Markdown
    sidecar   AssembledPack -> lineage JSON for Build 8
    derived   Markdown -> DOCX/PDF through a pinned subprocess (Build 4 S4.2)

Rendering is a pure function of `AssembledPack`, and `AssembledPack` carries no
clock. That is what makes a pack byte-stable given the same revisions, which
`golden`-grade tests assert directly.
"""

from adopt_handover.assemble import (
    AssembledPack,
    AssembledSection,
    StampedRevision,
    assemble,
)
from adopt_handover.derived import FORMATS, MARKDOWN, Converter, convert, converter_for
from adopt_handover.ports import (
    BoundaryReader,
    BoundaryView,
    ConflictView,
    FreshnessReader,
    GapView,
    IdentityReader,
    IdentityView,
    KnowledgeReader,
    KnowledgeView,
)
from adopt_handover.render import render
from adopt_handover.sections import (
    AUDIENCES,
    FRESH,
    SECTIONS,
    STALE,
    UNVERIFIED,
    UNVERIFIED_BANNER,
    Section,
    banner_for,
    select,
    select_drafts,
    stamp_for,
)
from adopt_handover.sidecar import render_sidecar
from adopt_handover.views import (
    PackBoundary,
    PackConflict,
    PackGap,
    PackIdentity,
    PackKnowledge,
)

__all__ = [
    "AUDIENCES",
    "FORMATS",
    "FRESH",
    "MARKDOWN",
    "SECTIONS",
    "STALE",
    "UNVERIFIED",
    "UNVERIFIED_BANNER",
    "AssembledPack",
    "AssembledSection",
    "BoundaryReader",
    "BoundaryView",
    "ConflictView",
    "Converter",
    "FreshnessReader",
    "GapView",
    "IdentityReader",
    "IdentityView",
    "KnowledgeReader",
    "KnowledgeView",
    "PackBoundary",
    "PackConflict",
    "PackGap",
    "PackIdentity",
    "PackKnowledge",
    "Section",
    "StampedRevision",
    "assemble",
    "banner_for",
    "convert",
    "converter_for",
    "render",
    "render_sidecar",
    "select",
    "select_drafts",
    "stamp_for",
]
