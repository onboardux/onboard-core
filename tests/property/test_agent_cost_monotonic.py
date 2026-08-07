"""Cost never decreases, and it equals what was charged.

*Fails when* a charge is dropped, double-counted, or reported as less than a
previous reading. *Matters because* `Cost` is what an operator is billed against
and what AI spec §3 promises is **accurate on every terminal status** -- a cost
that could go down is a cost that can under-report the abort path, which is
exactly the path someone audits. *No other instrument catches it because* every
example test charges once or twice and reads the total at the end; monotonicity
is a statement about every intermediate reading, and rounding is where it breaks.

**Rounding is the interesting part.** `Meter.cost()` rounds `usd` to six decimal
places for presentation, so a naive implementation that rounded the *accumulator*
rather than the reading would lose fractions of a cent per turn and drift below
the sum -- silently, and in the direction that under-bills. The property compares
each reading against the exact running total at that point.
"""

import datetime as _dt
from typing import Final

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from adopt_agent import Budget, Meter
from adopt_obs import ManualClock

pytestmark = pytest.mark.property

_START: Final[_dt.datetime] = _dt.datetime(2026, 8, 6, tzinfo=_dt.UTC)

#: One provider turn's charge. Fractions of a cent are the point: a per-million
#: token price applied to a small response lands well below six decimal places.
_CHARGE = st.tuples(
    st.integers(min_value=0, max_value=100_000),
    st.integers(min_value=0, max_value=100_000),
    st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
)


@given(charges=st.lists(_CHARGE, min_size=1, max_size=25))
@settings(max_examples=100, deadline=None)
def test_cost_is_monotonic_and_totals_what_was_charged(
    charges: list[tuple[int, int, float]],
) -> None:
    # The budget is deliberately enormous: this property is about the
    # accumulator, and a meter that reported exhaustion would still be expected
    # to keep counting -- accurate cost on the abort path is the same claim.
    meter = Meter(Budget(max_usd=1e9, max_wall_seconds=10**6), clock=ManualClock(_START))

    previous = meter.cost()
    running_usd = 0.0
    running_in = 0
    running_out = 0

    for input_tokens, output_tokens, usd in charges:
        meter.charge(input_tokens=input_tokens, output_tokens=output_tokens, usd=usd)
        running_usd += usd
        running_in += input_tokens
        running_out += output_tokens

        current = meter.cost()

        assert current.usd >= previous.usd
        assert current.input_tokens >= previous.input_tokens
        assert current.output_tokens >= previous.output_tokens
        # Equality against the exact running total, not just "close to it": the
        # rounding is a presentation rule and must not become a truncation.
        assert current.usd == pytest.approx(running_usd, abs=1e-6)
        assert current.input_tokens == running_in
        assert current.output_tokens == running_out
        previous = current


@given(charges=st.lists(_CHARGE, min_size=1, max_size=10))
@settings(max_examples=50, deadline=None)
def test_reading_the_cost_never_changes_it(charges: list[tuple[int, int, float]]) -> None:
    """`cost()` is a reading, not a transfer.

    Without this, an implementation that reset its accumulator on read would
    satisfy the monotonic property one charge at a time and report a total of
    zero to whoever asked last -- and the seam asks last, on the way out.
    """
    meter = Meter(Budget(max_usd=1e9, max_wall_seconds=10**6), clock=ManualClock(_START))
    for input_tokens, output_tokens, usd in charges:
        meter.charge(input_tokens=input_tokens, output_tokens=output_tokens, usd=usd)

    first = meter.cost()
    second = meter.cost()

    assert (first.usd, first.input_tokens, first.output_tokens) == (
        second.usd,
        second.input_tokens,
        second.output_tokens,
    )
