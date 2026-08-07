"""The budget meter: implementation spec §4.13's "budget logic only in
`budget.py`", asserted at the level that logic lives.

*Fails when* a cap stops being evaluated, the wrong cap is reported, the
tool-call cap is folded back into the general spend check, or the abort-grace
instrument becomes vacuous. *Matters because* AI spec §1 assigns metering to the
seam and nowhere else: an adapter that metered its own spend would double-count
across its transient retries, and the meter is the only thing standing between a
caller's request and unbounded spend. *No other instrument catches them because*
`test_agent_runner.py` drives the meter through a recorded fake, which cannot
advance a clock and reports a cap crossing only as an outcome -- these rows
assert the arithmetic that produced it, and name which cap was wrong when they
fail.

**These rows lived in `test_agent_runner.py` and were moved here**, not copied.
S7's Final Output Validation item 1 names this file, and two homes for one
behaviour is one of them drifting.
"""

import datetime as _dt

import pytest

from adopt_agent import Budget, Meter
from adopt_const import AGENT_ABORT_GRACE_MS
from adopt_obs import ManualClock

pytestmark = pytest.mark.unit

_START = _dt.datetime(2026, 8, 6, tzinfo=_dt.UTC)


def _meter(budget: Budget) -> tuple[Meter, ManualClock]:
    clock = ManualClock(_START)
    return Meter(budget, clock=clock), clock


def test_a_wall_clock_crossing_exhausts_the_meter() -> None:
    """The one budget dimension nothing in a recorded run advances.

    Driving this through `Runner` would need the fake adapter to move the clock,
    which would make it a test of the fake. `ManualClock` at the meter is the
    honest level; implementation spec §5 bans sleeps outright.
    """
    meter, clock = _meter(Budget(max_usd=100.0, max_wall_seconds=1))

    assert meter.check().exhausted is False
    clock.advance(_dt.timedelta(seconds=2))
    verdict = meter.check()

    assert verdict.exhausted is True
    assert verdict.cap == "wall_seconds"


def test_the_meter_reports_the_cap_that_actually_stopped_the_run() -> None:
    """An operator who raises the wrong cap gets the same abort, more
    expensively -- so the verdict names which one, and money is reported first
    because it is the only breach that stopping cannot undo."""
    meter, clock = _meter(Budget(max_usd=1.0, max_wall_seconds=1, max_tokens=5))
    meter.charge(input_tokens=99, output_tokens=99, usd=2.0)
    clock.advance(_dt.timedelta(seconds=99))

    assert meter.check().cap == "usd"


def test_the_token_cap_is_evaluated_when_money_and_time_are_untouched() -> None:
    """Without this row the token cap is only ever reached behind `usd`.

    Money is reported first by design, so a token cap that had stopped being
    evaluated at all would be invisible to every test that also crosses a
    spend cap.
    """
    meter, _ = _meter(Budget(max_usd=1000.0, max_wall_seconds=1000, max_tokens=10))
    meter.charge(input_tokens=6, output_tokens=6, usd=0.0)

    verdict = meter.check()

    assert verdict.exhausted is True
    assert verdict.cap == "tokens"


def test_the_tool_call_cap_is_not_a_dimension_of_spend() -> None:
    """The defect this sprint shipped and its own first test run caught.

    `max_tool_calls` defaults to `0` (contracts §10.1), so a tool-call cap
    evaluated inside `check()` makes `0 >= 0` true and **every ordinary run**
    report `budget_exhausted` before it begins. The cap is a bound on a specific
    action, so it lives in `may_call_tool()`. Asserted directly rather than only
    through the happy path, so a regression names the cause instead of failing a
    plain run for no visible reason.
    """
    meter, _ = _meter(Budget(max_usd=1.0, max_wall_seconds=30))

    assert meter.check().exhausted is False
    assert meter.may_call_tool().exhausted is True
    assert meter.may_call_tool().cap == "tool_calls"


def test_a_permitted_tool_call_is_allowed_and_then_counted() -> None:
    """The converse of the row above: without it, "return exhausted always"
    would satisfy the tool-call assertions."""
    meter, _ = _meter(Budget(max_usd=1.0, max_wall_seconds=30, max_tool_calls=1))

    assert meter.may_call_tool().exhausted is False
    meter.record_tool_call()

    assert meter.tool_calls == 1
    assert meter.may_call_tool().exhausted is True


def test_cost_is_accurate_before_any_verdict_is_computed() -> None:
    """`charge` runs before `check`, always.

    The other order reports `budget_exhausted` beside a cost that excludes the
    response which exhausted it -- and the abort path is exactly when an
    operator asks what it cost.
    """
    meter, _ = _meter(Budget(max_usd=1.0, max_wall_seconds=30))
    meter.charge(input_tokens=7, output_tokens=3, usd=2.5)

    cost = meter.cost()

    assert (cost.input_tokens, cost.output_tokens) == (7, 3)
    assert cost.usd == pytest.approx(2.5)
    assert meter.check().exhausted is True


def test_abort_grace_distinguishes_a_late_abort_from_a_prompt_one() -> None:
    """`aborted_within_grace` answers `True` for a run that never crossed
    anything, so the vacuity is recorded here rather than discovered.

    The second half is what makes the instrument worth using at all: advancing
    past `AGENT_ABORT_GRACE_MS` after exhaustion makes it answer `False`. Without
    that, a method returning `True` unconditionally would satisfy every other
    assertion about it.
    """
    meter, clock = _meter(Budget(max_usd=1.0, max_wall_seconds=30))

    assert meter.aborted_within_grace() is True  # vacuous: nothing was exhausted

    meter.charge(input_tokens=0, output_tokens=0, usd=2.0)
    assert meter.check().exhausted is True
    assert meter.aborted_within_grace() is True

    clock.advance(_dt.timedelta(milliseconds=AGENT_ABORT_GRACE_MS + 1))
    assert meter.aborted_within_grace() is False
