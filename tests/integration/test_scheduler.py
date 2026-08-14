"""Isolation, the watchdog, the budget and deterministic order -- `03` §5.8, §6.

*Defect sentence.* Fails when one extractor's failure, hang or budget stop reaches
the run; matters because `01` F7.3 and F7.4 promise a client that a bad pack costs
them that pack and not their map, and because `02` §8's exit 3 promises the
transaction still commits what completed; no other instrument catches it because
a scheduler that aborted the run on the first exception produces a *shorter* map
that looks like a system with less in it.

**The process-pool cases are integration, not unit.** They start real processes
and terminate real hangs, which is the only way to assert that a hang can be
stopped at all -- a thread-based scheduler passes every sequential test and fails
this file.
"""

import time

import pytest
from adopt_map.context import Budget, ExtractorContext
from adopt_map.fileindex import build_index
from adopt_map.scheduler import max_workers, run_all, run_one, timeout_for

from adopt_const import MAP_EXTRACTOR_TIMEOUT_LARGE_S, MAP_EXTRACTOR_TIMEOUT_S
from tests.fixtures.extractors import (
    BudgetRespectingExtractor,
    CrashingExtractor,
    HangingExtractor,
    KindViolatingExtractor,
    QuietExtractor,
)

pytestmark = pytest.mark.integration


def _context(root: str = ".", *, exhausted: bool = False) -> ExtractorContext:
    start = time.time() - 10_000 if exhausted else time.time()
    span = 1.0 if exhausted else 3_600.0
    return ExtractorContext(
        root=root,
        index=build_index(root),
        budget=Budget.starting_at(start, stage1_s=span, total_s=span),
        archetype="web",
    )


def test_a_crashing_extractor_is_isolated_and_the_run_continues() -> None:
    """`01` F7.4. One bad pack does not cost a client their map."""
    result = run_all([CrashingExtractor(), QuietExtractor()], _context(), sequential=True)
    outcomes = {outcome.extractor_id: outcome for outcome in result.outcomes}
    assert outcomes["common.crashing"].status == "failed"
    assert outcomes["common.crashing"].detail == "RuntimeError"
    assert outcomes["common.quiet"].status == "ok"
    assert len(outcomes["common.quiet"].facts) == 1


def test_a_failed_extractor_reports_its_declared_fallback() -> None:
    """`01` F7.3: a failure degrades to the declared fallback and records it.

    Recorded rather than silently substituted -- the run report has to name what
    ran instead of what was asked for.
    """
    result = run_all([CrashingExtractor()], _context(), sequential=True)
    assert result.outcomes[0].fallback == "common.regex"


def test_a_budget_stop_is_truncated_and_keeps_what_it_produced() -> None:
    """`02` §8 exit 3 emits *"stage-1 artifacts at minimum"*.

    `truncated` is distinct from `failed` on purpose: a budget stop is a
    successful partial run and a raise is a defect, and collapsing them would let
    a pack that always crashes read as a pack that is merely slow.
    """
    result = run_all([BudgetRespectingExtractor()], _context(exhausted=True), sequential=True)
    outcome = result.outcomes[0]
    assert outcome.status == "truncated"
    assert [fact.local_key for fact in outcome.facts] == ["budgeted.first"]
    assert result.truncated_families == ("symbol",)


def test_results_are_ordered_by_extractor_id_not_by_completion() -> None:
    """`02` §7 obligation 3. The fact sequence is every artifact's byte order."""
    forwards = run_all(
        [QuietExtractor(), CrashingExtractor(), BudgetRespectingExtractor()],
        _context(),
        sequential=True,
    )
    backwards = run_all(
        [BudgetRespectingExtractor(), CrashingExtractor(), QuietExtractor()],
        _context(),
        sequential=True,
    )
    assert [o.extractor_id for o in forwards.outcomes] == [
        o.extractor_id for o in backwards.outcomes
    ]
    assert [o.extractor_id for o in forwards.outcomes] == sorted(
        o.extractor_id for o in forwards.outcomes
    )


def test_a_kind_violating_extractor_runs_and_the_writer_is_what_refuses_it() -> None:
    """Obligation 4 is enforced at the **write**, not by dropping facts silently.

    The scheduler collects what an extractor emitted; the writer checks it against
    the manifest and raises `MAP_EXTRACTOR_FAILED`. A scheduler that filtered
    undeclared kinds would make the violation invisible and the run would report
    a family it never covered.
    """
    result = run_all([KindViolatingExtractor()], _context(), sequential=True)
    assert result.outcomes[0].status == "ok"
    assert result.outcomes[0].facts[0].identity_kind == "endpoint"


def test_sequential_mode_says_it_cannot_enforce_the_watchdog() -> None:
    """A scheduler that silently could not honour its watchdog is worse than one
    that reports it."""
    assert run_all([QuietExtractor()], _context(), sequential=True).timeout_enforced is False


def test_run_one_classifies_every_outcome_the_same_way_both_paths_use() -> None:
    """One classifier, used by sequential mode and by the worker entry point.

    Two classifiers are two vocabularies for one event, and the run report would
    then depend on which mode happened to run.
    """
    assert run_one(QuietExtractor(), _context()).status == "ok"
    assert run_one(CrashingExtractor(), _context()).status == "failed"
    assert run_one(BudgetRespectingExtractor(), _context(exhausted=True)).status == "truncated"


def test_the_worker_pool_terminates_a_hanging_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`01` F7.3, in a real process pool. **The reason the pool is processes.**

    A thread cannot be killed, so a thread-based scheduler would sit here until
    the suite timed out. The watchdog is shortened by monkeypatching the
    scheduler's own resolver rather than by editing a constant: `MAP_EXTRACTOR_
    TIMEOUT_S` is 120 s and this test would otherwise take two minutes to assert
    something that happens in one.
    """
    monkeypatch.setattr("adopt_map.scheduler.timeout_for", lambda *, heavy: 2)
    started = time.monotonic()
    result = run_all([HangingExtractor(), QuietExtractor()], _context(), workers=2)
    elapsed = time.monotonic() - started

    outcomes = {outcome.extractor_id: outcome for outcome in result.outcomes}
    assert outcomes["common.hanging"].status == "timeout"
    assert outcomes["common.hanging"].fallback == "common.regex"
    assert outcomes["common.quiet"].status == "ok", "the hang took the pool down with it"
    assert result.timeout_enforced is True
    assert elapsed < 60, "the watchdog did not stop the hang"


def test_the_worker_pool_isolates_a_crash_in_a_real_subprocess() -> None:
    """Failure isolation across the process boundary, not only in-process."""
    result = run_all([CrashingExtractor(), QuietExtractor()], _context(), workers=2)
    outcomes = {outcome.extractor_id: outcome for outcome in result.outcomes}
    assert outcomes["common.crashing"].status == "failed"
    assert outcomes["common.quiet"].status == "ok"


def test_the_pool_size_is_the_ceiling_against_the_machine() -> None:
    """`03` §3's own repair: the tunable is the ceiling; `cpu_count` is not one."""
    from adopt_const import MAP_MAX_WORKERS_CEILING

    assert 1 <= max_workers() <= MAP_MAX_WORKERS_CEILING


def test_a_heavy_manifest_takes_the_large_watchdog() -> None:
    assert timeout_for(heavy=False) == MAP_EXTRACTOR_TIMEOUT_S
    assert timeout_for(heavy=True) == MAP_EXTRACTOR_TIMEOUT_LARGE_S
