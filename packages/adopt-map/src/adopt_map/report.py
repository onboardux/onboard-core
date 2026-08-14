"""The run result every emitter reads, `run_report.json`, and the telemetry line.

**Emitters read this, never the store** (`03` §5.9 invariant 3). A store failure
cannot silently change what a run reports, because the report is a projection of
what the run *did* rather than a second query against what it wrote. That also
makes every emitter testable without a database.

**No client source content, ever, and absolute paths are stripped here** (`02`
§9.3, `03` §5.9 invariant 4). `RunResult` holds the tree root because the run
needs it; `as_report()` never emits it. Paths that do travel are repo-relative
because `FileEntry` and `SourceRef` have held them that way since they were read
-- there is nothing to strip and nothing to forget to strip, and this module is
the backstop rather than the mechanism.

**`peak_rss_bytes` is `None` where the platform cannot answer.** `resource` is
POSIX-only. Reporting a zero would put a number in an NFR field that no
measurement produced, and `01` N11's whole point is that the memory ceiling is
measured rather than asserted.
"""

import datetime as _dt
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from adopt_const import SURFACE_REPORT_VERSION
from adopt_map.confidence import Degradation
from adopt_map.coverage import CoverageReport
from adopt_map.fileindex import FileIndex
from adopt_map.minting import mint
from adopt_map.scheduler import ExtractorOutcome
from adopt_map.schemas.surface import FactRelation, SurfaceFact
from adopt_map.scope_resolve import ResolvedScope
from adopt_map.writer import FactBatch, SurfaceWriteResult
from adopt_obs import format_timestamp

__all__ = [
    "RUN_REPORT_NAME",
    "TELEMETRY_NAME",
    "MintedFact",
    "RunResult",
    "peak_rss_bytes",
    "write_run_report",
]

#: Decimal places for a duration and for a ratio in an artifact. Display
#: precision, not a tunable: rounding a timing to milliseconds is how the
#: number is *written*, and no evidence would ever revise it.
_SECONDS_PRECISION: Final[int] = 3  # const-sync: ok -- display precision
_RATIO_PRECISION: Final[int] = 4  # const-sync: ok -- display precision

RUN_REPORT_NAME: Final[str] = "run_report.json"
TELEMETRY_NAME: Final[str] = "telemetry.jsonl"


def peak_rss_bytes() -> int | None:
    """Peak resident set size, or `None` where the platform cannot answer.

    `resource` is POSIX-only, so this returns `None` on Windows. The paired
    `type: ignore` codes are both needed and neither is redundant: on Windows the
    module resolves with no attributes (`attr-defined`), and on Linux it resolves
    fully so the first code would itself be unused (`unused-ignore`). A single
    code would make `mypy --strict` fail on one of the two platforms CI checks.
    """
    try:
        import resource
    except ImportError:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF)  # type: ignore[attr-defined, unused-ignore]
    # `ru_maxrss` is kilobytes on Linux and bytes on macOS. The kilobyte reading
    # is the one that would silently understate by 1024x, so it is the one
    # normalised rather than assumed.
    maximum = int(usage.ru_maxrss)
    # const-sync: ok -- kilobytes to bytes. A unit, not a decision anybody may revise.
    return maximum if sys.platform == "darwin" else maximum * 1024


@dataclass(frozen=True, slots=True)
class MintedFact:
    """One emitted fact with the URI the framework minted for it.

    Paired here rather than carried on the fact, because `SurfaceFact` has no
    `uri` field and that absence is the mechanism behind environment isolation
    (`02` §7). The pairing is made by the emitters from `(scope, fact)` using the
    same `mint()` the writer used, so the two cannot disagree.
    """

    uri: str
    fact: SurfaceFact
    extractor: str
    method: str
    confidence: float


@dataclass(frozen=True, slots=True)
class RunResult:
    """Everything one `adopt map` invocation produced. The emitters' only input."""

    run_id: str
    adopt_version: str
    generated_at: _dt.datetime
    resolved: ResolvedScope
    index: FileIndex
    batches: tuple[FactBatch, ...]
    outcomes: tuple[ExtractorOutcome, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    degradations: tuple[Degradation, ...] = ()
    truncated_families: tuple[str, ...] = ()
    write_result: SurfaceWriteResult | None = None
    coverage: CoverageReport | None = None
    network_attempted: int = 0
    client_imports_attempted: int = 0
    stage1_elapsed_s: float = 0.0
    total_elapsed_s: float = 0.0
    exit_code: int = 0
    timeout_enforced: bool = True
    dry_run: bool = False
    peak_rss: int | None = field(default=None)

    def minted(self) -> tuple[MintedFact, ...]:
        """Every fact with its URI, **byte-sorted by URI** (`02` §10 C15).

        Sorted here once so `surface.json`, the markdown inventory and both
        diagrams share one order. A per-emitter sort is three places for the
        order to drift, and `01` N4 makes the order part of what determinism
        means.
        """
        from adopt_map.confidence import confidence_for

        minted = [
            MintedFact(
                uri=mint(self.resolved.scope, fact),
                fact=fact,
                extractor=batch.manifest.id,
                method=batch.manifest.method,
                confidence=confidence_for(batch.manifest.method),
            )
            for batch in self.batches
            for fact in batch.facts
        ]
        return tuple(sorted(minted, key=lambda entry: entry.uri.encode("utf-8")))

    def relation_target_uri(self, relation: FactRelation) -> str:
        """The URI a relation points at, minted from **this run's** scope.

        Minted here for the same reason the writer mints it there: an extractor
        supplies `(kind, namespace, local_key)` and never a URI, so a target can
        only ever land inside this run's environment (`02` §7 obligation 6). An
        edge cannot cross an environment boundary because there is no expression
        in which it could name another one.
        """
        probe = SurfaceFact(
            identity_kind=relation.target_kind,
            namespace=relation.target_namespace,
            local_key=relation.target_local_key,
            title=relation.target_local_key,
        )
        return mint(self.resolved.scope, probe)

    def counts_by_kind(self) -> dict[str, int]:
        """`02` §9.2's `counts_by_kind`, sorted by kind."""
        counts: dict[str, int] = {}
        for batch in self.batches:
            for fact in batch.facts:
                counts[fact.identity_kind] = counts.get(fact.identity_kind, 0) + 1
        return dict(sorted(counts.items()))

    def outside_vcs(self) -> tuple[str, ...]:
        """The URIs of behaviour-bearing settings that live outside version
        control -- `01` F8.6, and the first screen's item 7."""
        return tuple(entry.uri for entry in self.minted() if entry.fact.outside_vcs)

    def total_facts(self) -> int:
        return sum(len(batch.facts) for batch in self.batches)

    def extractor_timings(self) -> list[dict[str, object]]:
        """Per-extractor timings and fact counts -- `02` §9.3."""
        return [
            {
                "extractor": outcome.extractor_id,
                "status": outcome.status,
                "facts": len(outcome.facts),
                "elapsed_s": round(outcome.elapsed_s, _SECONDS_PRECISION),
                "fallback": outcome.fallback,
            }
            for outcome in self.outcomes
        ]

    def as_report(self) -> dict[str, object]:
        """`run_report.json` -- `02` §9.3. **No client source content.**"""
        write = self.write_result
        return {
            "report_version": SURFACE_REPORT_VERSION,
            "run_id": self.run_id,
            "generated_at": format_timestamp(self.generated_at),
            "adopt_version": self.adopt_version,
            "scope": {
                "firm_id": self.resolved.firm_id,
                "engagement_id": self.resolved.engagement_id,
                "system_id": self.resolved.system_id,
                "environment_id": self.resolved.environment_id,
                "environment_name": self.resolved.environment_slug,
            },
            "system": {"archetype": self.resolved.archetype, "tier": self.resolved.tier},
            "tree": {
                # The root is deliberately absent: it is the one absolute path a
                # run holds, and `02` §9.3 strips absolute paths at emission.
                "files_indexed": len(self.index.files),
                "files_discovered": self.index.discovered,
                "sampled": self.index.sampled,
                "skipped_large": self.index.skipped_large,
                "skipped_binary": self.index.skipped_binary,
                "vcs_revision": self.index.vcs_revision,
            },
            "extractors": self.extractor_timings(),
            "extractors_skipped": [
                {"extractor": name, "reason": reason} for name, reason in self.skipped
            ],
            "revisions_written": None if write is None else dict(write.revisions_written),
            "counts_by_kind": self.counts_by_kind(),
            "coverage": None if self.coverage is None else self.coverage.as_report_block(),
            "cache_agreement": None if self.coverage is None else self.coverage.cache_agreement,
            "degradations": [entry.as_report_row() for entry in self.degradations],
            "truncated_families": list(self.truncated_families),
            "gaps": []
            if write is None
            else [
                {"identity_uri": gap.identity_uri, "reason": gap.reason, "detail": gap.detail}
                for gap in write.gaps
            ],
            "network_attempted": self.network_attempted,
            "client_imports_attempted": self.client_imports_attempted,
            "peak_rss_bytes": self.peak_rss,
            "timings": {
                "stage1_elapsed_s": round(self.stage1_elapsed_s, _SECONDS_PRECISION),
                "total_elapsed_s": round(self.total_elapsed_s, _SECONDS_PRECISION),
                "timeout_enforced": self.timeout_enforced,
            },
            "agent": {"calls": 0, "cost_usd": 0.0, "gate_skip_reason": "agent_pass_not_built"},
            "dry_run": self.dry_run,
            "exit_code": self.exit_code,
        }

    def as_telemetry(self) -> dict[str, object]:
        """One JSONL line: the signals `03` §10 names, and nothing identifying.

        Local only. `01` §1.6 and `03` §10 make OSS mode zero-telemetry
        **permanently** -- there is no opt-in switch to add later, and nothing
        here is ever transmitted. The file exists so an operator who wants a
        trend has one without us building a collector.
        """
        write = self.write_result
        return {
            "ts": format_timestamp(self.generated_at),
            "run_id": self.run_id,
            "revisions_written": 0 if write is None else sum(write.revisions_written.values()),
            "coverage_ratio": None
            if self.coverage is None
            else round(self.coverage.ratio, _RATIO_PRECISION),
            "cache_agreement": None if self.coverage is None else self.coverage.cache_agreement,
            "stage1_elapsed_s": round(self.stage1_elapsed_s, _SECONDS_PRECISION),
            "total_elapsed_s": round(self.total_elapsed_s, _SECONDS_PRECISION),
            "degradations": len(self.degradations),
            "regex_share": round(self._method_share("regex"), _RATIO_PRECISION),
            "deterministic_share": round(
                self._method_share("grammar") + self._method_share("reflection"), 4
            ),
            "network_attempted": self.network_attempted,
            "exit_code": self.exit_code,
        }

    def _method_share(self, method: str) -> float:
        """`facts[method=X] / facts[*]` -- the M2 and M6 predicates (`01` §6)."""
        total = self.total_facts()
        if not total:
            return 0.0
        matching = sum(
            len(batch.facts) for batch in self.batches if batch.manifest.method == method
        )
        return matching / total


def write_run_report(result: RunResult, out_dir: Path) -> tuple[Path, Path]:
    """Write `run_report.json` and append one telemetry line. Returns both paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / RUN_REPORT_NAME
    report_path.write_text(
        json.dumps(result.as_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    telemetry_path = out_dir / TELEMETRY_NAME
    with telemetry_path.open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(result.as_telemetry(), sort_keys=True) + "\n")
    return report_path, telemetry_path


def absolute_paths_in(payload: object) -> tuple[str, ...]:
    """Every absolute-looking path anywhere in a rendered artifact.

    The instrument behind `03` §5.9 invariant 4 and `01` N16. Written as a
    function over the *rendered* structure rather than as a review rule, because
    "no absolute path in any report" is a property of the bytes and the only
    honest way to assert it is to look at the bytes.
    """
    found: list[str] = []
    _collect_absolute(payload, found)
    return tuple(found)


def _collect_absolute(payload: object, found: list[str]) -> None:
    if isinstance(payload, str):
        candidate = Path(payload)
        if candidate.is_absolute() or _looks_windows_absolute(payload):
            found.append(payload)
    elif isinstance(payload, dict):
        for value in payload.values():
            _collect_absolute(value, found)
    elif isinstance(payload, Sequence) and not isinstance(payload, str | bytes):
        for value in payload:
            _collect_absolute(value, found)


def _looks_windows_absolute(value: str) -> bool:
    """A drive-letter path, which `PurePosixPath` does not call absolute.

    Checked explicitly because the run that emits a Windows absolute path is by
    definition running on Windows, where the POSIX test says `False` -- a guard
    that only works on the platform that does not need it is not a guard.
    """
    return len(value) > 2 and value[1] == ":" and value[2] in {"\\", "/"}
