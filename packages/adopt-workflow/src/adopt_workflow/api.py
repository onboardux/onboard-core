"""The `Workflow` facade: contracts §10.2, and nothing a backend can widen.

**Why a facade at all.** Implementation spec §1.1 locks DBOS for durable
execution and source spec §13 requires that the documented Temporal migration
stay a migration. That is only true if no DBOS symbol appears outside one module,
which means every caller in the programme talks to the shapes declared here.
`no-dbos` enforces the second half; this file is the first.

**What the seam owns and what a backend owns.** The seam owns the vocabulary --
status values, the retry policy and its caps, the handle, the two contexts. A
backend owns *when* things run and *where* they are persisted, and nothing else.
A backend that could add a status value or lift a retry cap would make the
facade advisory, and the two backends would then differ in ways only production
discovers.

**`run_id` needs no new prefix.** Contracts §1.1 registers `run_` for "one CLI
invocation or one unit of work" and §10.2 names every keyed parameter `run_id`.
A workflow run is one unit of work, so it is a `run_` id -- adding a `wf_` prefix
would have been a §1.1 change made to avoid reusing the entry that already
describes this.
"""

from collections.abc import Callable, Mapping
from typing import Any, Final, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from adopt_const import (
    IDEMPOTENCY_KEY_MAX_CHARS,
    WORKFLOW_STEP_BACKOFF_BASE_MS,
    WORKFLOW_STEP_BACKOFF_MAX_MS,
    WORKFLOW_STEP_MAX_ATTEMPTS,
)
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "TERMINAL_STATUSES",
    "Backoff",
    "RetryPolicy",
    "StepContext",
    "WorkflowClient",
    "WorkflowContext",
    "WorkflowHandle",
    "WorkflowStatus",
    "backoff_delays_ms",
    "validate_idempotency_key",
]

#: The run lifecycle. `completed`, `failed` and `cancelled` are terminal; the
#: kill-and-resume drill asserts `completed` after a resume, so a resumed run
#: must reach the same value a run that never died would.
WorkflowStatus = Literal["pending", "running", "completed", "failed", "cancelled"]

#: A run in one of these states will not change again. Both backends are asked
#: for this set rather than each deciding which of their states are final.
TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({"completed", "failed", "cancelled"})

#: Contracts §10.2 shows `backoff="exponential"`. `none` exists for a step whose
#: failure is not worth waiting on; there is deliberately no `linear`, because a
#: third curve is a third thing to reason about at 3 a.m. for no stated need.
Backoff = Literal["exponential", "none"]


class RetryPolicy(BaseModel):
    """How many times a step is retried, and how long between attempts.

    `max_attempts` is **capped**, not defaulted, by `WORKFLOW_STEP_MAX_ATTEMPTS`.
    A caller asking for more is refused rather than silently clamped: a step
    declared with 50 attempts was written against an expectation the platform
    does not meet, and clamping hides that until someone reads a log.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=WORKFLOW_STEP_MAX_ATTEMPTS, ge=1)
    backoff: Backoff = "exponential"
    base_ms: int = Field(default=WORKFLOW_STEP_BACKOFF_BASE_MS, ge=0)
    max_ms: int = Field(default=WORKFLOW_STEP_BACKOFF_MAX_MS, ge=0)

    @field_validator("max_attempts")
    @classmethod
    def _within_cap(cls, value: int) -> int:
        if value > WORKFLOW_STEP_MAX_ATTEMPTS:
            raise ValueError(
                f"max_attempts={value} exceeds WORKFLOW_STEP_MAX_ATTEMPTS="
                f"{WORKFLOW_STEP_MAX_ATTEMPTS}. The cap is the platform's, not the "
                f"step's; raise it in implementation spec §2.2 and adopt_const together."
            )
        return value

    # A cross-field rule, so it runs after the model is built rather than as a
    # `field_validator` taking `ValidationInfo` -- that parameter is generic in
    # `Any`, and `mypy.ini` sets `disallow_any_decorated`.
    @model_validator(mode="after")
    def _max_not_below_base(self) -> "RetryPolicy":
        if self.max_ms < self.base_ms:
            raise ValueError(
                f"max_ms={self.max_ms} is below base_ms={self.base_ms}, so the first "
                f"delay would already exceed the ceiling and every later one would be "
                f"clamped to it."
            )
        return self


def backoff_delays_ms(policy: RetryPolicy) -> tuple[int, ...]:
    """The delay before each retry, in order. Length is `max_attempts - 1`.

    A pure function of the policy, so the schedule can be asserted without
    running a workflow and without either backend reproducing it independently.
    There is no jitter: this is a per-run schedule for a single-writer local
    backend and a DBOS queue, not a thundering herd of clients against one
    service. Adding jitter would also make the sequence untestable by equality,
    which is the property that keeps the two backends honest about the cap.
    """
    if policy.backoff == "none":
        return tuple(0 for _ in range(policy.max_attempts - 1))
    delays: list[int] = []
    delay = policy.base_ms
    for _ in range(policy.max_attempts - 1):
        delays.append(min(delay, policy.max_ms))
        delay *= 2
    return tuple(delays)


def validate_idempotency_key(key: str) -> str:
    """Contracts §1.5: opaque, non-empty, at most `IDEMPOTENCY_KEY_MAX_CHARS`.

    Refused at the seam rather than at a backend, so both backends refuse the
    same keys. A key silently truncated by a column width is two different runs
    that look like a replay of each other.
    """
    if not key:
        raise AdoptError(
            ErrorCode.WORKFLOW_DUPLICATE_START,
            message="an idempotency key is required to start a workflow",
            hint=(
                "Contracts §1.5: every retriable operation takes a key, because "
                "every message is assumed to be delivered twice."
            ),
        )
    if len(key) > IDEMPOTENCY_KEY_MAX_CHARS:
        raise AdoptError(
            ErrorCode.WORKFLOW_DUPLICATE_START,
            message=f"idempotency key is {len(key)} characters, over the "
            f"{IDEMPOTENCY_KEY_MAX_CHARS}-character limit",
            hint="Hash the caller's key rather than truncating it; a truncated key collides.",
        )
    return key


class WorkflowHandle(BaseModel):
    """What `start` returns, and what a replayed `start` returns unchanged."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    name: str
    version: int
    idempotency_key: str
    status: WorkflowStatus


@runtime_checkable
class StepContext(Protocol):
    """Contracts §10.2, verbatim: `run_id`, `attempt`, and `dedupe`."""

    run_id: str
    attempt: int

    def dedupe(self, key: str) -> bool:
        """`True` when this is the first commit for `key`; `False` on a replay.

        The exactly-once boundary. Steps run at least once, so the effect and
        its dedupe record must commit **together** -- a backend that writes the
        record after the effect has a window in which a crash duplicates the
        effect, and that window is exactly what the durability drill opens.
        """
        ...


@runtime_checkable
class WorkflowContext(Protocol):
    """What a workflow body is handed.

    Deliberately tiny. Everything non-deterministic reaches the body through
    `step`, because a body is **replayed**: on resume the engine re-executes it
    and expects the same decisions. `workflow-body-purity` enforces the
    negative half of that at lint and import time; this Protocol is the positive
    half -- the only door out of a body.
    """

    run_id: str

    def step(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Execute `fn` as a durable step, or replay its recorded result."""
        ...


@runtime_checkable
class WorkflowClient(Protocol):
    """Contracts §10.2, plus `list` *(CR-42)*.

    `list` is named by PRD F14.1 and omitted from §10.2's Protocol, and
    implementation spec §7.4's rollback surface needs it: flipping
    `ADOPT_FEATURE_DBOS_BACKEND` off requires in-flight runs to drain first, and
    "drain" is unobservable without a way to enumerate them.
    """

    def start(
        self,
        fn: Callable[..., Any],
        args: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> WorkflowHandle: ...

    def signal(self, run_id: str, name: str, payload: Mapping[str, Any]) -> None: ...

    def status(self, run_id: str) -> WorkflowStatus: ...

    def result(self, run_id: str, *, timeout_s: int) -> Any: ...

    def cancel(self, run_id: str) -> None: ...

    def recover(self) -> list[WorkflowHandle]:
        """Re-drive every non-terminal run; return what was resumed.

        Also CR-42. Implementation spec §4.14 says a crash between two step
        records "replays the step", which presumes an entry point where the
        replay begins -- DBOS reaches it at launch, and the in-process backend
        has to be told. Declaring it here is what lets **one** durability suite
        drive both: a drill that called `recover()` on one backend and relied on
        a constructor side effect on the other would be two drills wearing one
        name.
        """
        ...

    # `list` is declared last: it shadows the builtin for every annotation after
    # it in the class body, and the contract fixes the method name.
    def list(self, *, status: WorkflowStatus | None = None) -> list[WorkflowHandle]: ...
