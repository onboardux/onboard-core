"""`adopt gaps` -- identities minus covered knowledge, ranked.

The Coverage Check (§1.1 row 7) in its report form. Build 4 adds dispositions;
this build reads.

**Existence is derived, never stored.** `recompute_coverage` is the authority on
whether an identity is covered, and this module only orders what it returned. A
gap table would be a second answer to the same question, and the first thing
that happens to a second answer is that it disagrees with the first.

**The ranking is deterministic and has no tunable.** More blocked reasons first
(an identity with no binding *and* no boundary needs more work than one waiting
on an audience tag), then kind, then URI. Two runs over one store produce one
order, which is what lets an FDE work down the list across a week without it
reshuffling underneath them.
"""

import datetime as _dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from adopt_identity import parse_uri

__all__ = [
    "GAP_KEY_SEPARATOR",
    "OPEN_DISPOSITION",
    "ConflictRow",
    "CoverageEntry",
    "Gap",
    "OpenConflict",
    "gap_key_for",
    "rank_conflicts",
    "rank_gaps",
]

#: Separates the three parts of a `gap_key`. A pipe because it cannot occur in a
#: canonical URI: every segment is percent-encoded by the builder, so a key can
#: always be split back into its parts unambiguously.
GAP_KEY_SEPARATOR: Final[str] = "|"

#: The one `disposition` value that means nobody has decided anything yet.
#: A conflict is **never resolved away** (v6.1 Bet 4) -- dispositioning
#: records a human decision beside it and Build 5 writes none, so every row
#: this build creates carries this value and stays visible until somebody acts.
OPEN_DISPOSITION: Final[str] = "open"


def gap_key_for(uri: str) -> str:
    """The deterministic key a disposition is recorded against.

    v6.1 §6 Build 4 defines it as **identity URI + environment + kind**. The URI
    already encodes both of the other two, so this is redundant by construction
    -- and it is written the way the spec words it rather than shortened to the
    URI alone, because the key is what a human copies out of a report and pastes
    into `--ack`, and a key that names its environment and kind is one they can
    check by eye before disposing of it.

    Deterministic across runs and across machines: the same identity always
    produces the same key, which is what lets a disposition survive
    regeneration. An unparseable URI contributes `?` for the parts it could not
    yield rather than raising -- a gap whose URI is malformed is still a gap,
    and refusing to key it would remove it from the report entirely.
    """
    try:
        parsed = parse_uri(uri)
    except Exception:
        return GAP_KEY_SEPARATOR.join((uri, "?", "?"))
    return GAP_KEY_SEPARATOR.join((uri, parsed.environment, parsed.kind))


class CoverageEntry(Protocol):
    """One `IdentityCoverage` verdict, structurally.

    Declared rather than imported: `adopt_coverage` owns the shape and this
    package neither adds to it nor depends on the distribution that computes it.
    """

    @property
    def identity_id(self) -> str: ...

    @property
    def uri(self) -> str: ...

    @property
    def covered(self) -> bool: ...

    @property
    def reasons(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class Gap:
    """One uncovered identity, with why."""

    identity_id: str
    uri: str
    kind: str
    reasons: tuple[str, ...]

    @property
    def reason_count(self) -> int:
        return len(self.reasons)

    @property
    def gap_key(self) -> str:
        """The key a disposition is recorded against (Build 4)."""
        return gap_key_for(self.uri)


def _kind_of(uri: str) -> str:
    """The identity kind, for grouping. Unparseable rows sort under `?`."""
    try:
        return parse_uri(uri).kind
    except Exception:
        return "?"


def rank_gaps(entries: Sequence[CoverageEntry]) -> tuple[Gap, ...]:
    """Every uncovered identity, worst first.

    Covered identities are dropped rather than listed with an empty reason
    tuple: the report is the elicitation queue, and a queue that includes the
    work already done is a queue people scroll past.
    """
    gaps = [
        Gap(
            identity_id=entry.identity_id,
            uri=entry.uri,
            kind=_kind_of(entry.uri),
            reasons=tuple(entry.reasons),
        )
        for entry in entries
        if not entry.covered
    ]
    return tuple(sorted(gaps, key=lambda gap: (-gap.reason_count, gap.kind, gap.uri)))


@dataclass(frozen=True, slots=True)
class OpenConflict:
    """One recorded disagreement between what the store says and what a probe saw.

    Bet 4's deliverable, in the shape a report renders. The identity is named by
    **URI** rather than by id because the reader is a human deciding whether the
    runbook or the system is wrong, and an id tells them nothing.

    `actual_revision_id` is deliberately absent from this view even though the
    column exists: Build 5 writes no knowledge from probe output, so it is always
    NULL, and rendering an always-empty column would suggest the tool failed to
    fill it rather than that it refused to invent it.
    """

    identity_id: str
    uri: str
    kind: str
    intent_revision_id: str | None
    detected_at: _dt.datetime


class ConflictRow(Protocol):
    """One `conflict` row, structurally.

    Declared rather than imported for `CoverageEntry`'s reason: the generated
    model belongs to `adopt_model`, and this package renders reports rather than
    depending on how a row is realized.
    """

    @property
    def identity_id(self) -> str: ...
    @property
    def intent_revision_id(self) -> str | None: ...
    @property
    def detected_at(self) -> _dt.datetime: ...
    @property
    def disposition(self) -> str: ...


def rank_conflicts(
    rows: Sequence[ConflictRow], uris: Mapping[str, str]
) -> tuple[OpenConflict, ...]:
    """Open conflicts, oldest first, for the identities in `uris`.

    Args:
        rows: Every `conflict` row read from the store.
        uris: identity id -> canonical URI, for the identities in scope. A
            conflict whose identity is **not** in this mapping is dropped: it
            belongs to another system or environment, and a report that listed
            it would be reporting on a scope nobody asked about.

    Returns:
        Only `open` conflicts. A dispositioned one has been decided and is no
        longer something the reader must act on -- and unlike a gap, a conflict
        is never re-derived, so the row *is* the record.

        Oldest first, which is the opposite of most listings and deliberate: a
        disagreement between documentation and behaviour that has been open for
        a month is the one that has been quietly wrong for a month.
    """
    open_rows = [
        OpenConflict(
            identity_id=row.identity_id,
            uri=uris[row.identity_id],
            kind=_kind_of(uris[row.identity_id]),
            intent_revision_id=row.intent_revision_id,
            detected_at=row.detected_at,
        )
        for row in rows
        if str(row.disposition) == OPEN_DISPOSITION and row.identity_id in uris
    ]
    return tuple(
        sorted(
            open_rows,
            key=lambda conflict: (
                conflict.detected_at,
                conflict.uri,
                conflict.intent_revision_id or "",
            ),
        )
    )
