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

**Build 9 adds the handover event's rules to this package**, and both absences
above still hold: nothing here writes, and nothing here can reach a model.

    event       the six recorded steps, their order, and the fold that turns
                `audit_event` rows into one `HandoverRecord`
    elicit      open gaps -> targeted asks, grouped by the owner who answers
    checklist   what a valid verification round is, and what a failure becomes
    snapshot    the acceptance digest, over a bundle's table digests alone
    acceptance  the record both parties hold, rendered from the fold

The event's state is the store's audit trail rather than a new table -- v6.1 §8
closed the schema budget at Build 4 -- so this package reads rows a caller hands
it and returns values a caller writes. The CLI's `_handover_support` is where
those meet a store, exactly as `_pack_support` is for assembly.
"""

from adopt_handover.acceptance import (
    ACCEPTANCE_FILENAME,
    RECORD_VERSION,
    OpenQuestion,
    effective_owner,
    record_payload,
    render_record,
)
from adopt_handover.assemble import (
    AssembledPack,
    AssembledSection,
    StampedRevision,
    assemble,
)
from adopt_handover.checklist import (
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_SKIPPED,
    OUTCOMES,
    Checklist,
    Task,
    counts,
    failures,
    parse_checklist,
    question_for,
)
from adopt_handover.derived import FORMATS, MARKDOWN, Converter, convert, converter_for
from adopt_handover.elicit import (
    ASKS_BY_KIND,
    GENERIC_ASK,
    UNASSIGNED,
    Agenda,
    AgendaGroup,
    AgendaItem,
    agenda,
    ask_for,
    is_open_gap,
    render_agenda,
)
from adopt_handover.event import (
    HANDOVER_CLOSED,
    HANDOVER_ELICITED,
    HANDOVER_EVENT_TYPES,
    HANDOVER_PACK_EMITTED,
    HANDOVER_SNAPSHOT_TAKEN,
    HANDOVER_STARTED,
    HANDOVER_VERIFIED,
    PREREQUISITE,
    STEP_ORDER,
    VERB_FOR,
    HandoverRecord,
    current,
    decode_detail,
    encode_detail,
    fold,
    require,
)
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
from adopt_handover.render import render, render_sections, section_blocks
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
from adopt_handover.sidecar import parse_sidecar, render_sidecar, sections_affected
from adopt_handover.snapshot import ACCEPTANCE_DIGEST_ALGORITHM, acceptance_digest
from adopt_handover.views import (
    PackBoundary,
    PackConflict,
    PackGap,
    PackIdentity,
    PackKnowledge,
)

__all__ = [
    "ACCEPTANCE_DIGEST_ALGORITHM",
    "ACCEPTANCE_FILENAME",
    "ASKS_BY_KIND",
    "AUDIENCES",
    "FORMATS",
    "FRESH",
    "GENERIC_ASK",
    "HANDOVER_CLOSED",
    "HANDOVER_ELICITED",
    "HANDOVER_EVENT_TYPES",
    "HANDOVER_PACK_EMITTED",
    "HANDOVER_SNAPSHOT_TAKEN",
    "HANDOVER_STARTED",
    "HANDOVER_VERIFIED",
    "MARKDOWN",
    "OUTCOMES",
    "OUTCOME_FAIL",
    "OUTCOME_PASS",
    "OUTCOME_SKIPPED",
    "PREREQUISITE",
    "RECORD_VERSION",
    "SECTIONS",
    "STALE",
    "STEP_ORDER",
    "UNASSIGNED",
    "UNVERIFIED",
    "UNVERIFIED_BANNER",
    "VERB_FOR",
    "Agenda",
    "AgendaGroup",
    "AgendaItem",
    "AssembledPack",
    "AssembledSection",
    "BoundaryReader",
    "BoundaryView",
    "Checklist",
    "ConflictView",
    "Converter",
    "FreshnessReader",
    "GapView",
    "HandoverRecord",
    "IdentityReader",
    "IdentityView",
    "KnowledgeReader",
    "KnowledgeView",
    "OpenQuestion",
    "PackBoundary",
    "PackConflict",
    "PackGap",
    "PackIdentity",
    "PackKnowledge",
    "Section",
    "StampedRevision",
    "Task",
    "acceptance_digest",
    "agenda",
    "ask_for",
    "assemble",
    "banner_for",
    "convert",
    "converter_for",
    "counts",
    "current",
    "decode_detail",
    "effective_owner",
    "encode_detail",
    "failures",
    "fold",
    "is_open_gap",
    "parse_checklist",
    "parse_sidecar",
    "question_for",
    "record_payload",
    "render",
    "render_agenda",
    "render_record",
    "render_sections",
    "render_sidecar",
    "require",
    "section_blocks",
    "sections_affected",
    "select",
    "select_drafts",
    "stamp_for",
]
