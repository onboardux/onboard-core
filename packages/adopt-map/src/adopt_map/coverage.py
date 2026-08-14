"""Write the cache; trust only the function -- `01` F10, `03` §5.7.

`recompute_coverage()` is the authority and `identity.covered_cache` is a cache
rebuilt **from** it, never the reverse. Every coverage figure this build prints
anywhere comes from the recompute result, and `02` §9.2's `coverage.source` is
the literal string `"recompute"` so a reader of the artifact can check that
without reading our code.

**This module does not write the cache itself, and that is the design.**
`no-covered-cache-write` admits `packages/adopt-coverage` and nothing else, so the
rebuild goes through Build 0's `rebuild_cache(writer, result)` -- a function whose
signature takes a `CoverageResult` and performs no store read of its own,
precisely so there is no expression in which the cache could influence the value
written. Build 1 supplies the authority and the store handle and owns neither
half of the statement.

**Three findings this module records rather than works around.**

*B1-CR-62 -- every identity `adopt map` writes is uncovered, and it is not this
build's to fix.* Build 0's coverage rule needs six inputs, and input 4 is *"an
applicable audience and environment"* -- realized as `audience_count == 0`
blocking the identity. `audience_tag` is on `00` §5's **never writes** list for
Build 1. So the ratio is **0.0 on every run, always**, `01` §6's M1 target of
`≥ 0.60` cannot be moved by anything in this build, and `05` S1.4's
`jq '.coverage.ratio' >= 0.60` is unreachable as written. Measured, not
theorised: a fresh run reports `audience_or_environment_inapplicable` on every
identity. Build 1 does the one thing it may -- it reports the ratio honestly and
names the blocking reason on the first screen, so the run says *why* it is zero
rather than printing a bare zero. Whether Build 1 should write `audience_tag`
contradicts `00` §5 and is an owner decision; whether M1 belongs to Build 5 is a
PRD question. Both are flagged, neither is assumed.

*B1-CR-59 -- a null cache is an absent cache, not a disagreeing one.*
`recompute_coverage` reports a disagreement whenever the stored `covered_cache`
differs from the recomputed verdict, and a **newly created** identity carries the
column's `false` default with `covered_cache_at` still null. `covered_cache_at IS
NULL` is the discriminator: the cache was never written, so there is nothing for
it to disagree with. It is read to **classify**, never to decide coverage, which
is the line `03` §5.7 invariant 1 draws.

**This guard is currently unreachable through a real run, and that is stated
rather than hidden.** Because of B1-CR-62 every identity is uncovered, so the
`false` default *agrees* and a first run produces no disagreements at all. The
split becomes load-bearing the moment anything makes a surface identity covered
-- Build 5 supplying audience tags is the obvious trigger -- at which point a
first run would otherwise alarm on every row. It is kept, and its test plants the
cold state directly rather than asserting a count that is structurally zero
today: a guard proven only by a state the build cannot reach is a guard nobody
has seen work.

*B1-CR-60 -- drift on a committed run does not take exit 5.* `02` §1.4 maps
`MAP_COVERAGE_CACHE_DRIFT` to exit 5 by elimination, and `02` §8 states exit 5's
guarantee as *"nothing written; prior state intact"*. But `01` F10.3 and `03`
§5.7 both put the comparison at **run end**, after the transaction committed --
so exiting 5 there would be a false claim about the store, made by the code whose
entire job is not making false claims about the store. Contracts outrank the PRD
and §8's guarantee is the narrower statement, so a run-end drift **alarms, prints
and lands in both artifacts** while the run exits on what its write earned. The
code keeps its exit-5 mapping for the read-only path -- `adopt surface coverage`,
where "nothing written" is true by construction.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from adopt_const import COVERAGE_ALARM_SAMPLE_MAX
from adopt_coverage import CacheWriter, CoverageResult, Disagreement, recompute_coverage
from adopt_coverage.records import CoverageRecords
from adopt_identity import parse_uri
from adopt_obs import ErrorCode, get_logger

__all__ = ["COVERAGE_SOURCE", "CoverageReport", "report_coverage"]

_log = get_logger(__name__)

#: `02` §9.2's `coverage.source`. A literal in the artifact so a reader can check
#: the authority without reading our code -- and a constant here so no emitter
#: can spell it differently.
COVERAGE_SOURCE = "recompute"


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """`02` §9.2's `coverage` block, plus what the run report needs.

    `drift` is the alarming set and `cold` is the merely-uninitialised one. They
    are separate fields rather than one count because collapsing them is exactly
    the defect B1-CR-59 records: a first run would report maximum drift and the
    signal would be useless from the day it shipped.
    """

    discovered: int
    covered: int
    drift: tuple[Disagreement, ...] = ()
    cold: int = 0
    by_kind: dict[str, tuple[int, int]] = field(default_factory=dict)
    rows_written: int = 0
    #: `reason -> how many identities it blocked` (B1-CR-62). Carried because a
    #: bare `0.0` is the one coverage figure a reader cannot act on: `covered/
    #: discovered` says *how much*, and only the reasons say *what to do about
    #: it*. Build 0's `COVERAGE_REASONS` are the closed vocabulary.
    blocked_by: dict[str, int] = field(default_factory=dict)

    @property
    def source(self) -> str:
        return COVERAGE_SOURCE

    @property
    def ratio(self) -> float:
        """Covered over discovered. Zero identities is a ratio of 0.0, not 1.0.

        An empty scope has covered nothing. Reporting 1.0 would say a system with
        no identities is perfectly covered, which is the shape of claim `01` §1.6
        exists to refuse.
        """
        return self.covered / self.discovered if self.discovered else 0.0

    @property
    def cache_agreement(self) -> bool:
        """`02` §9.2's `coverage.cache_agreement`."""
        return not self.drift

    def as_report_block(self) -> dict[str, object]:
        """The `02` §9.2 shape, exactly."""
        return {
            "source": self.source,
            "discovered": self.discovered,
            "covered": self.covered,
            "ratio": round(self.ratio, 4),
            "cache_agreement": self.cache_agreement,
        }

    def headline(self) -> str:
        """The first-screen coverage line (`02` §9.1 item 5)."""
        percentage = f"{self.ratio * 100:.1f}%"
        agreement = (
            "cache agreement OK"
            if self.cache_agreement
            else (
                f"CACHE DRIFT on {len(self.drift)} identities -- defect signal, not repaired silently"
            )
        )
        return f"recompute {self.covered}/{self.discovered} = {percentage} · {agreement}"

    def blockers(self) -> tuple[str, ...]:
        """Why the uncovered identities are uncovered, commonest first.

        **B1-CR-62 -- a ratio of 0.0 with no reason is unactionable.** Every
        identity `adopt map` writes is blocked on
        `audience_or_environment_inapplicable` today, because Build 0's coverage
        rule requires at least one applicable `audience_tag` on the bound item
        and `00` §5 puts `audience_tag` on Build 1's **never writes** list. So
        the honest first screen is not *"coverage 0.0%"* -- it is *"coverage
        0.0%, and here is the one input nobody has supplied yet"*.
        """
        return tuple(
            f"{reason} ({count})"
            for reason, count in sorted(
                self.blocked_by.items(), key=lambda item: (-item[1], item[0])
            )
        )


def report_coverage(
    records: CoverageRecords,
    cache: CacheWriter | None,
    *,
    system_id: str,
    environment_id: str | None,
    rebuild: bool = True,
) -> CoverageReport:
    """Recompute, classify the disagreements, rebuild the cache, report.

    The order is `03` §5.7's, and the classification between them is B1-CR-59's.
    A rebuild that ran *before* the comparison would make the comparison vacuous:
    it would be comparing the authority against a cache it had just written from
    the authority, which agrees by construction and would have detected nothing.

    Args:
        records: The read port. Build 0's `recompute_coverage` takes it first.
        cache: The store to rebuild the cache through, or `None` under
            `--dry-run`, where nothing is written and the comparison still runs.
        system_id: The run's system.
        environment_id: The run's environment. **Never `None` from a map run** --
            `01` F6.3 forbids a cross-environment total -- though the port admits
            it for `store doctor`'s whole-store sweep.
        rebuild: Whether to write the cache. `False` reports without writing.

    Returns:
        The report every printed figure in this run derives from.
    """
    result = recompute_coverage(records, system_id, environment_id)
    stamps = {
        row.id: row.covered_cache_at
        for row in records.identities_in_scope(system_id=system_id, environment_id=environment_id)
    }

    cold = 0
    drift: list[Disagreement] = []
    for disagreement in result.disagreements:
        if stamps.get(disagreement.identity_id) is None:
            cold += 1
        else:
            drift.append(disagreement)

    if drift:
        # ALARM, not warn: `01` F10.3 makes this a defect signal that must page.
        # Identity **ids** travel and URIs do not -- an id is minted by us and
        # carries no client-derived text, which is Build 0's own rule for this
        # exact alarm.
        _log.alarm(
            "map_coverage_cache_drift",
            code=str(ErrorCode.MAP_COVERAGE_CACHE_DRIFT),
            system_id=system_id,
            environment_id=environment_id,
            drift_count=len(drift),
            drift_sample=[entry.identity_id for entry in drift[:COVERAGE_ALARM_SAMPLE_MAX]],
        )

    rows_written = 0
    if rebuild and cache is not None:
        # Rebuilt **after** the comparison and after the alarm. Leaving a
        # known-wrong cache in place would be worse, and rebuilding is not
        # "silently self-healing" (`03` §5.7 invariant 2) when the drift has
        # already alarmed and is already in the run report -- the evidence
        # survives the repair, which is the property that matters.
        from adopt_coverage import rebuild_cache

        rows_written = rebuild_cache(cache, result)

    return CoverageReport(
        discovered=len(result.identities),
        covered=result.covered,
        drift=tuple(drift),
        cold=cold,
        by_kind=_by_kind(result),
        rows_written=rows_written,
        blocked_by=_blocked_by(result),
    )


def _blocked_by(result: CoverageResult) -> dict[str, int]:
    """`reason -> count` over every uncovered identity -- B1-CR-62.

    Counted per reason rather than per identity, because an identity can be
    blocked on several and an operator fixes a **reason**. Build 0 already
    exports the closed vocabulary as `COVERAGE_REASONS`, so nothing here invents
    a string.
    """
    counts: dict[str, int] = {}
    for entry in result.identities:
        for reason in entry.reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _by_kind(result: CoverageResult) -> dict[str, tuple[int, int]]:
    """`kind -> (covered, discovered)` for `adopt surface coverage --by-kind`.

    The kind is parsed out of the URI rather than joined from the row, because
    `IdentityCoverage` carries the URI and the URI is canonical: `parse_uri`
    round-trips it, so a kind read this way is the kind the identity was minted
    with and not a second copy that could drift.
    """
    counts: dict[str, tuple[int, int]] = {}
    for entry in result.identities:
        kind = parse_uri(entry.uri).kind
        covered, discovered = counts.get(kind, (0, 0))
        counts[kind] = (covered + (1 if entry.covered else 0), discovered + 1)
    return dict(sorted(counts.items()))


def printed_figures(reports: Sequence[CoverageReport]) -> tuple[str, ...]:
    """Every coverage line this run would print, from the recompute result only.

    Exists so the `05` S1.3 source scan has a single place to point at: if a
    coverage figure is printed anywhere in this build, it came through a
    `CoverageReport`, and a `CoverageReport` is only ever constructed by
    `report_coverage` above.
    """
    return tuple(report.headline() for report in reports)
