"""Two tiers of evidence that a document is about an identity, and only two.

v6.1 §6 Build 2 (H2, D9) draws the line this module implements:

* a **structural match** -- a canonical URI written in the document, or a path
  that resolves to **exactly one** identity -- auto-binds; and
* a **name match** -- an identity's key appearing as a token in prose --
  becomes a *suggestion* in the review queue and **never a binding row**.

There is no third heuristic in v1, and the reason is the failure H2 names: a
false binding is the worst silent failure available here. `recompute_coverage`
counts the identity covered, so the gap report stops asking for the knowledge
that is genuinely missing, and staleness fans out to documents that never
described the thing that changed. `config`, `user`, `handler` and `model` are
identity keys **and** ordinary English, and no scoring function distinguishes
the two readings from the text alone -- so this module does not try. It presents
them to a human instead.

**Every function here is pure.** Suggestions are re-derived at review time from
the same code that produced them at ingest (plan decision D3), so a registry
that has changed since the document was ingested yields *current* suggestions
rather than stale ones -- and nothing provisional was ever written to be
cleaned up.
"""

import re
from collections.abc import Container, Sequence
from dataclasses import dataclass
from typing import Final

from adopt_identity import parse_uri

__all__ = [
    "NAME_TIER",
    "STRUCTURAL_TIERS",
    "IdentityView",
    "Match",
    "MatchOutcome",
    "match_document",
    "name_matches",
    "path_matches",
    "structural_matches",
]

#: The tiers, named. A tier is recorded on the binding through
#: `binding_revision.extractor` -- **not** through `locator_rung`, whose meaning
#: contracts §9 fixes as the semantic *locator* hierarchy (product id through
#: fragile selector) for rendered referents. Overloading that column with a
#: second private meaning is the kind of drift Build 4's recipe work would then
#: have to unpick.
URI_TIER: Final[str] = "uri"
PATH_TIER: Final[str] = "path"
NAME_TIER: Final[str] = "name"
STRUCTURAL_TIERS: Final[tuple[str, ...]] = (URI_TIER, PATH_TIER)

_URI_RE: Final[re.Pattern[str]] = re.compile(r"onboard-v1://[^\s`'\"<>)\]]+")
_BACKTICKED_RE: Final[re.Pattern[str]] = re.compile(r"`([^`\n]{1,200})`")
#: A bare token only counts as a path when it carries a separator *and* a
#: suffix. `src/payments/refund.ts` qualifies; `and/or` does not. Prose is full
#: of the second shape and none of the first.
_BARE_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w/.-])([\w.-]+(?:/[\w.-]+)+\.[A-Za-z0-9]{1,8})"
)


@dataclass(frozen=True, slots=True)
class IdentityView:
    """What a matcher needs to know about one identity.

    Assembled by the caller from `identity` plus its head `identity_revision`,
    so this package neither reads a store nor knows one exists.
    """

    identity_id: str
    uri: str
    #: Every path an extractor recorded for this identity, from
    #: `identity_revision.source_ref` (`<path>:<start>-<end>`), POSIX-relative.
    source_paths: tuple[str, ...] = ()

    @property
    def key_leaf(self) -> str:
        """The last key segment -- what a document would call this thing.

        A malformed URI yields an empty leaf rather than raising: the registry
        is the store's, a matcher is not the place to discover corruption in it,
        and an empty leaf simply never matches.
        """
        try:
            parsed = parse_uri(self.uri)
        except Exception:
            return ""
        return parsed.key[-1] if parsed.key else ""


@dataclass(frozen=True, slots=True)
class Match:
    """One identity a document refers to, and the evidence for it."""

    identity_id: str
    uri: str
    tier: str
    #: The exact text that matched, so a reviewer sees *why* something is
    #: proposed rather than being asked to trust a score.
    evidence: str

    @property
    def is_structural(self) -> bool:
        return self.tier in STRUCTURAL_TIERS


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    """A document's matches, split by what may be done with them."""

    structural: tuple[Match, ...]
    suggested: tuple[Match, ...]
    #: Paths that named several identities. Reported, never bound and never
    #: suggested: "this file holds four endpoints" is not evidence about which
    #: one the prose is describing.
    ambiguous_paths: tuple[str, ...]


def _tokens_of(body: str) -> tuple[frozenset[str], frozenset[str]]:
    """`(backticked spans, bare path-shaped tokens)` found in the body."""
    backticked = frozenset(match.group(1).strip() for match in _BACKTICKED_RE.finditer(body))
    bare = frozenset(match.group(1) for match in _BARE_PATH_RE.finditer(body))
    return backticked, bare


def _path_index(identities: Sequence[IdentityView]) -> dict[str, list[IdentityView]]:
    """Every recorded source path -> the identities extracted from it."""
    index: dict[str, list[IdentityView]] = {}
    for identity in identities:
        for path in identity.source_paths:
            index.setdefault(path, []).append(identity)
    return index


def _path_candidates(token: str, index: dict[str, list[IdentityView]]) -> list[IdentityView]:
    """Identities for a path token, matched exactly or by trailing segments.

    A document usually writes `src/payments/refund.ts` where the store recorded
    the same path, but it may reasonably write `payments/refund.ts`. A suffix
    match is accepted **only when it is unambiguous**, which the caller enforces
    by refusing anything that resolves to more than one identity.
    """
    exact = index.get(token)
    if exact:
        return exact
    suffix = "/" + token.lstrip("/")
    matched: list[IdentityView] = []
    for path, identities in index.items():
        if path.endswith(suffix):
            matched.extend(identities)
    return matched


def structural_matches(
    body: str, identities: Sequence[IdentityView]
) -> tuple[tuple[Match, ...], tuple[str, ...]]:
    """`(matches, ambiguous paths)` -- the tier that may auto-bind.

    Two kinds of evidence qualify, and both are statements the document makes
    about *structure* rather than about language:

    1. **A canonical URI**, written out and resolving to a known identity. The
       author addressed the referent; there is nothing to infer.
    2. **A path resolving to exactly one identity.** One identity means the
       reference is unambiguous. Several means the file holds several referents
       and the path says nothing about which one is meant -- so it is reported
       and dropped, never guessed.
    """
    by_uri = {identity.uri: identity for identity in identities}
    index = _path_index(identities)
    backticked, bare = _tokens_of(body)

    matches: dict[str, Match] = {}
    ambiguous: set[str] = set()

    for raw in _URI_RE.finditer(body):
        uri = raw.group(0).rstrip(".,;:")
        identity = by_uri.get(uri)
        if identity is not None:
            matches[identity.identity_id] = Match(
                identity_id=identity.identity_id,
                uri=identity.uri,
                tier=URI_TIER,
                evidence=uri,
            )

    for token in sorted(backticked | bare):
        if "/" not in token and "." not in token:
            continue
        candidates = _path_candidates(token, index)
        distinct = {candidate.identity_id: candidate for candidate in candidates}
        if len(distinct) == 1:
            identity = next(iter(distinct.values()))
            matches.setdefault(
                identity.identity_id,
                Match(
                    identity_id=identity.identity_id,
                    uri=identity.uri,
                    tier=PATH_TIER,
                    evidence=token,
                ),
            )
        elif len(distinct) > 1:
            ambiguous.add(token)

    return tuple(matches[key] for key in sorted(matches)), tuple(sorted(ambiguous))


def path_matches(
    paths: Sequence[str], identities: Sequence[IdentityView]
) -> tuple[tuple[Match, ...], tuple[str, ...]]:
    """`(matches, ambiguous paths)` for a caller that already holds paths.

    The **same rule** `structural_matches` applies to path tokens it finds in
    prose, exposed for harvest, whose input is a commit's files-touched list
    rather than a document body. One rule with two doors, never two rules: the
    thing that makes a path structural evidence is that it resolves to exactly
    one identity, and that must not mean something different depending on
    whether a human typed the path or git reported it.

    The ambiguity case is load-bearing here in a way it is not for prose. A
    commit touching `pyproject.toml` names every dependency identity extracted
    from that file, and binding a decision to all of them would attach one
    sentence about one library to forty. Several identities means the path says
    nothing about which one the commit was *about*, so it is reported and
    dropped.
    """
    index = _path_index(identities)
    matches: dict[str, Match] = {}
    ambiguous: set[str] = set()

    for token in sorted(set(paths)):
        candidates = _path_candidates(token, index)
        distinct = {candidate.identity_id: candidate for candidate in candidates}
        if len(distinct) == 1:
            identity = next(iter(distinct.values()))
            matches.setdefault(
                identity.identity_id,
                Match(
                    identity_id=identity.identity_id,
                    uri=identity.uri,
                    tier=PATH_TIER,
                    evidence=token,
                ),
            )
        elif len(distinct) > 1:
            ambiguous.add(token)

    return tuple(matches[key] for key in sorted(matches)), tuple(sorted(ambiguous))


def name_matches(
    body: str,
    identities: Sequence[IdentityView],
    *,
    exclude: Container[str] = frozenset(),
) -> tuple[Match, ...]:
    """Identities whose key appears as a token in the prose. **Suggestions only.**

    The match is deliberately strict about *token* and deliberately naive about
    *meaning*: a key must appear either inside backticks or bounded by
    non-word characters, and that is all this function claims. Whether the
    sentence is about the referent is the reviewer's judgement, which is the
    whole point of the tier.

    Args:
        exclude: Identity ids already bound or structurally matched. Suggesting
            what is already bound wastes the one resource this queue spends,
            which is a person's attention.
    """
    backticked, _ = _tokens_of(body)
    found: dict[str, Match] = {}

    for identity in identities:
        if identity.identity_id in exclude:
            continue
        leaf = identity.key_leaf
        if not leaf:
            continue
        if leaf in backticked:
            found[identity.identity_id] = Match(
                identity_id=identity.identity_id,
                uri=identity.uri,
                tier=NAME_TIER,
                evidence=f"`{leaf}`",
            )
            continue
        if re.search(rf"(?<!\w){re.escape(leaf)}(?!\w)", body):
            found[identity.identity_id] = Match(
                identity_id=identity.identity_id,
                uri=identity.uri,
                tier=NAME_TIER,
                evidence=leaf,
            )

    return tuple(found[key] for key in sorted(found))


def match_document(
    body: str,
    identities: Sequence[IdentityView],
    *,
    already_bound: Container[str] = frozenset(),
) -> MatchOutcome:
    """Both tiers over one document body, with the structural tier taking priority.

    An identity matched structurally is never also suggested: the evidence that
    auto-binds it is stronger than the evidence that would have queued it, and
    asking a human to confirm what the code already bound is how a queue teaches
    people to click confirm without reading.
    """
    structural, ambiguous = structural_matches(body, identities)
    bound_or_matched = {match.identity_id for match in structural}
    suggested = name_matches(
        body,
        identities,
        exclude=frozenset(bound_or_matched)
        | frozenset(
            identity.identity_id for identity in identities if identity.identity_id in already_bound
        ),
    )
    return MatchOutcome(structural=structural, suggested=suggested, ambiguous_paths=ambiguous)
