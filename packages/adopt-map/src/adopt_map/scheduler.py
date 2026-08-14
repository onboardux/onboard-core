"""The process pool, the watchdog and the failure isolation -- `03` §5.8, §6.

**A process pool, not threads** (`03` §6). Threads are a false economy against
tree-sitter and the GIL, and they are worse than that here: a thread cannot be
killed. `01` F7.3 requires a hung extractor to *degrade to its declared fallback
and never fail the run*, and only a process can actually be stopped.

**Four guarantees, and each is a different failure it is written against:**

1. **Failure isolation** (`01` F7.4, `02` §7 obligation 8). An extractor that
   raises is recorded and the run continues. One bad pack does not cost a client
   their map.
2. **The watchdog** (`01` F7.3). `MAP_EXTRACTOR_TIMEOUT_S`, or the large variant
   when the manifest declares `heavy`. A timeout terminates the worker, records
   the transition and degrades to the manifest's `fallback`.
3. **Deterministic result ordering** (`02` §7 obligation 3). Results are sorted
   by manifest id after collection, never by completion order -- otherwise the
   fact sequence, and therefore every artifact byte, would depend on which core
   finished first.
4. **The budget is the extractor's to check and the scheduler's to enforce.**
   An extractor raising ``MAP_BUDGET_EXHAUSTED`` from `ctx.budget.check()` is
   recorded as **truncated**, not failed: `02` §8 exit 3 is a successful run with
   less output, and the transaction still commits what completed.

**Why `spawn` explicitly.** The default start method differs by platform, and a
`fork` child inherits the parent's file descriptors, its patched socket class and
its store handle. `spawn` gives each extractor a clean interpreter, which is what
makes "an extractor cannot reach the store" true of the process and not only of
the object graph it was handed.

**Sequential mode is a real mode, not a test seam.** `--profile fast` and a
single-worker machine run in-process, where a timeout cannot be enforced because
there is nothing to kill; `run_all` says so by returning `timeout_enforced=False`
on the batch rather than pretending. A scheduler that silently could not honour
its own watchdog is worse than one that reports it.
"""

import multiprocessing as mp
import os
import queue
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from adopt_const import (
    MAP_EXTRACTOR_TIMEOUT_LARGE_S,
    MAP_EXTRACTOR_TIMEOUT_S,
    MAP_MAX_WORKERS_CEILING,
)
from adopt_map.context import ExtractorContext
from adopt_map.schemas.surface import Extractor, SurfaceFact
from adopt_obs import AdoptError, ErrorCode, get_logger

__all__ = [
    "ExtractorOutcome",
    "ScheduleResult",
    "max_workers",
    "run_all",
    "run_one",
    "timeout_for",
]

_log = get_logger(__name__)

#: What happened to one extractor. `truncated` is deliberately distinct from
#: `failed`: a budget stop is a successful partial run (`02` §8 exit 3) and a
#: raise is a defect, and collapsing them would let a pack that always crashes
#: read as a pack that is merely slow.
OutcomeStatus = Literal["ok", "failed", "timeout", "truncated"]

#: How long a worker is given to hand back its result *after* the extractor's own
#: deadline has passed -- the terminate-and-collect window. Small, because it
#: covers a queue drain and nothing else.
_COLLECT_GRACE_S: Final[float] = 2.0

#: The queue-poll floor and ceiling. Internal loop timing with no observable
#: effect on any output: the floor stops a passed deadline spinning and the
#: ceiling keeps the loop responsive to a worker that died quietly. Neither is
#: a behaviour anybody would retune against evidence.
_POLL_FLOOR_S: Final[float] = 0.05  # const-sync: ok -- internal loop timing
_POLL_CEILING_S: Final[float] = 1.0  # const-sync: ok -- internal loop timing


def max_workers() -> int:
    """`min(MAP_MAX_WORKERS_CEILING, cpu_count)` -- `03` §3's own repair.

    The tunable is the ceiling; the machine's core count is not a tunable and
    does not belong in the constants table, so the `min()` lives here at the call
    site exactly as `03` §3 prescribes after B1-CR-38.
    """
    return max(1, min(MAP_MAX_WORKERS_CEILING, os.cpu_count() or 1))


def timeout_for(*, heavy: bool) -> int:
    """The watchdog for one extractor -- `01` F7.3, `03` §3."""
    return MAP_EXTRACTOR_TIMEOUT_LARGE_S if heavy else MAP_EXTRACTOR_TIMEOUT_S


@dataclass(frozen=True, slots=True)
class ExtractorOutcome:
    """One extractor's result, whatever happened to it.

    `facts` is empty for every status but `ok` and `truncated`. A truncated
    extractor keeps what it yielded before the budget stopped it, because
    throwing that away would make exit 3 emit less than it actually has.
    """

    extractor_id: str
    status: OutcomeStatus
    facts: tuple[SurfaceFact, ...]
    elapsed_s: float
    detail: str = ""
    fallback: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    """Every outcome, in **manifest-id order**, plus what the batch could enforce."""

    outcomes: tuple[ExtractorOutcome, ...]
    timeout_enforced: bool

    @property
    def facts(self) -> tuple[SurfaceFact, ...]:
        """Every fact, in extractor order then emission order.

        A single flattened sequence rather than a mapping, because that ordering
        *is* the run's fact order and every artifact's bytes derive from it
        (`01` N4).
        """
        return tuple(fact for outcome in self.outcomes for fact in outcome.facts)

    @property
    def truncated_families(self) -> tuple[str, ...]:
        """`02` §9.2's `truncated_families[]` -- the kinds a stopped extractor
        would have covered, sorted and de-duplicated."""
        families: set[str] = set()
        for outcome in self.outcomes:
            if outcome.status in {"timeout", "truncated"}:
                families.update(outcome.detail.split(",") if outcome.detail else [])
        return tuple(sorted(family for family in families if family))


def run_one(extractor: Extractor, ctx: ExtractorContext) -> ExtractorOutcome:
    """Run one extractor in this process, catching everything it can throw.

    The isolation half of `02` §7 obligation 8. Used directly by sequential mode
    and by the worker entry point, so both paths classify a failure identically
    rather than each inventing its own vocabulary.
    """
    manifest = extractor.manifest()
    started = time.monotonic()
    facts: list[SurfaceFact] = []
    try:
        for fact in extractor.extract(ctx):
            facts.append(fact)
    except AdoptError as error:
        elapsed = time.monotonic() - started
        if error.code is ErrorCode.MAP_BUDGET_EXHAUSTED:
            return ExtractorOutcome(
                extractor_id=manifest.id,
                status="truncated",
                facts=tuple(facts),
                elapsed_s=elapsed,
                detail=",".join(manifest.kinds),
            )
        _log.warn("map_extractor_failed", extractor=manifest.id, error_code=str(error.code))
        return ExtractorOutcome(
            extractor_id=manifest.id,
            status="failed",
            facts=(),
            elapsed_s=elapsed,
            detail=str(error.code),
            fallback=manifest.fallback,
        )
    except Exception as error:
        # An extractor is third-party-shaped code running over a client tree.
        # Narrowing this would mean choosing in advance which defects are allowed
        # to end somebody's run, and `01` F7.4 does not give us that choice: the
        # exception is isolated, recorded, and the run continues.
        _log.warn("map_extractor_failed", extractor=manifest.id, error_type=type(error).__name__)
        return ExtractorOutcome(
            extractor_id=manifest.id,
            status="failed",
            facts=(),
            elapsed_s=time.monotonic() - started,
            detail=type(error).__name__,
            fallback=manifest.fallback,
        )
    return ExtractorOutcome(
        extractor_id=manifest.id,
        status="ok",
        facts=tuple(facts),
        elapsed_s=time.monotonic() - started,
    )


def _worker(extractor: Extractor, ctx: ExtractorContext, sink: "mp.Queue[object]") -> None:
    """The pool's entry point. Module-level so `spawn` can pickle it."""
    sink.put(run_one(extractor, ctx))


@dataclass(slots=True)
class _Pending:
    extractor_id: str
    process: "mp.process.BaseProcess"
    deadline: float
    fallback: str | None
    kinds: tuple[str, ...]
    started: float = field(default_factory=time.monotonic)


def run_all(
    extractors: Sequence[Extractor],
    ctx: ExtractorContext,
    *,
    workers: int | None = None,
    sequential: bool = False,
) -> ScheduleResult:
    """Run every extractor, isolated and budgeted, and return them in id order.

    Args:
        extractors: The run plan, already filtered by `plugins.ExtractorRegistry`.
        ctx: The shared read-only context. Crosses the process boundary by value.
        workers: Pool size; defaults to `max_workers()`.
        sequential: Run in this process. Correct and deterministic, but **cannot
            enforce the watchdog** -- there is nothing to terminate -- so the
            result says `timeout_enforced=False` rather than implying otherwise.

    Returns:
        A `ScheduleResult` whose outcomes are sorted by extractor id.
    """
    if not extractors:
        return ScheduleResult(outcomes=(), timeout_enforced=not sequential)
    if sequential or (workers or max_workers()) == 1:
        outcomes = [run_one(extractor, ctx) for extractor in extractors]
        return ScheduleResult(
            outcomes=tuple(sorted(outcomes, key=lambda outcome: outcome.extractor_id)),
            timeout_enforced=False,
        )
    return _run_pooled(extractors, ctx, workers=workers or max_workers())


def _run_pooled(
    extractors: Sequence[Extractor], ctx: ExtractorContext, *, workers: int
) -> ScheduleResult:
    context = mp.get_context("spawn")
    sink: mp.Queue[object] = context.Queue()
    collected: dict[str, ExtractorOutcome] = {}
    queued = list(extractors)
    running: list[_Pending] = []

    while queued or running:
        while queued and len(running) < workers:
            extractor = queued.pop(0)
            manifest = extractor.manifest()
            process = context.Process(target=_worker, args=(extractor, ctx, sink), daemon=True)
            process.start()
            running.append(
                _Pending(
                    extractor_id=manifest.id,
                    process=process,
                    deadline=time.monotonic() + timeout_for(heavy=manifest.heavy),
                    fallback=manifest.fallback,
                    kinds=tuple(manifest.kinds),
                )
            )

        try:
            result = sink.get(timeout=_poll_interval(running))
        except queue.Empty:
            result = None
        if isinstance(result, ExtractorOutcome):
            collected[result.extractor_id] = result

        still_running: list[_Pending] = []
        for pending in running:
            if pending.extractor_id in collected:
                pending.process.join(timeout=_COLLECT_GRACE_S)
                continue
            if not pending.process.is_alive() and pending.process.exitcode is not None:
                # Dead, and nothing on the queue: the worker died before it could
                # answer -- a segfault, an OOM kill, a `sys.exit` inside client-
                # shaped code. Recorded as a failure, never as an empty success:
                # "returned no facts" and "was killed" are different claims.
                collected[pending.extractor_id] = ExtractorOutcome(
                    extractor_id=pending.extractor_id,
                    status="failed",
                    facts=(),
                    elapsed_s=time.monotonic() - pending.started,
                    detail=f"worker_exit_{pending.process.exitcode}",
                    fallback=pending.fallback,
                )
                continue
            if time.monotonic() >= pending.deadline:
                pending.process.terminate()
                pending.process.join(timeout=_COLLECT_GRACE_S)
                _log.warn("map_extractor_timeout", extractor=pending.extractor_id)
                collected[pending.extractor_id] = ExtractorOutcome(
                    extractor_id=pending.extractor_id,
                    status="timeout",
                    facts=(),
                    elapsed_s=time.monotonic() - pending.started,
                    detail=",".join(pending.kinds),
                    fallback=pending.fallback,
                )
                continue
            still_running.append(pending)
        running = still_running

    sink.close()
    return ScheduleResult(
        outcomes=tuple(collected[key] for key in sorted(collected)),
        timeout_enforced=True,
    )


def _poll_interval(running: Sequence[_Pending]) -> float:
    """How long to wait on the queue before re-checking the deadlines.

    The nearest deadline, floored so a passed deadline does not spin and capped
    so a long-running extractor does not make the loop unresponsive to a worker
    that died quietly.
    """
    if not running:
        return _POLL_FLOOR_S * 2
    nearest = min(pending.deadline for pending in running) - time.monotonic()
    return min(max(nearest, _POLL_FLOOR_S), _POLL_CEILING_S)
