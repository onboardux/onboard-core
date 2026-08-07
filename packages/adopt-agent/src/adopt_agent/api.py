"""The `AgentRunner` seam: contracts §10.1, and nothing an adapter can widen.

**Why a seam at all.** AI spec §1 makes this the one choke point every model call
in either repository passes through. That is not a style preference: budget
metering, output-schema validation, idempotency and tracing are each wrong in a
different way if an adapter owns them. An adapter that metered its own budget
would double-count across a retry; an adapter that validated its own output
would be free to define "valid"; an adapter that recorded its own trace would be
the component deciding what an audit can see. `no-provider-sdk` enforces the
other half -- that provider clients live only under `adapters/` -- and this file
is the vocabulary both halves are written against.

**The four types this module declares that §10.1 only named** *(CR-44)*.
`ToolSpec`, `Trace`, `Artifact` and `AdapterInfo` appeared in `AgentRequest`,
`AgentResult` and `AgentRunner.adapters()` and were declared nowhere in the pack.
Each is derived from what the pack already requires of it rather than invented,
and §10.1 now records the derivation so it is auditable rather than folklore.

**There is no `execute` on anything here, and that is the enforcement of F13.6.**
PRD F13.6 says agent-authored code is written to a file for review and never
executed in the run that produced it. An `Artifact` therefore carries a path, a
digest and a size, and no content and no way to run it. A boolean saying it was
not executed would be a shape through which a caller could believe it had been.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from adopt_const import (
    AGENT_DEFAULT_MAX_USD,
    AGENT_DEFAULT_MAX_WALL_SECONDS,
    IDEMPOTENCY_KEY_MAX_CHARS,
)
from adopt_obs import AdoptError

__all__ = [
    "Adapter",
    "AdapterInfo",
    "AdapterKind",
    "AdapterResponse",
    "AgentRequest",
    "AgentResult",
    "AgentRunner",
    "AgentStatus",
    "Artifact",
    "Budget",
    "Cost",
    "ToolCall",
    "ToolSpec",
    "Trace",
    "TraceStep",
    "TraceStepType",
]

#: Contracts §10.1, verbatim. `budget_exhausted` is a *status*, never an
#: exception -- §13 refuses `AGENT_BUDGET_EXHAUSTED` at `AdoptError`
#: construction, because a caller that must inspect partial output and accurate
#: cost cannot be handed a raised exception instead.
AgentStatus = Literal["ok", "budget_exhausted", "refused", "error", "cancelled"]

#: Contracts §10.1, verbatim.
TraceStepType = Literal[
    "request", "response", "tool_call", "tool_result", "validation_retry", "abort"
]

#: AI spec §2's Kind column. `test` is `fake_recorded`'s: it is neither hosted
#: nor local because it opens no connection at all, and calling it "local" would
#: let it satisfy the conformance gate's "at least one local adapter" rule --
#: which would make a pipeline exercising *no real model* pass the gate that
#: exists to prove one was exercised.
AdapterKind = Literal["hosted", "local", "test"]


class Budget(BaseModel):
    """What one run may spend. Contracts §10.1.

    The defaults are `AGENT_DEFAULT_MAX_USD` and `AGENT_DEFAULT_MAX_WALL_SECONDS`
    from implementation spec §2.2, so a caller who omits a budget still has one.
    A budget of "unlimited" is deliberately not expressible for money or wall
    time: `max_tokens` and `max_tool_calls` may be `None`/`0` because a provider
    already bounds them, but an unbounded spend is the failure the meter exists
    to prevent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_usd: float = Field(default=AGENT_DEFAULT_MAX_USD, gt=0)
    max_wall_seconds: int = Field(default=AGENT_DEFAULT_MAX_WALL_SECONDS, gt=0)
    max_tokens: int | None = Field(default=None, gt=0)
    max_tool_calls: int = Field(default=0, ge=0)


class ToolSpec(BaseModel):
    """A tool the model may call, and the caller's function that answers it.

    *(CR-44 -- §10.1 named this type and declared it nowhere.)*

    **`handler` is the caller's, never the model's**, and that is what keeps
    §10.1's "no API executes model-authored code" true in the presence of tools
    at all. The model chooses *which* declared tool to call and with what
    arguments; it never supplies the code that runs. AI spec §7.1 cases 4-6
    require a tool to be invoked once with schema-valid arguments, to terminate a
    multi-turn loop, and to surface its exception as a tool result rather than a
    crash -- none of which a spec without a handler can express.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


class AgentRequest(BaseModel):
    """One call through the seam. Contracts §10.1.

    **`idempotency_key` is bounded here rather than by a raised error**, because
    the request is built by the caller: refusing at construction refuses before
    the seam is entered, and there is no `AGENT_*` code in §13 for a malformed
    key. A key silently truncated to a column width is two different runs that
    look like a replay of each other -- so the limit is a constraint on the
    shape, not a check someone has to remember to run.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_ref: str = Field(min_length=1)
    inputs: dict[str, Any]
    tools: list[ToolSpec] = Field(default_factory=list)
    budget: Budget
    output_schema: dict[str, Any] | None = None
    idempotency_key: str = Field(min_length=1, max_length=IDEMPOTENCY_KEY_MAX_CHARS)
    adapter: str | None = None


class Cost(BaseModel):
    """What one run actually spent. Contracts §10.1.

    Accurate on **every** terminal status, including `budget_exhausted` and
    `error`. A cost that were only accurate on success would make the abort path
    the one place an operator cannot answer "what did that cost me", which is
    precisely when they ask.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    usd: float = Field(ge=0)
    wall_ms: int = Field(ge=0)


class TraceStep(BaseModel):
    """One recorded event. Contracts §10.1, verbatim.

    `detail_sha256` is a digest and there is no field for the payload. AI spec
    §8.3: an operator can prove *what was asked* without the payload being
    retrievable from our artifacts -- which is the property that lets a trace be
    kept inside a client environment at all.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int = Field(ge=0)
    type: TraceStepType
    at: datetime
    detail_sha256: str = Field(min_length=1)
    tokens: int | None = Field(default=None, ge=0)


class Trace(BaseModel):
    """The audit record of one run.

    *(CR-44 -- §10.1 named this type and declared it nowhere.)*

    The six scalars are **exactly** §12's `agent_run` columns that sit beside
    `trace_json`, and **exactly** what AI spec §7.1 case 11 asserts is present:
    adapter, model, `params_hash`, `skill_sha256`, `inputs_sha256`. `skill_ref`
    joins them because a digest without the reference it digests cannot be
    looked up. Deriving the shape from the table it is stored in is what stops
    the trace and the annex row disagreeing about what a run was.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter: str = Field(min_length=1)
    model: str = Field(min_length=1)
    params_hash: str = Field(min_length=1)
    skill_ref: str = Field(min_length=1)
    skill_sha256: str = Field(min_length=1)
    inputs_sha256: str = Field(min_length=1)
    steps: list[TraceStep] = Field(default_factory=list)


class Artifact(BaseModel):
    """A file the run produced, recorded for review and never executed.

    *(CR-44 -- §10.1 named this type and declared it nowhere.)*

    PRD F13.6 and AI spec §8.5 are one rule stated twice: model-authored code is
    written to a file for review, and never executed in the run that produced it.
    This type carries a path, a digest and a size and **no content** -- and there
    is deliberately no `executed` flag and no `run()` anywhere in this package,
    because the guarantee is kept by there being no API to call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    bytes: int = Field(ge=0)


class AdapterInfo(BaseModel):
    """What `AgentRunner.adapters()` reports, and what `adopt agent adapters`
    prints.

    *(CR-44 -- §10.1 named this type and declared it nowhere.)*

    The four fields are **exactly** the `adapters[{id, kind, available, reason}]`
    envelope contracts §14 already publishes for that command, so the CLI
    renders this rather than re-shaping it. `reason` is populated whenever
    `available` is false and is the difference between a usable diagnostic and
    "no": offline denial, an unconfigured endpoint and a missing credential are
    three different fixes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    kind: AdapterKind
    available: bool
    reason: str | None = None


class AgentResult(BaseModel):
    """What one run returns. Contracts §10.1.

    `error` is an `AdoptError` -- an exception instance carried as a value, which
    is why this model allows an arbitrary type. That is §10.1's declared shape
    and it is the right one: a run that failed still has a cost and a trace, and
    raising would throw both away at exactly the moment they matter.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    status: AgentStatus
    output: str | dict[str, Any] | None
    artifacts: list[Artifact] = Field(default_factory=list)
    cost: Cost
    trace: Trace
    error: AdoptError | None = None


@runtime_checkable
class AgentRunner(Protocol):
    """Contracts §10.1, verbatim.

    Two methods, and the second is not decoration: AI spec §2 says the seam
    **raises** rather than substituting when a configured adapter is
    unavailable, so a caller needs a way to find out what is available before
    committing a run to it. A silent substitution changes cost, behaviour and
    data residency without the operator knowing, and in a client environment
    that is a security-review finding rather than a convenience.
    """

    def run(self, request: AgentRequest) -> AgentResult: ...

    def adapters(self) -> list[AdapterInfo]: ...


@runtime_checkable
class Adapter(Protocol):
    """What a provider integration must implement. Not in §10.1 by name.

    **Adapters translate; they do not enforce policy** (AI spec §1, §2 and PRD
    F13.2). This Protocol is deliberately one method wide: everything the seam
    owns -- budget metering, the output-schema retry, idempotency, tracing --
    is absent from it, so an adapter has no surface through which to own any of
    them. The one thing an adapter may retry is a *transient provider error*,
    bounded by `AGENT_ADAPTER_TIMEOUT_S`, and it does that inside `complete`
    where the seam's meter cannot double-count it.
    """

    id: str
    kind: AdapterKind

    def model(self) -> str:
        """The configured model id. Never a hard-coded default (AI spec §2)."""
        ...

    def params_hash(self) -> str:
        """A stable digest of the parameters this adapter will send."""
        ...

    def complete(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolSpec],
        tool_results: list[Mapping[str, Any]],
        max_tokens: int | None,
    ) -> "AdapterResponse":
        """One provider round trip. Raises `AGENT_PROVIDER_ERROR` on failure."""
        ...


class AdapterResponse(BaseModel):
    """One provider turn, normalized. The adapter's whole output surface.

    `tool_calls` carries what the model asked for; the **seam** decides whether
    the budget permits answering it, and the caller's `ToolSpec.handler` answers
    it. Splitting it this way is what makes AI spec §7.1 case 5 -- a multi-turn
    loop that terminates and respects `max_tool_calls` -- a property of the seam
    rather than of each adapter's loop.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    tool_calls: list["ToolCall"] = Field(default_factory=list)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    #: Set only where the provider reports cost directly; AI spec §3 says a
    #: reported value wins over the price table.
    reported_usd: float | None = Field(default=None, ge=0)


class ToolCall(BaseModel):
    """One tool invocation the model asked for."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


AdapterResponse.model_rebuild()
