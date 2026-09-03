"""Candidate selection: exact URI lookup merged with ranked text search.

**Two retrievers, one ordering, and the order is the point.** A question that
names a canonical URI has told us exactly what it is about, and no ranking
function should be allowed to disagree with it. So exact matches come first, in
URI order, and ranked text fills the rest of the budget. BM25 over a small
corpus is noisy enough that an unmerged ranking regularly puts a passage
mentioning "orders" above the passage *bound to the orders endpoint the question
named* -- which reads to an FDE as the tool not understanding its own address
scheme.

**This module chooses candidates; it does not choose an answer.** Nothing here
reads freshness or verification. That separation is what makes the freshness
check impossible to skip: `branch.compose` cannot be called with candidates
alone, so there is no path from "I retrieved something" to "I served something"
that does not pass through a resolution (invariant #5).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from adopt_ask.records import Passage, SearchRecords
from adopt_const import ASK_TOP_K, URI_SCHEME

__all__ = [
    "STOPWORDS",
    "Candidate",
    "CandidateOrigin",
    "content_terms",
    "covers_question",
    "retrieve",
    "uris_in",
]

#: Word characters, Unicode-aware.
_WORD: Final[re.Pattern[str]] = re.compile(r"\w+", re.UNICODE)

#: English function words. Dropped from every question before anything sees it.
#:
#: **Without this the UNKNOWN branch is unreachable.** Retrieval OR-s its terms,
#: so one shared "the" makes every document in the store a candidate for every
#: question, and a branch that can always find *something* verified always
#: answers KNOWN -- the unqualified guess this build exists to prevent, arriving
#: through retrieval instead of through generation. Observed on the first real
#: journey run: *"how do I rotate the API key?"* matched a deployment runbook on
#: the word "the".
#:
#: **Function words only, and that boundary is the safety property.** Nothing
#: here carries information about a client's system, so dropping these can never
#: suppress a passage that was genuinely about the question. A list reaching into
#: domain vocabulary -- "service", "key", "config" -- could, which is why this
#: one stops at grammar.
STOPWORDS: Final[frozenset[str]] = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "did",
        "do",
        "does",
        "doing",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "shall",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "too",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)


def content_terms(question: str) -> tuple[str, ...]:
    """The words a question is *about*, de-duplicated, in first-seen order.

    One definition, used twice: the realization builds its engine query from
    this list, and `covers_question` judges candidates against the same list.
    Two tokenizations would mean the filter weighing different words than the
    search matched, which is the kind of disagreement that shows up as
    "retrieval is flaky" and is never traced back.
    """
    seen: dict[str, None] = {}
    for word in _WORD.findall(question):
        folded = word.casefold()
        if folded not in STOPWORDS:
            seen.setdefault(folded, None)
    return tuple(seen)


def covers_question(passage: Passage, terms: Sequence[str]) -> bool:
    """Whether `passage` matches enough of `terms` to be about the question.

    **The rule: two distinct content terms, or all of them when the question has
    fewer than two.** One shared word is a coincidence; two is a topic. This is
    what stands between "the store mentions your word somewhere" and "the store
    answers your question", and without it BM25 will happily rank a runbook
    first because it says "API" once.

    Both literals are `1` and `2`, which the constants rule exempts, and
    deliberately so rather than as a loophole: this is not a tunable awaiting
    measurement. It is the smallest coverage that can distinguish a topic from a
    coincidence, and a store where the right value is `5` is a store whose
    questions should be narrowing scope instead.

    Matched against title, body and bound URIs together: a passage is about a
    question if any of the text it was indexed on is.
    """
    if not terms:
        return False
    haystack = " ".join((passage.title, passage.body_md, *passage.identity_uris)).casefold()
    words = set(_WORD.findall(haystack))
    matched = sum(1 for term in terms if term in words)
    return matched >= min(2, len(terms))


#: How a candidate was found. Carried into the answer payload because "you named
#: this" and "this ranked well for your words" are different claims, and an
#: operator debugging a poor answer needs to know which one they are looking at.
CandidateOrigin = Literal["uri", "text"]

_ORIGIN_URI: Final[CandidateOrigin] = "uri"
_ORIGIN_TEXT: Final[CandidateOrigin] = "text"

#: A canonical URI as it appears inside prose. Deliberately permissive about the
#: tail: `parse_uri` is the authority on whether a string is a valid URI, and a
#: pattern that tried to be that authority here would be a second, drifting copy
#: of Build 0's grammar. This finds candidates for the store to confirm.
_URI_IN_PROSE: Final[re.Pattern[str]] = re.compile(re.escape(f"{URI_SCHEME}://") + r"[^\s<>\"'`]+")


@dataclass(frozen=True, slots=True)
class Candidate:
    """A passage that survived retrieval, with how it was found."""

    passage: Passage
    origin: CandidateOrigin


def uris_in(question: str) -> tuple[str, ...]:
    """Canonical URIs mentioned in the question, de-duplicated, in first-seen order.

    Trailing sentence punctuation is stripped: a question ends `…/prod/endpoint/-/x?`
    and the `?` is grammar, not part of the address. Only characters that cannot
    end a URI segment are removed, so a key legitimately ending in one is not
    silently truncated -- the store lookup simply misses, which is visible, where
    a wrong-but-plausible URI would not be.
    """
    seen: dict[str, None] = {}
    for match in _URI_IN_PROSE.finditer(question):
        seen.setdefault(match.group().rstrip(".,;:!?"), None)
    return tuple(seen)


def retrieve(
    records: SearchRecords,
    question: str,
    *,
    limit: int = ASK_TOP_K,
) -> tuple[Candidate, ...]:
    """Candidates for `question`, best first, at most `limit`.

    Exact URI matches are never crowded out by text hits: they are placed first
    and the text search fills what remains. A passage found both ways appears
    once, as a URI match, because that is the stronger claim about why it is
    here.

    **Only text hits are held to `covers_question`.** A URI match needs no
    coverage test: the question named that exact referent, which is a stronger
    statement of relevance than any count of shared words. Applying the rule to
    both would refuse to answer a question that spelled out precisely what it
    was about.
    """
    candidates: list[Candidate] = []
    claimed: set[str] = set()

    named = uris_in(question)
    if named:
        for passage in sorted(records.lookup_uris(named), key=_exact_order):
            if passage.revision_id not in claimed:
                claimed.add(passage.revision_id)
                candidates.append(Candidate(passage=passage, origin=_ORIGIN_URI))

    if len(candidates) >= limit:
        return tuple(candidates[:limit])

    terms = content_terms(question)
    for passage in records.search(question, limit=limit):
        if passage.revision_id in claimed or not covers_question(passage, terms):
            continue
        claimed.add(passage.revision_id)
        candidates.append(Candidate(passage=passage, origin=_ORIGIN_TEXT))
        if len(candidates) == limit:
            break

    return tuple(candidates)


def _exact_order(passage: Passage) -> tuple[str, str]:
    """Stable order for exact matches: by first URI, then revision id.

    Deterministic rather than arbitrary because two runs over one store must
    produce the same answer -- an assistant whose citation order moves between
    runs is one nobody can quote in a handover document.
    """
    first = min(passage.identity_uris) if passage.identity_uris else ""
    return (first, passage.revision_id)
