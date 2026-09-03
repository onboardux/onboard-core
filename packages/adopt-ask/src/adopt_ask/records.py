"""The retrieval seam -- v6.1 F1's `SearchRecords` port, and nothing beneath it.

**Why a port at all, when there is exactly one realization today.** v6.0 claimed
B7's ask endpoint would be "adopt-ask's code imported against the Postgres
realization", and F1 found that unimplementable as written: the retrieval it
specifies is SQLite **FTS5**, which does not exist in Postgres. A build that
discovers this in B7 either rewrites retrieval there -- the contract drift the
claim existed to prevent -- or bolts SQLite onto the plane. Declaring the seam
now costs one protocol; retrofitting it later costs the ranking, the merge and
the three-way branch.

So everything that decides *what the answer is* lives above this file, and
everything that knows *how the text was found* lives below it. The split is
load-bearing rather than tidy: `adopt_ask` names no dialect, imports no driver,
and is listed in `no-raw-sqlite` for exactly that reason.

**A passage carries its own citation.** `revision_id` is the canonical thing an
answer cites and `item_id` is what freshness resolves on, so no caller has to
join back to the store to find out what it is allowed to say. A realization that
returned bare text would push that join into every consumer, and the consumer
that forgot it would serve an uncited answer -- which is the one output this
build exists to make impossible.

**Scores are higher-is-better here.** FTS5's `bm25()` is lower-is-better and
negative; normalizing at the realization boundary rather than at each comparison
site keeps the ranking code free of one dialect's sign convention.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

__all__ = ["Passage", "RefreshOutcome", "SearchRecords"]


@dataclass(frozen=True, slots=True)
class Passage:
    """One retrievable revision, with everything an answer needs to cite it.

    `identity_uris` are the URIs bound to the passage's item. They are carried
    because they are both a retrieval key -- a question naming a URI resolves
    directly -- and part of the citation an FDE reads.
    """

    revision_id: str
    item_id: str
    title: str
    body_md: str
    identity_uris: tuple[str, ...] = field(default_factory=tuple)
    #: Higher is better. Zero for passages found by exact lookup rather than
    #: by ranked text search, which are ordered by origin rather than by score.
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    """What a refresh did, and why.

    `reason` is reported rather than inferred because the interesting case is the
    one that did *nothing*: an index that silently declined to rebuild and an
    index that was already current are indistinguishable from the answer they
    produce, and the first is a stale-answer defect.
    """

    rebuilt: bool
    indexed: int
    reason: str


class SearchRecords(Protocol):
    """Retrieval, as the store-shaped port pattern Build 0 established.

    Structural (PEP 544): the realization lives in `adopt_store.annex` and this
    package never imports it, exactly as `adopt_coverage` and `adopt_freshness`
    declare their own read ports and are handed realizations by the composition
    root (CR-34, CR-37).
    """

    def refresh(self, *, force: bool = False) -> RefreshOutcome:
        """Bring the derived index into agreement with the store.

        Implementations must rebuild when the index is absent or disagrees with
        the store, and when `force` is set. **Only revisions that may serve as
        KNOWN are indexed** -- see `adopt_ask.branch` for what that means and
        why it is enforced twice.
        """
        ...

    def search(self, query: str, *, limit: int) -> Sequence[Passage]:
        """Ranked free-text retrieval, best first, at most `limit` passages."""
        ...

    def lookup_uris(self, uris: Sequence[str]) -> Sequence[Passage]:
        """Every indexed passage bound to any of `uris`. Unranked; exact."""
        ...

    def verified_in_store(self, revision_ids: Sequence[str]) -> frozenset[str]:
        """Which of `revision_ids` **canon** reports as verified.

        On this port because every realization has to answer it and answers it
        from the same place -- but pointedly *not* from the index. The index is
        built to hold verified revisions only, which makes it fast and makes it
        the wrong authority: a derived copy that has fallen behind would vouch
        for a revision the store has since superseded. Implementations read the
        canonical table.
        """
        ...
