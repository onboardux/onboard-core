"""The move rule -- contracts §4.3 rows 4-5, implementation spec §5.6, PRD F5.

A renamed referent should become an alias chain, not an orphan plus an unrelated
new identity. The rule that achieves it is **exact-digest matching with
declination on ambiguity** (B1-CR-08), and every clause of it exists to stop a
guess:

* **Exact, never similar.** No threshold, no distance, no ranking. `03` §5.6
  invariant 2 is *"never guesses on ambiguity"*, and a similarity score is a
  guess with a number attached.
* **Exactly one match, or no move.** Zero candidates and two candidates are the
  same answer here -- *we do not know* -- and both produce a `conflict` row for
  Build 3's resolver rather than a decision. PRD §8's autonomy matrix assigns
  ambiguous-move resolution to **"nobody in Build 1"**.
* **Null never matches null.** Two opaque identities carry no evidence that one
  became the other (`02` §4.3 row 6). `sourceversion.matches_semantically` holds
  that rule; this module does not re-implement it.
* **One pair, one entry** (`03` §5.6 invariant 4). A new identity that two
  disappeared identities both match is not a destination -- it is an ambiguity
  seen from the other side, and both are declined.
* **Scoped to one `(system, environment)`.** `PriorState.load` does the scoping,
  so a referent that appears in staging and vanishes from production is not a
  move (PRD F5.4) by construction rather than by a check here.

**This module decides and writes nothing.** It returns an outcome the writer
applies, for the reason the writer's own docstring gives: there is one path to
the store. That also makes the six cases in `05` S1.2 testable without a store.

**Idempotence is part of the rule, not an afterthought (B1-CR-46).** An identity
that disappeared stays disappeared, so a rule that reconsidered it every run
would write a fresh `conflict` row every run and CUJ-2's *"no other row
changes"* would fail on the third run and every run after. Two conditions keep
the rule quiet: an identity is examined only while its head revision is still
`active` -- a moved identity's head is not -- and only while it carries no
unresolved conflict of its own.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from adopt_map.prior import PriorState
from adopt_map.sourceversion import SourceVersion, matches_semantically

__all__ = ["Declination", "Move", "MoveOutcome", "Observation", "detect_moves"]

#: The only status from which a referent can be observed to have moved. A head
#: revision in any other state has already been ruled on.
_ACTIVE: Final[str] = "active"


@dataclass(frozen=True, slots=True)
class Observation:
    """One identity as this run saw it."""

    uri: str
    identity_id: str
    identity_kind: str
    source_version: SourceVersion
    is_new: bool


@dataclass(frozen=True, slots=True)
class Move:
    """An unambiguous rename: one old identity, one new one."""

    from_identity_id: str
    from_uri: str
    to_identity_id: str
    to_uri: str


@dataclass(frozen=True, slots=True)
class Declination:
    """A disappearance the rule refused to resolve, and how many candidates it had."""

    identity_id: str
    uri: str
    candidates: int


@dataclass(frozen=True, slots=True)
class MoveOutcome:
    """What the rule decided. Both tuples are ordered by URI, so a run's report
    and its `conflict` rows come out in the same order on any machine."""

    moves: tuple[Move, ...] = ()
    declinations: tuple[Declination, ...] = ()


def detect_moves(prior: PriorState, observations: Sequence[Observation]) -> MoveOutcome:
    """Decide which disappeared identities moved -- contracts §4.3 rows 4-5.

    Args:
        prior: The state a previous run left, already scoped to one
            `(system, environment)`.
        observations: Every identity this run saw, new and returning.

    Returns:
        The moves to record and the ambiguities to declare.
    """
    seen = {observation.uri for observation in observations}
    candidates = [observation for observation in observations if observation.is_new]

    # Pass one: what each disappeared identity matches.
    matched: dict[str, list[Observation]] = {}
    for entry in prior.all():
        identity = entry.identity
        if identity.uri in seen or entry.status != _ACTIVE:
            continue
        if prior.is_open_conflict(identity.id):
            continue

        version = entry.source_version
        matched[identity.uri] = (
            []
            if version is None
            else [
                candidate
                for candidate in candidates
                if candidate.identity_kind == identity.identity_kind
                and matches_semantically(version, candidate.source_version)
            ]
        )

    # Pass two: a candidate claimed by two disappeared identities is an ambiguity
    # seen from the far side, so neither claim survives it. Counting before
    # deciding is what makes the rule symmetric -- deciding in one pass would let
    # whichever URI sorted first take the candidate and silently win.
    claims: dict[str, int] = {}
    for hits in matched.values():
        if len(hits) == 1:
            claims[hits[0].identity_id] = claims.get(hits[0].identity_id, 0) + 1

    moves: list[Move] = []
    declinations: list[Declination] = []
    for uri in sorted(matched):
        found = prior.get(uri)
        assert found is not None  # noqa: S101 -- `matched` was built from `prior`
        hits = matched[uri]
        if len(hits) == 1 and claims.get(hits[0].identity_id, 0) == 1:
            moves.append(
                Move(
                    from_identity_id=found.identity.id,
                    from_uri=uri,
                    to_identity_id=hits[0].identity_id,
                    to_uri=hits[0].uri,
                )
            )
        else:
            declinations.append(
                Declination(identity_id=found.identity.id, uri=uri, candidates=len(hits))
            )

    return MoveOutcome(moves=tuple(moves), declinations=tuple(declinations))
