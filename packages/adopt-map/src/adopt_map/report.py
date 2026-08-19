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

**`peak_rss_bytes` is `None` where the platform cannot answer**, and S1.8 shrank
the set of platforms that cannot. `resource` is POSIX-only, so until here this
returned `None` on Windows -- which made `01` **N11** unmeasurable on the machine
this build was authored on, and an NFR whose instrument returns `None` is an NFR
nobody can breach visibly. That is the same shape as the four measurements Build 0
found succeeding by having nothing to measure. Windows answers through PSAPI's
`GetProcessMemoryInfo`, reached with `ctypes` from the standard library: `psutil`
would be a runtime distribution added for one field, which is the trade B1-CR-50,
B1-CR-65 and B1-CR-87 each refused in the other direction. Reporting a zero is
still forbidden -- a platform that cannot answer returns `None`, because a zero is
a number no measurement produced.
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


def _peak_rss_windows() -> int | None:
    """Peak working set in bytes via PSAPI, or `None` if the call does not answer.

    `PROCESS_MEMORY_COUNTERS` is two `DWORD`s followed by eight `SIZE_T`s;
    `PeakWorkingSetSize` is the second `SIZE_T`. The struct is declared here rather
    than assumed, because getting the field order wrong reports a *different real
    number* -- `WorkingSetSize` instead of its peak -- which is the failure mode a
    smoke test cannot see. `GetProcessMemoryInfo` returns zero on failure, and a
    zero is returned as `None`: `01` N11 is a ceiling, and a ceiling compared
    against a fabricated zero passes forever.
    """
    import ctypes
    from ctypes import wintypes

    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = _Counters()
    counters.cb = ctypes.sizeof(_Counters)
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined, unused-ignore]
        psapi = ctypes.windll.psapi  # type: ignore[attr-defined, unused-ignore]
        # **Declaring these is what makes the call answer at all.** Left to
        # ctypes' defaults every argument is an `int`, the 64-bit pseudo-handle
        # is truncated, `GetProcessMemoryInfo` returns 0, and this function
        # reports `None` -- indistinguishable from "this platform cannot answer".
        # Measured, not assumed: the undeclared form returns 0 here and the
        # declared form returns a real peak on the same process.
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        ok = psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        )
    except (AttributeError, OSError):
        return None
    if not ok:
        return None
    peak = int(counters.PeakWorkingSetSize)
    return peak or None


def peak_rss_bytes() -> int | None:
    """Peak resident set size in bytes, or `None` where the platform cannot answer.

    Two implementations, one contract. POSIX answers through `resource`; Windows
    answers through PSAPI (`_peak_rss_windows`). The paired `type: ignore` codes on
    the POSIX call are both needed and neither is redundant: on Windows the module
    resolves with no attributes (`attr-defined`), and on Linux it resolves fully so
    the first code would itself be unused (`unused-ignore`). A single code would
    make `mypy --strict` fail on one of the two platforms CI checks.
    """
    # **Dispatch on the module, not on `sys.platform`.** A statement-level
    # `sys.platform == "win32"` guard is narrowed by mypy against the platform it
    # is running on, so `warn_unreachable` would fail the other platform's branch
    # -- and CI type-checks both. `resource` is the POSIX half by definition and
    # its absence is the Windows half by definition, so the import *is* the
    # dispatch and neither branch is dead on either machine.
    try:
        import resource
    except ImportError:
        return _peak_rss_windows()
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
        """Per-extractor timings, fact counts and **why a failure happened** -- `02` §9.3.

        **`detail` was computed and dropped here until S1.8 (B1-CR-97).**
        `run_extractor` classifies every failure with a cause -- a registered error
        code, or the exception type for anything else -- and this projection kept
        `status` and discarded it. So a run report could say `common.secrets`
        failed and could not say why, which is what the S1.8 soak hit on a real
        repository: one extractor failed on the second of two runs over an
        unchanged 930k-LoC tree, the map silently lost an identity, and the only
        artefact anybody had afterwards recorded the failure with no cause.

        The cause is a *type name* or an error code, never a message: `02` §9.3 is
        **no client source content**, and an exception's `str()` is the one field
        of an exception that routinely carries the line it choked on.
        """
        return [
            {
                "extractor": outcome.extractor_id,
                "status": outcome.status,
                "facts": len(outcome.facts),
                "elapsed_s": round(outcome.elapsed_s, _SECONDS_PRECISION),
                "fallback": outcome.fallback,
                "detail": outcome.detail,
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
