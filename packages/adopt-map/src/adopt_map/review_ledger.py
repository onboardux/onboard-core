"""`.adopt/review_ledger.jsonl` -- append-only, cross-run, and the only home of M5.

`04` §6: *"Outcomes append to `.adopt/review_ledger.jsonl`, which survives runs
and is the source of truth for the rewrite rate."* Two properties do all the work
and both are load-bearing rather than tidy:

**Append-only, because the measurement is the mechanism.** `04` §6 refuses
`--approve` on a modified file *"without this, reviewers silently patch and
approve, and the ADR-0.1 >40% reversal trigger can never fire -- the measurement
is the point of the mechanism"*. A ledger a later run could rewrite would let the
same pressure win one layer down: a rewrite recorded on Tuesday and edited out on
Friday leaves an approval rate nobody can audit. There is no update path and no
delete path in this module, which is the same argument `02` §6 makes about
`*_revision` tables, at a much smaller scale.

**Cross-run, because ADR-0.1's trigger is a rate over a population.** A per-run
file would answer *"how did today go"*, and the reversal trigger asks *"is the
glue approach working"*. That is why the ledger sits at `.adopt/review_ledger.jsonl`
and not under `.adopt/out/`, which every run regenerates -- and why
`docs/pack/OPEN-DECISIONS.md` **OD-15** takes no position on it: whatever a firm
decides about committing quarantine artefacts, deleting this file destroys the
only evidence that would fire the trigger.

**M5 is computed here rather than in S1.8 (B1-CR-86).** `05` S1.8 asks for
*"M5 glue rewrite rate from `review_ledger.jsonl`"* and no S1.7 checkbox builds
it, so the metric would have arrived as somebody's arithmetic over a file. Its
predicate has a detail arithmetic gets wrong -- `01` §6 M5 is over the **latest
outcome per extractor**, so an extractor rewritten once and approved after the
rewrite counts once, as approved.
"""

import json
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from adopt_obs import Clock, SystemClock, format_timestamp, get_logger

__all__ = [
    "LEDGER_FILENAME",
    "REVIEW_OUTCOMES",
    "ReviewEntry",
    "append",
    "latest_by_extractor",
    "m5_rewrite_rate",
    "read_all",
]

_log = get_logger(__name__)

#: Where the ledger lives, relative to the project's `.adopt/` directory.
LEDGER_FILENAME: Final[str] = "review_ledger.jsonl"

#: `01` §6 M5's vocabulary. `quarantined` is the entry a pass writes when a module
#: reaches the review queue; the other three are what a human did about it.
#: `pending` is **not** a stored outcome -- M5's denominator is
#: `outcome != 'pending'`, and a row that could carry it would be a row somebody
#: could park a decision in forever.
REVIEW_OUTCOMES: Final[tuple[str, ...]] = ("quarantined", "approved", "rewritten", "rejected")

#: The outcomes that mean a human has decided. M5's denominator.
_DECIDED: Final[frozenset[str]] = frozenset({"approved", "rewritten", "rejected"})


@dataclass(frozen=True, slots=True)
class ReviewEntry:
    """One line of the ledger.

    `module_sha256` is what makes the approve-refusal auditable after the fact: a
    reviewer comparing two entries can see that the bytes approved are the bytes
    generated, without trusting that the refusal was working on the day.
    """

    ts: str
    extractor_id: str
    outcome: str
    module_sha256: str
    prompt_ref: str | None = None
    adapter: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in REVIEW_OUTCOMES:
            message = f"{self.outcome!r} is not one of {REVIEW_OUTCOMES}"
            raise ValueError(message)


def append(ledger_path: Path, entry: ReviewEntry) -> None:
    """Add one line. The only write path in this module.

    Opens in append mode and writes one `json.dumps` line with sorted keys, so two
    processes appending concurrently interleave lines rather than corrupting one,
    and so a diff of the file is a diff of decisions.
    """
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(entry), sort_keys=True, separators=(",", ":"))
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    _log.info("review_ledger_appended", extractor=entry.extractor_id, outcome=entry.outcome)


def read_all(ledger_path: Path) -> tuple[ReviewEntry, ...]:
    """Every entry, in file order. A missing ledger is empty, never an error."""
    if not ledger_path.is_file():
        return ()
    return tuple(_parse(ledger_path))


def _parse(ledger_path: Path) -> Iterator[ReviewEntry]:
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        yield ReviewEntry(**payload)


def latest_by_extractor(entries: Sequence[ReviewEntry]) -> dict[str, ReviewEntry]:
    """The last entry per extractor, in file order. `01` §6 M5's *"latest outcome"*."""
    latest: dict[str, ReviewEntry] = {}
    for entry in entries:
        latest[entry.extractor_id] = entry
    return latest


def m5_rewrite_rate(entries: Sequence[ReviewEntry]) -> float | None:
    """`reviews[outcome='rewritten'] / reviews[outcome != 'pending']`, latest per extractor.

    Returns **`None` when nothing has been decided**, never `0.0`. An empty ledger
    and a ledger in which every reviewer approved are different facts, and
    reporting the first as a perfect score is Build 0's CR-51 finding -- *"an
    undefined ratio reports `null` and never `1.0`"* -- arriving at the metric
    ADR-0.1 actually watches. A zero here would read as "the glue approach is
    working" on a run where nobody has reviewed anything.
    """
    decided = [
        entry for entry in latest_by_extractor(entries).values() if entry.outcome in _DECIDED
    ]
    if not decided:
        return None
    rewritten = sum(1 for entry in decided if entry.outcome == "rewritten")
    return rewritten / len(decided)


def now_iso(clock: Clock | None = None) -> str:
    """RFC 3339 UTC with `Z`, from Build 0's injectable clock (`02` §1.5).

    Never `datetime.now()`: `03` §5 bans it outright, and a ledger is exactly the
    artefact where an untestable timestamp would go unnoticed for the longest.
    """
    return format_timestamp((clock or SystemClock()).now())
