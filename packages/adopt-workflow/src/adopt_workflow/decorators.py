"""`@workflow`, `@step`, `@scheduled` -- contracts §10.2 and PRD F14.

**Registration, not execution.** A decorator here records what a function *is*
and validates that it may be one. Running it is a backend's job, and keeping the
two apart is what lets one suite run against both backends: the registry is
shared, so `inproc` and DBOS are handed identical definitions.

**`@workflow` refuses an impure body at import time.** PRD F14.4 requires the
purity check "at import time", and this is where import time happens. The
alternative -- checking on the first run -- means the failure appears in whatever
environment first executes the workflow, which is usually production, and the
symptom there is a divergent replay rather than an error naming the line.

**`@scheduled` is not a workflow, and that is the point.** PRD F14.5 says
periodic single-step work uses cron rather than a workflow, and calls registering
one as the other "a review rejection". A review line catches it when someone
reads carefully; this decorator catches it always -- `@scheduled` refuses to
decorate a `@workflow`, so the rejection is mechanical and does not depend on the
reviewer having F14.5 in mind.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, TypeVar

from adopt_obs import AdoptError, ErrorCode
from adopt_workflow.api import RetryPolicy
from adopt_workflow.purity import assert_pure

__all__ = [
    "REGISTRY",
    "ScheduledDefinition",
    "StepDefinition",
    "WorkflowDefinition",
    "clear_registry",
    "resolve",
    "scheduled",
    "step",
    "workflow",
]

F = TypeVar("F", bound=Callable[..., Any])

#: Marks set on the decorated function. Read by the backends and by `@scheduled`
#: when it refuses a workflow; kept as dunder-free attributes so a definition is
#: inspectable from a test without reaching into a private registry.
WORKFLOW_ATTR: Final[str] = "__adopt_workflow__"
STEP_ATTR: Final[str] = "__adopt_step__"
SCHEDULED_ATTR: Final[str] = "__adopt_scheduled__"


@dataclass(frozen=True)
class WorkflowDefinition:
    """What `@workflow` records. `(name, version)` is the identity."""

    name: str
    version: int
    fn: Callable[..., Any]


@dataclass(frozen=True)
class StepDefinition:
    """What `@step` records, including the policy its retries are capped by."""

    name: str
    retries: RetryPolicy
    fn: Callable[..., Any]


@dataclass(frozen=True)
class ScheduledDefinition:
    """A periodic single-step job. Cron, deliberately not a workflow."""

    name: str
    cron: str
    fn: Callable[..., Any]


#: One registry for the programme, keyed by `(kind, name, version)`.
#:
#: A backend resolves a definition from here rather than from a closure, which is
#: what makes a resumed run able to find the body it was executing: after a
#: process death there is no closure left, only a name in a journal.
REGISTRY: Final[dict[tuple[str, str, int], object]] = {}


def clear_registry() -> None:
    """Empty the registry. For tests that declare throwaway workflows."""
    REGISTRY.clear()


def resolve(kind: str, name: str, version: int = 1) -> Any:
    """The definition registered under `(kind, name, version)`.

    Raises rather than returning `None`: a resumed run that cannot find its body
    must fail loudly, because the alternative is a run that silently never
    completes and a queue that never drains.
    """
    key = (kind, name, version)
    if key not in REGISTRY:
        known = ", ".join(sorted(f"{k[0]}:{k[1]}@{k[2]}" for k in REGISTRY))
        raise AdoptError(
            ErrorCode.WORKFLOW_STEP_EXHAUSTED,
            message=f"no {kind} registered as {name!r} version {version}",
            hint=(
                f"A resumed run resolves its body by name; the module declaring it "
                f"must be imported before the backend starts. Registered: {known or 'none'}"
            ),
        )
    return REGISTRY[key]


def workflow(*, name: str, version: int = 1) -> Callable[[F], F]:
    """Register a deterministic, replayable workflow body.

    The body is checked for purity **now**, at decoration time.
    """

    def decorate(fn: F) -> F:
        assert_pure(fn)
        definition = WorkflowDefinition(name=name, version=version, fn=fn)
        key = ("workflow", name, version)
        if key in REGISTRY:
            raise AdoptError(
                ErrorCode.WORKFLOW_DUPLICATE_START,
                message=f"a workflow named {name!r} version {version} is already registered",
                hint=(
                    "`(name, version)` is the identity a resumed run resolves through. "
                    "Two bodies under one key means a resume can execute the wrong one."
                ),
            )
        REGISTRY[key] = definition
        setattr(fn, WORKFLOW_ATTR, definition)
        return fn

    return decorate


def step(*, name: str, retries: RetryPolicy | None = None) -> Callable[[F], F]:
    """Register a step: the only place non-deterministic work may live."""

    def decorate(fn: F) -> F:
        definition = StepDefinition(name=name, retries=retries or RetryPolicy(), fn=fn)
        REGISTRY[("step", name, 1)] = definition
        setattr(fn, STEP_ATTR, definition)
        return fn

    return decorate


def scheduled(*, name: str, cron: str) -> Callable[[F], F]:
    """Register periodic single-step work. **Not** a workflow -- PRD F14.5.

    Refuses to decorate a function that is already a workflow. The durable
    machinery exists for work that must survive a crash mid-sequence; a single
    step that runs every hour survives by running again next hour, and putting it
    on the engine buys retention, replay and a queue nobody needed.
    """

    def decorate(fn: F) -> F:
        if hasattr(fn, WORKFLOW_ATTR):
            raise AdoptError(
                ErrorCode.WORKFLOW_BODY_IMPURE,
                message=f"{name!r} is registered as both a workflow and a scheduled job",
                hint=(
                    "PRD F14.5: periodic single-step work uses cron, not a workflow. "
                    "If the job really is a multi-step sequence that must survive a "
                    "crash halfway, drop @scheduled and start it from cron instead."
                ),
            )
        definition = ScheduledDefinition(name=name, cron=cron, fn=fn)
        REGISTRY[("scheduled", name, 1)] = definition
        setattr(fn, SCHEDULED_ATTR, definition)
        return fn

    return decorate
