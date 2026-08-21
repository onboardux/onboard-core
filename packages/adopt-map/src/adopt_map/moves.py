"""Move detection -- and the three answers it can give, of which two write nothing.

A referent that changes address is the failure mode an inventory exists to
survive. `src/api/orders.py` becomes `src/api/v2/orders.py`, every path-derived
key changes, and a map with no move detection reports the entire module as gone
and its replacement as new -- taking every binding, every probe and every piece
of bound knowledge with it.

**The evidence a move is a move is the attribute digest.** Nothing else is
available: the file moved, so the path cannot say it, and the key changed, so the
key cannot say it. Two referents with byte-identical extracted attributes at the
same extractor version are the same referent, and that is the whole test.

**Three answers, and only one of them writes** (plan decision D6):

* exactly one disappeared and exactly one appeared share a digest -- a move, and
  `IdentityFacade.move()` records it with the old URI resolvable forever;
* nothing appeared with that digest -- **absence, which is not death.** Reported,
  never written. A file excluded by a new `.gitignore` rule, a repository mapped
  from a subdirectory, or an extractor that failed this run all look exactly like
  a deleted referent from here, and retirement belongs to Build 6, which owns the
  `change_event` a retirement would be reported through;
* several share a digest -- **ambiguity, reported and not guessed.** Three
  identical `__init__.py` config stubs genuinely have identical attributes, and
  picking a pairing would mint a permanent alias between two unrelated referents.
  There is no `conflict` row to write it to either: that table is Build 5's
  (B-09), so the report is the whole of the record.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from adopt_model._enums import IdentityKind

__all__ = [
    "MoveCandidate",
    "MoveOutcome",
    "ObservedIdentity",
    "StoredIdentity",
    "detect_moves",
]


@dataclass(frozen=True, slots=True)
class StoredIdentity:
    """An identity already in the store, as move detection needs to see it.

    Deliberately not `adopt_model.Identity`: the digest lives on the identity's
    creating *revision*, so neither table alone carries what a comparison needs,
    and joining them is the caller's job rather than a shape this module invents.
    """

    identity_id: str
    uri: str
    #: `identity_revision.source_version` from the creating revision -- the H5
    #: attribute digest, with the extractor version already mixed in.
    digest: str | None
    #: The head revision's status. A `moved` or `dead` identity is not a
    #: candidate: it has already been accounted for, and pairing it again would
    #: chain an alias onto an alias for no reason anyone could later read.
    status: str


@dataclass(frozen=True, slots=True)
class ObservedIdentity:
    """A referent this run saw, with everything `move()` needs to address it."""

    uri: str
    kind: IdentityKind
    namespace: str | None
    key: tuple[str, ...]
    digest: str


@dataclass(frozen=True, slots=True)
class MoveCandidate:
    """One recorded move: this identity is now at that URI."""

    identity_id: str
    from_uri: str
    to: ObservedIdentity


@dataclass(frozen=True, slots=True)
class MoveOutcome:
    """What detection concluded. Two of the three fields are reports, not writes."""

    moves: tuple[MoveCandidate, ...] = ()
    #: URIs in the store that this run did not see, and could not pair.
    absent: tuple[str, ...] = ()
    #: Digests where more than one referent disappeared or appeared. Rendered as
    #: the URIs involved, because a digest tells a reader nothing and the URIs
    #: tell them exactly which referents the tool declined to pair.
    ambiguous: tuple[tuple[str, ...], ...] = ()


def detect_moves(
    *,
    observed: Iterable[ObservedIdentity],
    stored: Sequence[StoredIdentity],
) -> MoveOutcome:
    """Pair disappeared referents with appeared ones, uniquely or not at all.

    Args:
        observed: Every referent this run saw, whether new or already known.
        stored: Every identity in scope **as it was before this run**. Read
            before the run, not after: after the run every new identity is in
            the store and nothing appears to have appeared.

    Returns:
        The moves to record, the absences to report, and the ambiguities to
        report. A caller writes only `moves`.
    """
    seen = {entry.uri: entry for entry in observed}
    live = [entry for entry in stored if entry.status not in {"moved", "dead"}]

    disappeared: dict[str, list[StoredIdentity]] = {}
    for entry in live:
        if entry.uri in seen or entry.digest is None:
            continue
        disappeared.setdefault(entry.digest, []).append(entry)

    known = {entry.uri for entry in stored}
    appeared: dict[str, list[ObservedIdentity]] = {}
    for uri, arrival in seen.items():
        if uri in known:
            continue
        appeared.setdefault(arrival.digest, []).append(arrival)

    return _pair(disappeared, appeared)


def _pair(
    disappeared: Mapping[str, list[StoredIdentity]],
    appeared: Mapping[str, list[ObservedIdentity]],
) -> MoveOutcome:
    moves: list[MoveCandidate] = []
    absent: list[str] = []
    ambiguous: list[tuple[str, ...]] = []

    for digest in sorted(disappeared):
        gone = sorted(disappeared[digest], key=lambda entry: entry.uri)
        arrived = sorted(appeared.get(digest, ()), key=lambda entry: entry.uri)
        if not arrived:
            absent.extend(entry.uri for entry in gone)
            continue
        if len(gone) != 1 or len(arrived) != 1:
            # Reported with **both sides** named. "Ambiguous move" alone sends a
            # reader looking; the URIs tell them whether they are looking at
            # three identical stubs or at a real pairing the tool could not make.
            ambiguous.append((*(entry.uri for entry in gone), *(entry.uri for entry in arrived)))
            continue
        moves.append(
            MoveCandidate(identity_id=gone[0].identity_id, from_uri=gone[0].uri, to=arrived[0])
        )

    return MoveOutcome(moves=tuple(moves), absent=tuple(sorted(absent)), ambiguous=tuple(ambiguous))
