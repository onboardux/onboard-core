"""What a pack contains, per audience, and how each section is stamped.

Code-adjacent data: the section list is a tuple of declarations, not a template
language. v6.1 §6 Build 4 names the sections a pack carries -- system overview
from the map, runbook/how-to from confirmed knowledge, a decisions appendix, a
gap appendix, the boundary statement -- and this module is that list plus the
two rules that decide what a reader is told about each one.

**The stamp rule is this build's safety property.** v6.1: *"a client reading a
draft as verified truth is this build's worst failure."* So the rule is written
once, here, and every section goes through it -- there is no path that renders a
body without asking for its stamp, because `stamp_for` takes the revision and
the caller has nothing else to render from.
"""

from dataclasses import dataclass
from typing import Final

from adopt_handover.ports import KnowledgeView

__all__ = [
    "AUDIENCES",
    "FRESH",
    "SECTIONS",
    "STALE",
    "UNVERIFIED",
    "UNVERIFIED_BANNER",
    "Section",
    "select",
    "stamp_for",
]

#: The stamp vocabulary v6.1 §6 Build 4 names: `{fresh|stale|unverified}`.
FRESH: Final[str] = "fresh"
STALE: Final[str] = "stale"
UNVERIFIED: Final[str] = "unverified"

#: The audience vocabulary, the same tuple `adopt_knowledge.documents` tags with.
#: Not imported from there: this package does not depend on `adopt-knowledge`,
#: and `--audience` accepts free text anyway (`audience_tag` declares no enum),
#: so this is the list a pack knows how to *name*, never a filter on what a firm
#: may ask for.
AUDIENCES: Final[tuple[str, ...]] = ("technical", "client_ops", "end_user", "admin")

#: Rendered above any unverified body, every time. Deliberately a banner and not
#: a suffix: a reader who stops reading half way through a section must already
#: have been told. v6.1 -- unverified content is "typographically unmistakable".
#:
#: The wording covers **both** ways a section is unverified (see `stamp_for`):
#: no human has confirmed the content, or nothing has confirmed it is still
#: true of the running system. Both mean the same thing to the person reading
#: it -- do not treat this as checked -- and v6.1's stamp vocabulary is three
#: values, so inventing a fourth to separate them would be a distinction the
#: contract does not carry.
UNVERIFIED_BANNER: Final[str] = (
    "> **UNVERIFIED — nothing has confirmed this section.**\n"
    "> Either no human has approved it, or nothing has checked it against the "
    "running system. Treat every statement in it as a proposal to verify."
)

#: Rendered above a stale body, for the same reason. The sentence is a **claim
#: about a change**, so only a freshness state that actually means "something
#: changed under this" may reach it -- see `stamp_for`.
STALE_BANNER: Final[str] = (
    "> **STALE — the system changed after this was last confirmed.**\n"
    "> What follows was true when it was written and may not be now."
)

#: Freshness states that mean *something changed, or stopped being watched,
#: under knowledge a human did confirm*. Everything outside this set and
#: `fresh` is unverified rather than stale, which is not a nicety: the stale
#: banner claims a change occurred, and printing it over an item that has
#: simply never been observed is exactly the false-staleness noise v6.1 H5
#: identifies as the thing that makes reviewers stop trusting the queue.
_STALE_FRESHNESS: Final[frozenset[str]] = frozenset({"stale", "observation_stale", "retired"})


@dataclass(frozen=True, slots=True)
class Section:
    """One section of a pack.

    `kinds` selects from the `item_kind` vocabulary. A section with no `kinds`
    is not knowledge-backed -- the overview, gaps and boundary sections are
    rendered from the map, the coverage join and the boundary row respectively.
    """

    key: str
    heading: str
    kinds: tuple[str, ...] = ()
    #: What the section says when it selected nothing. Rendered rather than
    #: omitted, because a pack that silently drops an empty section tells the
    #: reader nothing is missing -- and the gap is the deliverable (v6.1 §1.1
    #: row 7). Without a model this is also the whole of the no-model mode:
    #: R3 requires a complete pack with no adapter configured.
    empty_note: str = "No confirmed knowledge yet for this section."


#: The pack, in render order. One list for every audience: what differs per
#: audience is which knowledge carries that audience tag, not which sections
#: exist -- a client_ops pack missing its runbook heading would look complete
#: while being empty, where an empty runbook section says so.
SECTIONS: Final[tuple[Section, ...]] = (
    Section(key="overview", heading="System overview"),
    Section(
        key="runbook",
        heading="Runbook and how-to",
        kinds=("procedure",),
        empty_note="No confirmed procedures yet. Every entry below the gap appendix "
        "is a candidate for this section.",
    ),
    Section(
        key="answers",
        heading="Answers to common questions",
        kinds=("answer",),
        empty_note="No captured answers yet. `adopt ask` records the questions "
        "nobody could answer; `adopt answer` turns one into knowledge.",
    ),
    Section(
        key="decisions",
        heading="Decisions and rationale",
        kinds=("rationale",),
        empty_note="No confirmed decision records yet. `adopt harvest` mines "
        "candidates from local history; `adopt review` confirms them.",
    ),
    Section(key="gaps", heading="Coverage gaps"),
    Section(key="boundary", heading="Observability boundary"),
)


def stamp_for(revision: KnowledgeView, freshness: str) -> str:
    """The stamp one section carries. **Unverified always wins.**

    Three inputs collapse to v6.1's three-value vocabulary, and the order is the
    safety property:

    1. **A revision nobody confirmed is `unverified`**, whatever the freshness
       resolution says. `resolve_freshness` answers *"has anything changed under
       this?"* and an unconfirmed draft's problem is not staleness -- it is that
       no human has ever agreed with it. Checking freshness first would let a
       freshly-written draft render as `fresh`, which is precisely the sentence
       v6.1 calls this build's worst failure. A missing marker counts as
       unconfirmed: a verification that was never written is not a confirmation.
    2. **`fresh` freshness on confirmed content is `fresh`.**
    3. **Only a freshness state that means something actually changed is
       `stale`.** The remaining states -- chiefly `unverified`, which is what a
       store with no sensor observation reports -- are `unverified` too. This
       distinction is not cosmetic: the stale banner *claims a change occurred*,
       and printing it over knowledge that has simply never been checked
       manufactures exactly the false staleness H5 identifies as the failure
       that makes people stop trusting the queue. The honest sentence for
       never-checked knowledge is "nothing has confirmed this", not "the system
       changed".
    """
    if revision.verification != "verified":
        return UNVERIFIED
    if freshness == FRESH:
        return FRESH
    return STALE if freshness in _STALE_FRESHNESS else UNVERIFIED


def banner_for(stamp: str) -> str | None:
    """The banner a stamp requires above the body, or `None` for a fresh one."""
    if stamp == UNVERIFIED:
        return UNVERIFIED_BANNER
    if stamp == STALE:
        return STALE_BANNER
    return None


def select(
    section: Section, revisions: tuple[KnowledgeView, ...], audience: str
) -> tuple[KnowledgeView, ...]:
    """The confirmed revisions a section renders, ordered deterministically.

    Three filters, and the first is the honesty invariant: **only `verified`
    revisions are selected**. An unverified draft reaches a pack only when
    Build 4's drafting put it there for this run and the caller passed it in
    knowingly -- `assemble` keeps that path separate, so nothing here can
    promote a draft by accident.

    Ordering is by `(title, revision_id)` rather than by insertion or by date:
    the bytes must be a pure function of the revisions, and two revisions
    written in the same millisecond would otherwise order by whatever the store
    returned.
    """
    chosen = [
        revision
        for revision in revisions
        if revision.kind in section.kinds
        and revision.verification == "verified"
        and audience in revision.audiences
    ]
    return tuple(sorted(chosen, key=lambda revision: (revision.title, revision.revision_id)))
