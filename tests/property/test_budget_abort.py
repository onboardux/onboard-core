"""For **any** budget, a crossing aborts on the response that crossed it.

*Fails when* the meter stops evaluating a cap, or the seam discovers a crossing
one billed round trip late. *Matters because* AI spec §3 promises an abort within
`AGENT_ABORT_GRACE_MS` with partial output and accurate cost, and the failure
mode is spending money after the cap was already breached -- which is invisible
in a happy-path test and expensive in production. *No other instrument catches it
because* `tests/unit/test_budget.py` fixes one budget per row, and the claim
implementation spec §4.13 makes is quantified: *abort within grace for any
budget*. An example cannot carry that quantifier.

**The wall-clock dimension is driven by `ManualClock`, never by sleeping.**
Implementation spec §5 bans sleeps in tests, and a property that slept would be
unaffordable at Hypothesis's example counts anyway.

**Both halves are asserted, and the second is what makes the first mean
anything.** `Meter.aborted_within_grace()` answers `True` for a meter that never
crossed anything, so the property also drives the clock *past* the grace and
asserts it answers `False`. Without that, a method returning `True`
unconditionally would satisfy every assertion here.
"""

import datetime as _dt
import json
import tempfile
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from adopt_agent import AgentRequest, Budget, Meter, Runner
from adopt_const import AGENT_ABORT_GRACE_MS
from adopt_obs import ManualClock
from adopt_store.annex import open_annex

pytestmark = pytest.mark.property

_START: Final[_dt.datetime] = _dt.datetime(2026, 8, 6, tzinfo=_dt.UTC)
_SCOPE: Final[str] = "northwind/acme-erp"
_SKILL: Final[str] = "---\nname: probe\ndescription: A skill.\n---\n\nBody.\n"

#: Money caps a client would plausibly set, and factors that cross them. A
#: factor of exactly `1.0` is included because `>=` is the documented
#: comparison: spending precisely the cap is a crossing, not a near miss.
_CAPS = st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False)
_FACTORS = st.floats(min_value=1.0, max_value=5.0, allow_nan=False, allow_infinity=False)


@given(cap=_CAPS, factor=_FACTORS, overshoot_ms=st.integers(min_value=1, max_value=60_000))
@settings(max_examples=50, deadline=None)
def test_the_grace_instrument_answers_for_any_budget(
    cap: float, factor: float, overshoot_ms: int
) -> None:
    """Exhaustion is reached, reported within the grace, and *not* reported
    within it once the clock passes the grace."""
    clock = ManualClock(_START)
    meter = Meter(Budget(max_usd=cap, max_wall_seconds=3600), clock=clock)

    meter.charge(input_tokens=1, output_tokens=1, usd=cap * factor)
    verdict = meter.check()

    # Guard against a vacuous pass: the property is about what happens *after*
    # a crossing, so a generated pair that did not cross proves nothing.
    assert verdict.exhausted is True
    assert verdict.cap == "usd"
    assert meter.aborted_within_grace() is True

    clock.advance(_dt.timedelta(milliseconds=AGENT_ABORT_GRACE_MS + overshoot_ms))
    assert meter.aborted_within_grace() is False


def _run_one(root: Path, *, budget: Budget, turn: dict[str, Any]) -> tuple[str, list[str]]:
    """Drive one recorded turn through the seam. Returns `(status, step types)`."""
    skills = root / "skills" / "probe" / "v1"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    fixture = root / "recorded.json"
    # Two turns are recorded although the run must consume one. A seam that
    # discovered the crossing a round trip late would find a second turn waiting
    # and succeed -- with a fixture of one it would raise instead, and the
    # property would pass for the wrong reason.
    fixture.write_text(json.dumps({"turns": [turn, turn]}), encoding="utf-8")

    with open_annex(root / ".adopt" / "runtime.db") as annex:
        runner = Runner(
            annex=annex,
            scope_ref=_SCOPE,
            skills_root=root / "skills",
            offline=True,
            adapter_id="fake_recorded",
            endpoint=str(fixture),
            clock=ManualClock(_START),
        )
        result = runner.run(
            AgentRequest(
                skill_ref="probe/v1",
                inputs={"path": "/repo"},
                budget=budget,
                idempotency_key="k-1",
            )
        )
    return result.status, [step.type for step in result.trace.steps]


@given(cap=_CAPS, factor=_FACTORS)
@settings(max_examples=25, deadline=None)
def test_a_money_crossing_stops_the_seam_on_the_response_that_crossed(
    cap: float, factor: float
) -> None:
    """No `request` step follows the `abort` step, for any money cap.

    That ordering *is* the claim: a meter checked only after the next request is
    formed has already committed to a round trip the budget forbade.
    """
    with tempfile.TemporaryDirectory() as tmp:
        status, kinds = _run_one(
            Path(tmp),
            budget=Budget(max_usd=cap, max_wall_seconds=3600),
            turn={
                "text": "partial",
                "tool_calls": [],
                "input_tokens": 1,
                "output_tokens": 1,
                "reported_usd": cap * factor,
            },
        )

    assert status == "budget_exhausted"
    assert kinds[-1] == "abort"
    assert "request" not in kinds[kinds.index("abort") :]


@given(cap=st.integers(min_value=2, max_value=10_000), factor=st.integers(min_value=1, max_value=4))
@settings(max_examples=25, deadline=None)
def test_a_token_crossing_stops_the_seam_for_any_token_cap(cap: int, factor: int) -> None:
    """The token cap, crossed with money and wall time deliberately untouched.

    Money is reported first by design, so a token cap that had stopped being
    evaluated would be invisible to any case that also crosses a spend cap.
    """
    tokens = cap * factor
    assume(tokens >= cap)
    with tempfile.TemporaryDirectory() as tmp:
        status, kinds = _run_one(
            Path(tmp),
            budget=Budget(max_usd=1000.0, max_wall_seconds=3600, max_tokens=cap),
            turn={
                "text": "partial",
                "tool_calls": [],
                "input_tokens": tokens,
                "output_tokens": 0,
                "reported_usd": 0.0,
            },
        )

    assert status == "budget_exhausted"
    assert kinds[-1] == "abort"
