"""Budget metering. AI spec §1 and §3; contracts §10.1.

**All budget logic is in this module and nowhere else** (implementation spec
§4.13). That is an invariant rather than tidiness: an adapter that metered its
own spend would double-count across its transient retries, and a caller that
metered its own would be the component deciding what it is allowed to spend.

**The meter is checked after every provider response and before every tool
call.** Both, and the ordering is the whole design. After a response is where
cost actually lands; before a tool call is where the *next* response is
committed to. A meter checked only after responses discovers a crossing one full
round trip late, and that round trip is billed.

**Crossing aborts within `AGENT_ABORT_GRACE_MS` and returns partial output with
accurate cost.** "Accurate" is doing real work there: a run that aborts having
spent money must say so, because the abort path is exactly when an operator
asks what it cost. `Meter.spent` is therefore updated before the verdict is
computed, never after.

**Time comes from an injected clock.** Implementation spec §5 bans sleeps in
tests, and the wall-clock cap is the one budget dimension that cannot be driven
by counting tokens -- so the property test advances a `ManualClock` instead.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict

from adopt_agent.api import Budget, Cost
from adopt_const import AGENT_ABORT_GRACE_MS
from adopt_obs import Clock, SystemClock

__all__ = ["BudgetVerdict", "Meter"]

_MS_PER_SECOND: Final[int] = 1000


class BudgetVerdict(BaseModel):
    """Whether the run may continue, and if not, which cap stopped it.

    The reason is carried rather than derived by the caller. "Which cap" is the
    only actionable part of a budget abort -- an operator who raises the wrong
    one gets the same abort again, more expensively.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    exhausted: bool
    #: One of `usd`, `wall_seconds`, `tokens`, `tool_calls`; `None` when the run
    #: may continue.
    cap: str | None = None
    detail: str | None = None


class Meter:
    """Accumulates spend against one `Budget` and answers "may I continue".

    Deliberately not a context manager and deliberately not a decorator: the
    seam has to be able to ask *between* two specific points, and both wrappers
    would only let it ask at the edges.
    """

    def __init__(self, budget: Budget, *, clock: Clock | None = None) -> None:
        self._budget = budget
        self._clock = clock if clock is not None else SystemClock()
        self._started_at = self._clock.now()
        self._input_tokens = 0
        self._output_tokens = 0
        self._usd = 0.0
        self._tool_calls = 0
        #: When the meter first reported exhaustion. The abort deadline is
        #: measured from here, so "within the grace" is a claim about what the
        #: seam did *after it knew*, which is the only part it controls.
        self._exhausted_at: float | None = None

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    def elapsed_ms(self) -> int:
        return int((self._clock.now() - self._started_at).total_seconds() * _MS_PER_SECOND)

    def cost(self) -> Cost:
        """What has been spent so far. Accurate on every path, including abort."""
        return Cost(
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            usd=round(self._usd, 6),
            wall_ms=self.elapsed_ms(),
        )

    def charge(self, *, input_tokens: int, output_tokens: int, usd: float) -> None:
        """Record one provider response.

        Called **before** `check`, always. Charging after the verdict would let
        a run report `budget_exhausted` alongside a cost that excludes the
        response which exhausted it.
        """
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        self._usd += usd

    def record_tool_call(self) -> None:
        self._tool_calls += 1

    def check(self) -> BudgetVerdict:
        """Evaluate every cap. Money first, because it is the irreversible one.

        Token and tool-call caps are bounds on work; the wall-clock cap is a
        bound on waiting; the money cap is the only one whose breach cannot be
        undone by stopping. Reporting it first means an operator reading a
        single line learns the expensive fact.
        """
        verdict = self._evaluate()
        if verdict.exhausted and self._exhausted_at is None:
            self._exhausted_at = self.elapsed_ms()
        return verdict

    def _evaluate(self) -> BudgetVerdict:
        if self._usd >= self._budget.max_usd:
            return BudgetVerdict(
                exhausted=True,
                cap="usd",
                detail=f"spent {self._usd:.6f} against a cap of {self._budget.max_usd}",
            )
        elapsed_s = self.elapsed_ms() / _MS_PER_SECOND
        if elapsed_s >= self._budget.max_wall_seconds:
            return BudgetVerdict(
                exhausted=True,
                cap="wall_seconds",
                detail=f"ran {elapsed_s:.3f}s against a cap of {self._budget.max_wall_seconds}s",
            )
        total_tokens = self._input_tokens + self._output_tokens
        if self._budget.max_tokens is not None and total_tokens >= self._budget.max_tokens:
            return BudgetVerdict(
                exhausted=True,
                cap="tokens",
                detail=f"used {total_tokens} tokens against a cap of {self._budget.max_tokens}",
            )
        return BudgetVerdict(exhausted=False)

    def may_call_tool(self) -> BudgetVerdict:
        """`check()`, plus the tool-call cap. Asked **before** each tool call.

        The tool-call cap is deliberately **not** part of `check`. It is a bound
        on a specific action, not a dimension of spend, and folding it in makes
        every ordinary run report `budget_exhausted` the moment it starts --
        because `max_tool_calls` defaults to `0` (contracts §10.1) and `0 >= 0`
        is true. That defect was live until `test_a_plain_run_returns_ok`
        caught it, which is the argument for asserting the happy path too.

        The `0` default itself stays: a request that declares tools and omits
        the budget gets **no** tool calls rather than unlimited ones. The
        reading is counter-intuitive and intended; do not "fix" it to something
        permissive.
        """
        verdict = self.check()
        if verdict.exhausted:
            return verdict
        if self._tool_calls >= self._budget.max_tool_calls:
            outcome = BudgetVerdict(
                exhausted=True,
                cap="tool_calls",
                detail=(
                    f"made {self._tool_calls} tool calls against a cap of "
                    f"{self._budget.max_tool_calls}"
                ),
            )
            if self._exhausted_at is None:
                self._exhausted_at = self.elapsed_ms()
            return outcome
        return BudgetVerdict(exhausted=False)

    def aborted_within_grace(self) -> bool:
        """Did the seam stop within `AGENT_ABORT_GRACE_MS` of knowing?

        Asserted by the conformance suite (case 8) and by the abort property.
        A meter that never reported exhaustion answers `True` vacuously, so the
        property that uses this also asserts exhaustion was actually reached --
        otherwise a run that never crossed anything would satisfy the claim.
        """
        if self._exhausted_at is None:
            return True
        return self.elapsed_ms() - self._exhausted_at <= AGENT_ABORT_GRACE_MS
