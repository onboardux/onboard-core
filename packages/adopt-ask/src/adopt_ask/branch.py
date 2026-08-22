"""The three-way branch: KNOWN, STALE, UNKNOWN -- and never anything else.

**This module is where critical semantic invariant #5 stops being a rule and
becomes a shape.** `compose` does not accept candidates. It accepts `Resolved` --
a candidate welded to the `FreshnessResolution` for its item -- and `Resolved`
cannot be constructed without one. So there is no argument a caller can supply
that represents "a passage I have not resolved freshness for", which means there
is no code path from retrieval to a served answer that skips the check. Not a
guard that runs first: a value that cannot exist.

That is deliberately stronger than a required `Mapping[item_id, resolution]`
argument, which was the obvious design. A mapping can be missing a key, so it
needs a runtime raise for the gap, and a runtime raise is exactly the thing a
later refactor quietly turns into `.get(..., FRESH)`. Pairing removes the gap
instead of checking for it.

Why any of this is worth the ceremony: "check freshness before serving" is the
kind of rule that survives its first author and dies in the third refactor, when
someone adds a fast path for a case that "obviously cannot be stale". The
failure is silent and lands in a client's face -- a confident, cited answer
about a system that changed last month.

**Two verification filters, deliberately duplicated.** Unverified knowledge never
serves as KNOWN (F6). The FTS index already refuses to hold it, and this module
checks again against the store's own answer. Not belt-and-braces: the index is
derived and can be stale, so it is precisely the wrong authority on what may be
served. The index makes the common case fast; the store makes the guarantee
true.

**STALE serves rather than refuses.** A stale answer carries the prior content
*and* the rule that decided staleness (F3, pre-B6: identity death, moves,
retirement). Refusing would discard knowledge the store genuinely holds; the
honest form is "here is what we knew, and here is why it may be wrong".
"""

from collections.abc import Sequence, Set
from dataclasses import dataclass
from typing import Final, Literal

from adopt_ask.retrieve import Candidate, CandidateOrigin
from adopt_freshness import FreshnessResolution

__all__ = ["KNOWN", "STALE", "UNKNOWN", "Answer", "Branch", "Citation", "Resolved", "compose"]

Branch = Literal["known", "stale", "unknown"]

KNOWN: Final[Branch] = "known"
STALE: Final[Branch] = "stale"
UNKNOWN: Final[Branch] = "unknown"

#: The freshness states that may serve as KNOWN. Everything else -- `stale`,
#: `observation_stale`, `retired` -- serves as STALE with its cause named.
#:
#: **`unverified` is here, and that is the subtle one.** It is
#: `INITIAL_ITEM_FRESHNESS`: the state every knowledge item is created in,
#: meaning *no freshness rule has fired either way*. It is not a staleness
#: signal, and reading it as one would be false staleness manufactured from a
#: default -- the precise failure v6.1's H5 calls "the exact failure that makes
#: FDEs stop trusting the queue". It would also make Build 3 undeliverable:
#: pre-B6 nothing sets an item to `fresh`, so treating `unverified` as STALE
#: means `adopt ask` could never answer KNOWN until Build 6 shipped.
#:
#: v6.1 §6 F3 is the authority and is explicit about the pre-B6 scope: staleness
#: arises from identity **death and moves** surfaced by map reruns, and from
#: **retirement**. Those three arrive as `stale` and `retired`, each carrying the
#: rule that produced it. Nothing else is staleness yet.
#:
#: Naming the servable states rather than the unservable ones is deliberate in
#: the other direction: a `freshness_state` added to the manifest later falls
#: into STALE, which is the safe way to be wrong about a state this code has
#: never seen.
_SERVES_AS_KNOWN: Final[frozenset[str]] = frozenset({"fresh", "unverified"})


@dataclass(frozen=True, slots=True)
class Resolved:
    """A retrieval candidate and the freshness resolution for its item.

    The pairing is the invariant, and `__post_init__` enforces that it is a
    *true* pairing: a resolution computed for some other item is rejected at
    construction. Without that check the type would still admit the failure it
    exists to prevent -- freshness resolved, but not for this passage.
    """

    candidate: Candidate
    freshness: FreshnessResolution

    def __post_init__(self) -> None:
        if self.freshness.item_id != self.candidate.passage.item_id:
            raise ValueError(
                "the freshness resolution is for a different item than the candidate: "
                f"{self.freshness.item_id} != {self.candidate.passage.item_id}"
            )


@dataclass(frozen=True, slots=True)
class Citation:
    """One served passage and the exact grounds for serving it."""

    revision_id: str
    item_id: str
    title: str
    body_md: str
    identity_uris: tuple[str, ...]
    origin: CandidateOrigin
    freshness_state: str
    deciding_rule: str


@dataclass(frozen=True, slots=True)
class Answer:
    """What `adopt ask` returns. One of exactly three branches, always cited.

    An UNKNOWN carries no citations by construction; a KNOWN or STALE carries at
    least one. `__post_init__` refuses to build the alternatives rather than
    leaving a caller to notice -- an uncited KNOWN is the unqualified guess this
    whole build exists to make impossible.
    """

    question: str
    branch: Branch
    citations: tuple[Citation, ...]
    #: Present exactly when `branch` is STALE: the rule that decided staleness,
    #: taken verbatim from the resolution rather than re-derived here.
    cause: str | None = None
    #: Revisions retrieved but withheld as unverified. Reported so an UNKNOWN
    #: over a store that *does* hold matching text says which of the two reasons
    #: applied -- nothing matched, or nothing matched that was verified. Those
    #: send an operator to different places: write the answer, or go confirm the
    #: draft that already says it.
    withheld: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.branch == UNKNOWN and self.citations:
            raise ValueError("an UNKNOWN answer cannot carry citations")
        if self.branch != UNKNOWN and not self.citations:
            raise ValueError(f"a {self.branch.upper()} answer must cite at least one revision")
        if (self.cause is None) == (self.branch == STALE):
            raise ValueError("cause is set exactly when the branch is STALE")


def compose(resolved: Sequence[Resolved], verified_revision_ids: Set[str], question: str) -> Answer:
    """Decide the branch over already-resolved candidates.

    Args:
        resolved: Retrieval output paired with freshness, best first. Order is
            preserved into the citations: retrieval already decided what "best"
            means and this function does not second-guess it.
        verified_revision_ids: Revision ids the **store** reports as verified.
            Read from canon rather than from the derived index, because the
            index is exactly the wrong authority on what may be served.
        question: Echoed into the answer so a payload is self-describing.

    Returns:
        KNOWN if any resolved candidate is verified and carries no staleness
        signal; otherwise STALE if any is verified and has gone out of date;
        otherwise UNKNOWN. `_SERVES_AS_KNOWN` records which states are which and
        why `unverified` freshness is not staleness.
    """
    servable = [
        item for item in resolved if item.candidate.passage.revision_id in verified_revision_ids
    ]
    withheld = tuple(
        item.candidate.passage.revision_id
        for item in resolved
        if item.candidate.passage.revision_id not in verified_revision_ids
    )

    fresh = [item for item in servable if item.freshness.state in _SERVES_AS_KNOWN]
    if fresh:
        return Answer(
            question=question,
            branch=KNOWN,
            citations=tuple(_cite(item) for item in fresh),
            withheld=withheld,
        )

    if servable:
        return Answer(
            question=question,
            branch=STALE,
            citations=tuple(_cite(item) for item in servable),
            cause=servable[0].freshness.deciding_rule,
            withheld=withheld,
        )

    return Answer(question=question, branch=UNKNOWN, citations=(), withheld=withheld)


def _cite(item: Resolved) -> Citation:
    passage = item.candidate.passage
    return Citation(
        revision_id=passage.revision_id,
        item_id=passage.item_id,
        title=passage.title,
        body_md=passage.body_md,
        identity_uris=passage.identity_uris,
        origin=item.candidate.origin,
        freshness_state=item.freshness.state,
        deciding_rule=item.freshness.deciding_rule,
    )
