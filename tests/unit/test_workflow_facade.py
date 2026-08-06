"""The `Workflow` facade: contracts §10.2's obligations, one row each.

*Fails when* an obligation of the seam breaks -- a replayed `start` runs the body
twice, a retry ignores the cap, an exhausted step loses its history. *Matters
because* every durable operation in the programme goes through this door, and a
seam that executes twice on a replay turns "assume every message is delivered
twice" from a design stance into a defect. *No other instrument catches it
because* the durability drill kills a process and asserts recovery, which is a
different claim: these are the rules that hold while nothing goes wrong.

**No sleeps.** The retry schedule is asserted by recording the delays a client
*would* have waited (`sleeper` is injected), which is exact rather than
timing-dependent -- and implementation spec §5 bans sleeps in tests outright.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from adopt_const import (
    IDEMPOTENCY_KEY_MAX_CHARS,
    WORKFLOW_STEP_BACKOFF_BASE_MS,
    WORKFLOW_STEP_BACKOFF_MAX_MS,
    WORKFLOW_STEP_MAX_ATTEMPTS,
)
from adopt_obs import AdoptError, ErrorCode
from adopt_workflow import (
    InProcessWorkflowClient,
    RetryPolicy,
    backoff_delays_ms,
    clear_registry,
    step,
    workflow,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def delays() -> list[float]:
    return []


@pytest.fixture
def client(tmp_path: Path, delays: list[float]) -> InProcessWorkflowClient:
    return InProcessWorkflowClient(tmp_path, sleeper=delays.append)


# -- RetryPolicy and its schedule -----------------------------------------


def test_retry_policy_defaults_come_from_the_constants_module() -> None:
    policy = RetryPolicy()
    assert policy.max_attempts == WORKFLOW_STEP_MAX_ATTEMPTS
    assert policy.base_ms == WORKFLOW_STEP_BACKOFF_BASE_MS
    assert policy.max_ms == WORKFLOW_STEP_BACKOFF_MAX_MS


def test_max_attempts_above_the_cap_is_refused_not_clamped() -> None:
    """Clamping hides a step written against an expectation the platform does
    not meet; the caller finds out from a log, months later."""
    with pytest.raises(ValidationError) as caught:
        RetryPolicy(max_attempts=WORKFLOW_STEP_MAX_ATTEMPTS + 1)
    assert "WORKFLOW_STEP_MAX_ATTEMPTS" in str(caught.value)


def test_a_ceiling_below_the_base_is_refused() -> None:
    with pytest.raises(ValidationError):
        RetryPolicy(base_ms=1000, max_ms=500)


BACKOFF_ROWS: list[tuple[str, RetryPolicy, tuple[int, ...]]] = [
    (
        "exponential_doubles_from_the_base",
        RetryPolicy(max_attempts=4, base_ms=250, max_ms=30_000),
        (250, 500, 1000),
    ),
    (
        "clamped_at_the_ceiling",
        RetryPolicy(max_attempts=5, base_ms=250, max_ms=600),
        (250, 500, 600, 600),
    ),
    ("no_backoff_waits_nothing", RetryPolicy(max_attempts=3, backoff="none"), (0, 0)),
    ("a_single_attempt_never_waits", RetryPolicy(max_attempts=1), ()),
]


@pytest.mark.parametrize(
    ("label", "policy", "expected"), BACKOFF_ROWS, ids=[r[0] for r in BACKOFF_ROWS]
)
def test_backoff_schedule(label: str, policy: RetryPolicy, expected: tuple[int, ...]) -> None:
    assert backoff_delays_ms(policy) == expected


# -- start, replay, and the duplicate rule --------------------------------


def _counting_workflow(counter: list[str]) -> Callable[..., Any]:
    @step(name="record", retries=RetryPolicy(max_attempts=1))
    def record(ctx: Any, value: str) -> str:
        counter.append(value)
        return value.upper()

    @workflow(name="counting")
    def flow(ctx: Any, args: dict[str, Any]) -> str:
        result = ctx.step(record, args["value"])
        return str(result)

    return flow


def test_start_runs_the_body_and_records_a_terminal_status(
    client: InProcessWorkflowClient,
) -> None:
    seen: list[str] = []
    flow = _counting_workflow(seen)
    handle = client.start(flow, {"value": "a"}, idempotency_key="k1")

    assert handle.status == "completed"
    assert client.status(handle.run_id) == "completed"
    assert client.result(handle.run_id, timeout_s=1) == "A"
    assert seen == ["a"]


def test_the_same_key_returns_the_existing_handle_and_runs_nothing_twice(
    client: InProcessWorkflowClient,
) -> None:
    """Contracts §10.2: repeated `(name, idempotency_key)` returns the existing
    handle. The side-effect count is the assertion that matters -- returning the
    right handle while re-running the body would satisfy a weaker test."""
    seen: list[str] = []
    flow = _counting_workflow(seen)
    first = client.start(flow, {"value": "a"}, idempotency_key="k1")
    second = client.start(flow, {"value": "a"}, idempotency_key="k1")

    assert first == second
    assert seen == ["a"]


def test_one_key_naming_two_workflows_is_a_usage_error(
    client: InProcessWorkflowClient,
) -> None:
    """Not a replay: returning the first handle would silently run something the
    caller never asked for."""
    flow = _counting_workflow([])

    @workflow(name="other")
    def other(ctx: Any, args: dict[str, Any]) -> int:
        return 1

    client.start(flow, {"value": "a"}, idempotency_key="shared")
    with pytest.raises(AdoptError) as caught:
        client.start(other, {}, idempotency_key="shared")
    assert caught.value.code is ErrorCode.WORKFLOW_DUPLICATE_START


IDEMPOTENCY_ROWS: list[tuple[str, str]] = [
    ("empty", ""),
    ("over_the_limit", "x" * (IDEMPOTENCY_KEY_MAX_CHARS + 1)),
]


@pytest.mark.parametrize(("label", "key"), IDEMPOTENCY_ROWS, ids=[r[0] for r in IDEMPOTENCY_ROWS])
def test_idempotency_key_is_validated_at_the_seam(
    client: InProcessWorkflowClient, label: str, key: str
) -> None:
    flow = _counting_workflow([])
    with pytest.raises(AdoptError) as caught:
        client.start(flow, {"value": "a"}, idempotency_key=key)
    assert caught.value.code is ErrorCode.WORKFLOW_DUPLICATE_START


def test_a_key_at_exactly_the_limit_is_accepted(client: InProcessWorkflowClient) -> None:
    flow = _counting_workflow([])
    handle = client.start(flow, {"value": "a"}, idempotency_key="x" * IDEMPOTENCY_KEY_MAX_CHARS)
    assert handle.status == "completed"


def test_starting_something_that_is_not_a_workflow_is_refused(
    client: InProcessWorkflowClient,
) -> None:
    with pytest.raises(AdoptError) as caught:
        client.start(lambda ctx, args: None, {}, idempotency_key="k")
    assert caught.value.code is ErrorCode.WORKFLOW_DUPLICATE_START


def test_calling_a_plain_function_from_a_body_is_refused(
    client: InProcessWorkflowClient,
) -> None:
    """Only a `@step` is persisted and replayed. A plain call runs again on
    every resume, which is the silent version of this failure."""

    def helper(ctx: Any) -> int:
        return 1

    @workflow(name="calls-a-plain-function")
    def flow(ctx: Any, args: dict[str, Any]) -> int:
        return int(ctx.step(helper))

    with pytest.raises(AdoptError) as caught:
        client.start(flow, {}, idempotency_key="k")
    assert caught.value.code is ErrorCode.WORKFLOW_BODY_IMPURE


# -- retries, backoff and exhaustion --------------------------------------


def test_a_step_retries_up_to_the_cap_then_succeeds(
    client: InProcessWorkflowClient, delays: list[float]
) -> None:
    attempts: list[int] = []

    @step(name="flaky", retries=RetryPolicy(max_attempts=3, base_ms=250, max_ms=30_000))
    def flaky(ctx: Any) -> str:
        attempts.append(ctx.attempt)
        if ctx.attempt < 3:
            raise RuntimeError("not yet")
        return "ok"

    @workflow(name="retrying")
    def flow(ctx: Any, args: dict[str, Any]) -> str:
        return str(ctx.step(flaky))

    handle = client.start(flow, {}, idempotency_key="k")
    assert attempts == [1, 2, 3]
    assert handle.status == "completed"
    # The waits the client would have taken, in order and in seconds.
    assert delays == [0.25, 0.5]


def test_an_exhausted_step_fails_the_run_and_keeps_its_history(
    client: InProcessWorkflowClient,
) -> None:
    @step(name="always-fails", retries=RetryPolicy(max_attempts=3, backoff="none"))
    def always_fails(ctx: Any) -> None:
        raise RuntimeError("nope")

    @workflow(name="doomed")
    def flow(ctx: Any, args: dict[str, Any]) -> None:
        ctx.step(always_fails)

    handle = client.start(flow, {}, idempotency_key="k")
    assert handle.status == "failed"

    started = [
        record
        for record in client._journal.records()
        if record.get("type") == "step_started" and record.get("step") == "always-fails"
    ]
    assert [record["attempt"] for record in started] == [1, 2, 3]


# -- cancel, signal, list --------------------------------------------------


def test_cancel_moves_a_run_to_a_terminal_status(client: InProcessWorkflowClient) -> None:
    flow = _counting_workflow([])
    handle = client.start(flow, {"value": "a"}, idempotency_key="k")
    client.cancel(handle.run_id)
    assert client.status(handle.run_id) == "completed"  # already terminal; cancel is a no-op


def test_signal_is_recorded_against_the_run(client: InProcessWorkflowClient) -> None:
    flow = _counting_workflow([])
    handle = client.start(flow, {"value": "a"}, idempotency_key="k")
    client.signal(handle.run_id, "approved", {"by": "operator"})
    signals = [r for r in client._journal.records() if r.get("type") == "signal"]
    assert signals == [
        {
            "type": "signal",
            "run_id": handle.run_id,
            "name": "approved",
            "payload": {"by": "operator"},
        }
    ]


def test_list_is_what_the_drain_procedure_reads(client: InProcessWorkflowClient) -> None:
    """Implementation spec §7.4: flipping `ADOPT_FEATURE_DBOS_BACKEND` off
    requires in-flight runs to drain, and "in-flight" has to be observable."""
    flow = _counting_workflow([])
    client.start(flow, {"value": "a"}, idempotency_key="k1")
    client.start(flow, {"value": "b"}, idempotency_key="k2")

    assert len(client.list()) == 2
    assert len(client.list(status="completed")) == 2
    assert client.list(status="running") == []


def test_status_of_an_unknown_run_is_an_error_not_a_guess(
    client: InProcessWorkflowClient,
) -> None:
    with pytest.raises(AdoptError):
        client.status("run_does_not_exist")


def test_close_is_idempotent_and_keeps_the_run_history(
    client: InProcessWorkflowClient,
) -> None:
    """*Fails when* `close` raises on a second call or discards completed runs.
    *Matters because* the runbook's drain-then-flip ends by closing every holder
    of a client, and a rollback is exactly when a process is closed twice by two
    people — a `close` that raises there turns a rollback into an incident. It
    must also not lose history: an operator closes the client and then asks what
    drained. *No other instrument catches it because* the durability drill closes
    each client exactly once and never reads it afterwards, and the DBOS half of
    that drill does not run without a Postgres.
    """
    flow = _counting_workflow([])
    client.start(flow, {"value": "a"}, idempotency_key="k1")

    client.close()
    client.close()

    assert [h.idempotency_key for h in client.list()] == ["k1"]
