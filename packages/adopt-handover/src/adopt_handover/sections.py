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
    "select_drafts",
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
#: It says exactly one thing -- *no human has confirmed this* -- because that is
#: exactly what the stamp means (see `stamp_for`). An earlier wording covered a
#: second case as well, and covering two cases is what made it worthless: it
#: printed over human-written confirmed documents as readily as over drafts, so
#: every section of every pack carried it and the word stopped distinguishing
#: anything. A banner on everything is a banner on nothing.
UNVERIFIED_BANNER: Final[str] = (
    "> **UNVERIFIED — no human has confirmed this section.**\n"
    "> It was drafted or mined from what the system shows, and nobody has yet "
    "agreed that it is true. Treat every statement in it as a proposal to verify."
)

#: Rendered above a stale body, for the same reason. The sentence is a **claim
#: about a change**, so only a freshness state that actually means "something
#: changed under this" may reach it -- see `stamp_for`.
STALE_BANNER: Final[str] = (
    "> **STALE — the system changed after this was last confirmed.**\n"
    "> What follows was true when it was written and may not be now."
)

#: Freshness states that mean *something changed, or stopped being watched,
#: under knowledge a human did confirm*. **Only these reach the stale banner**,
#: which is not a nicety: that banner claims a change occurred, and printing it
#: over an item nothing has ever observed is exactly the false-staleness noise
#: v6.1 H5 identifies as what makes reviewers stop trusting the queue. Every
#: other state on confirmed content is `fresh` -- see `stamp_for`.
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

    Two inputs, two questions, and which one is asked first is the safety
    property:

    1. **Did a human confirm this?** If not the stamp is `unverified`, whatever
       the freshness resolution says. `resolve_freshness` answers a different
       question -- *has anything changed under this?* -- and an unconfirmed
       draft's problem is not staleness, it is that nobody has agreed with it.
       Asking freshness first would let a draft written seconds ago render
       `fresh`, which is the sentence v6.1 calls this build's worst failure. A
       missing marker counts as unconfirmed: a verification that was never
       written is not a confirmation.
    2. **Has anything changed under it since?** Only a state that actually means
       *something changed* is `stale`; everything else is `fresh`.

    **The second half was wrong when S4.1 shipped it, and running the demo is
    what showed it.** The rule then had a third branch: a confirmed revision
    whose freshness was `unverified` -- which is *every* item in *every* store
    before Build 5, because `knowledge_item.freshness_state` starts `unverified`
    and only sensing ever moves it -- stamped `unverified` and carried the
    banner. So a document a human wrote and confirmed rendered as unconfirmed,
    every section of every pack was bannered, and demo line 3 (*confirm a draft,
    watch it upgrade*) changed nothing a reader could see. A banner that appears
    on everything distinguishes nothing, which makes it worse than absent: it
    trains readers to skip the one line protecting them from a real draft.

    What that branch was protecting is kept, and it is a different thing: the
    **stale** banner claims the system changed, so a never-observed item must
    not carry it. That is why `_STALE_FRESHNESS` is a membership test rather
    than `!= fresh`. The correction is only about which of the two remaining
    stamps a confirmed, unchanged item gets -- and in a three-value vocabulary
    where `unverified` is false and `stale` is false, `fresh` is both the only
    value left and the honest one: confirmed, and nothing has invalidated it.
    The date rendered beside it says when.
    """
    if revision.verification != "verified":
        return UNVERIFIED
    return STALE if freshness in _STALE_FRESHNESS else FRESH


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
    return _ordered(chosen)


def select_drafts(
    section: Section, drafts: tuple[KnowledgeView, ...], audience: str
) -> tuple[KnowledgeView, ...]:
    """The **unverified drafts** a section renders, below its confirmed content.

    Build 4's drafting half (v6.1 §6: drafts "rendered with UNVERIFIED stamps").
    A separate function rather than a flag on `select`, and the separation is the
    honesty invariant made structural: `select` is what "confirmed knowledge"
    means everywhere in this package, and a boolean that relaxed it would be one
    argument away from a pack that promoted every draft it found.

    **The caller decides what a draft is.** This filters on kind, audience and
    *not confirmed*; which revisions are drafts at all is the composition root's
    answer, read from the `draft:` provenance a drafting run wrote. That matters:
    a harvest candidate is also `unverified`, and it is review fodder rather than
    handover content -- a filter that took every unverified revision would put
    every unconfirmed commit in the client's document.

    A confirmed draft leaves this set by itself. Confirming appends a `verified`
    revision to the same item, so the head stops matching here and starts
    matching `select` -- one item, one chain, and the stamp changes because the
    revision did.
    """
    chosen = [
        revision
        for revision in drafts
        if revision.kind in section.kinds
        and revision.verification != "verified"
        and audience in revision.audiences
    ]
    return _ordered(chosen)


def _ordered(revisions: list[KnowledgeView]) -> tuple[KnowledgeView, ...]:
    """By `(title, revision_id)` -- never by insertion order or by date.

    The bytes must be a pure function of the revisions, and two revisions written
    in the same millisecond would otherwise order by whatever the store returned.
    """
    return tuple(sorted(revisions, key=lambda revision: (revision.title, revision.revision_id)))
