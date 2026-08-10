"""The seam itself: contracts §10.1, AI spec §1 and §3.

Everything AI spec §1's table assigns to "the seam" happens here and nowhere
else -- budget metering and abort, the single output-schema retry, idempotency,
tracing. Adapters translate. The ordering below is the contract:

1. **Load and hash the skill.** Malformed raises before a request is formed, so
   a broken skill costs nothing (AI spec §6).
2. **Look up the idempotency key** in the runtime annex. A hit returns the
   recorded result with **zero** provider calls (PRD F13.5).
3. **Construct the adapter.** A hosted adapter under offline mode raises here,
   before any socket opens (F13.7).
4. **Loop**: provider turn, meter, optional tool calls, meter again.
5. **Validate the output schema**, retrying exactly once (`AGENT_OUTPUT_SCHEMA_RETRIES`).
6. **Record the run** in the annex and return.

**The trace records digests and never payloads.** Every `TraceStep` carries
`detail_sha256` and there is no field for the text -- so "no prompt, output or
tool argument in a trace or a log" is a property of the shape rather than of
this file remembering to redact. AI spec §8.3 is what that buys: an operator
proves *what was asked* without the payload being retrievable from our
artifacts.

**Cost is accurate on every terminal status.** `Meter.charge` runs before
`Meter.check`, so a run that aborts on the response that exhausted it reports
that response's cost. The abort path is exactly when someone asks what it cost.

**Cancellation is a caller-supplied predicate, checked where the meter is**
*(CR-47)*. Contracts §10.1 types `status="cancelled"` and `04` §7.1 case 9
requires the conformance suite to assert it, and **nothing in the declared shapes
could produce it** -- the same class of gap as CR-42 and CR-44. `cancelled` is
therefore a constructor argument rather than a field on `AgentRequest`: the
Protocol §10.1 declares is `run` and `adapters`, so this adds no wire or
persisted shape. It is asked at exactly the two points the budget is asked --
before a provider turn and before a tool call -- because those are the two places
the run is about to spend, and a cancellation honoured anywhere else either
arrives too late to save money or too early to return partial output.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from adopt_agent.adapters.base import build_adapter, describe_adapters
from adopt_agent.annex import AgentRunRecord, AnnexRecords
from adopt_agent.api import (
    Adapter,
    AdapterInfo,
    AdapterResponse,
    AgentRequest,
    AgentResult,
    AgentStatus,
    Cost,
    ToolSpec,
    Trace,
    TraceStep,
)
from adopt_agent.budget import Meter
from adopt_agent.pricing import cost_usd
from adopt_agent.schema_check import (
    SchemaViolation,
    UnsupportedSchema,
    unfence,
    validate_against_schema,
)
from adopt_agent.skills import LoadedSkill, load_skill
from adopt_const import AGENT_OUTPUT_SCHEMA_RETRIES
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, format_timestamp, new_id

__all__ = ["Runner"]

#: One canonical rendering, so a digest is a function of the value and not of
#: whichever dict ordering happened to be in memory. The same rule `02` §11
#: fixes for the export bundle, and for the same reason: two runs that asked
#: the same thing must produce the same `inputs_sha256`.
_JSON: Final[dict[str, Any]] = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": False,
    "default": str,
}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, **_JSON).encode("utf-8")).hexdigest()


def _never() -> bool:
    """The default cancellation predicate: a run nobody cancelled."""
    return False


def _retry_user(base_user: str, violation: str) -> str:
    """The single schema retry's user message: the original ask, plus what broke.

    **The first version of this told the model nothing and made things worse.** It
    re-sent `{"inputs": …, "retry": "previous reply failed its schema"}` — which
    named no violation, so a model had no idea what to change, and which handed a
    JSON-emitting model a *new wrapper to mirror*. Against a real local model the
    observed second reply was
    `{"archetype": "web", "inputs": {…}, "retry": "previous reply failed its schema"}`:
    the correct answer, wrapped in the retry envelope's own keys, failing the
    schema a second time **because of the retry itself**.

    It passed against `fake_recorded` throughout, because a recorded fake replays
    its script whatever it is asked. That is S8's lesson restated at a different
    seam: structural conformance is not evidence of behaviour.

    So the retry re-sends the original request **unchanged** and appends the
    violation. `AGENT_OUTPUT_SCHEMA_RETRIES` is `1`, so this is the only correction
    a run ever gets, and spending it on a message that cannot be acted on is
    spending it on nothing.
    """
    return (
        f"{base_user}\n\n"
        f"Your previous reply did not satisfy the required output schema: {violation}. "
        f"Reply again with a single JSON object that satisfies the schema exactly, "
        f"and nothing else -- no prose, no markdown fences, no extra properties."
    )


def _render_user(skill: LoadedSkill, inputs: Mapping[str, Any]) -> str:
    """The user message: the prompt's template, or canonical JSON of the inputs.

    *(CR-47.)* `04` §5.1 gives `detect-001@1` a user template with four named
    placeholders, and `02` §10.1's `AgentRequest` has no field to carry one -- so
    the template comes from the prompt directory the `skill_ref` already names, and
    is rendered here with the request's own inputs.

    **A missing placeholder raises rather than rendering a hole.** `str.format`
    would raise `KeyError` with just the key name; the refusal below says which
    prompt and which input, because a prompt asking for evidence a caller did not
    supply is a caller/prompt mismatch and the two are versioned separately.

    A skill with no `user.md` renders as before -- canonical JSON, sorted keys --
    which is what an ordinary skill wants and what every existing caller gets.
    """
    if skill.user_template is None:
        return json.dumps(inputs, **_JSON)
    try:
        return skill.user_template.format(**inputs)
    except KeyError as exc:
        raise AdoptError(
            ErrorCode.MANIFEST_INVALID,
            message=(
                f"{skill.ref} declares a user template needing {exc.args[0]!r}, "
                f"which the request's inputs do not supply"
            ),
            hint="A prompt and its caller are versioned separately (AI spec §5.2). "
            "Either the caller is older than the prompt or the prompt is older "
            "than the caller; the fix is a new prompt version, never an edit.",
        ) from exc


class Runner:
    """Realizes contracts §10.1's `AgentRunner`."""

    def __init__(
        self,
        *,
        annex: AnnexRecords,
        scope_ref: str,
        skills_root: Path,
        offline: bool = True,
        adapter_id: str | None = None,
        model: str | None = None,
        endpoint: str | None = None,
        scratch: Path | None = None,
        clock: Clock | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._annex = annex
        self._scope_ref = scope_ref
        self._skills_root = skills_root
        # Offline defaults to **True**, matching the CLI (PRD F16, `03` §3). A
        # seam whose default were online would make "offline by default" a
        # property of whoever constructed it.
        self._offline = offline
        self._adapter_id = adapter_id
        self._model = model
        self._endpoint = endpoint
        self._scratch = scratch
        self._clock = clock if clock is not None else SystemClock()
        #: Asked before every provider turn and every tool call. Absent means
        #: "never cancelled", which is why the default is a constant rather than
        #: a flag the loop has to branch on twice per turn.
        self._cancelled = cancelled if cancelled is not None else _never

    def adapters(self) -> list[AdapterInfo]:
        """Contracts §14's `adopt agent adapters` payload, unshaped."""
        return describe_adapters(offline=self._offline, model=self._model, endpoint=self._endpoint)

    def run(self, request: AgentRequest) -> AgentResult:
        skill = load_skill(request.skill_ref, root=self._skills_root, scratch=self._scratch)
        inputs_sha256 = _digest(request.inputs)

        recorded = self._annex.find_run(
            scope_ref=self._scope_ref, idempotency_key=request.idempotency_key
        )
        if recorded is not None:
            return _replayed(recorded)

        adapter_id = request.adapter or self._adapter_id
        if adapter_id is None:
            raise AdoptError(
                ErrorCode.AGENT_ADAPTER_UNKNOWN,
                message="no adapter was requested and none is configured",
                hint="Set ADOPT_ADAPTER, or name one on the request. The seam never "
                "substitutes: a silent substitution changes cost, behaviour and "
                "data residency without the operator knowing (AI spec §2).",
            )
        # Constructing is where a hosted adapter under offline mode raises --
        # before any request is formed and before any socket opens (F13.7).
        adapter = build_adapter(
            adapter_id, offline=self._offline, model=self._model, endpoint=self._endpoint
        )

        meter = Meter(request.budget, clock=self._clock)
        steps: list[TraceStep] = []
        trace = Trace(
            adapter=adapter.id,
            model=adapter.model(),
            params_hash=adapter.params_hash(),
            skill_ref=skill.ref,
            skill_sha256=skill.sha256,
            inputs_sha256=inputs_sha256,
            steps=[],
        )

        status, output, error = self._drive(request, skill, adapter, meter, steps)
        trace = trace.model_copy(update={"steps": steps})
        cost = meter.cost()

        self._record(request, trace, cost, status)
        return AgentResult(
            status=status, output=output, artifacts=[], cost=cost, trace=trace, error=error
        )

    # ---------------------------------------------------------------- driving

    def _step(
        self, steps: list[TraceStep], kind: str, detail: object, tokens: int | None = None
    ) -> None:
        """Append one trace step. `detail` is hashed, never stored."""
        steps.append(
            TraceStep(
                seq=len(steps),
                type=kind,  # type: ignore[arg-type]  # kind is a TraceStepType literal
                at=self._clock.now(),
                detail_sha256=_digest(detail),
                tokens=tokens,
            )
        )

    def _drive(
        self,
        request: AgentRequest,
        skill: LoadedSkill,
        adapter: Adapter,
        meter: Meter,
        steps: list[TraceStep],
    ) -> tuple[AgentStatus, str | dict[str, Any] | None, AdoptError | None]:
        """The provider/tool loop plus the single schema retry."""
        by_name = {tool.name: tool for tool in request.tools}
        system = skill.body
        #: The request as first asked. The retry re-sends **this**, with the
        #: violation appended -- see `_retry_user`.
        base_user = _render_user(skill, request.inputs)
        user = base_user
        # A prompt that declares its own output schema (`04` §5's table) has it
        # enforced without every caller remembering to pass one; an explicit
        # `request.output_schema` still wins, because a caller asking for a
        # narrower shape is asking on purpose.
        output_schema = (
            request.output_schema if request.output_schema is not None else skill.output_schema
        )
        tool_results: list[Mapping[str, Any]] = []
        schema_attempts = 0
        last_text = ""

        while True:
            # Before the request is formed, so a cancelled run costs nothing
            # further. `04` §7.1 case 9 asserts the trace records `abort`, and
            # the partial output of the previous turn is still returned -- a
            # cancellation that discarded what was already paid for would make
            # cancelling more expensive than waiting.
            if self._cancelled():
                self._step(steps, "abort", {"reason": "cancelled"})
                return "cancelled", (last_text or None), None

            self._step(steps, "request", {"system": system, "user": user})
            try:
                response = adapter.complete(
                    system=system,
                    user=user,
                    tools=list(request.tools),
                    tool_results=tool_results,
                    max_tokens=request.budget.max_tokens,
                )
            except AdoptError as exc:
                self._step(steps, "abort", {"error": exc.code.value})
                return "error", None, exc

            usd = (
                response.reported_usd
                if response.reported_usd is not None
                else cost_usd(
                    adapter.id,
                    adapter.model(),
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )
            )
            # Charge, *then* check. The other order reports `budget_exhausted`
            # beside a cost that excludes the response which exhausted it.
            meter.charge(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                usd=usd,
            )
            last_text = response.text
            self._step(
                steps,
                "response",
                {"text": response.text},
                tokens=response.input_tokens + response.output_tokens,
            )

            verdict = meter.check()
            if verdict.exhausted:
                self._step(steps, "abort", {"cap": verdict.cap, "detail": verdict.detail})
                return "budget_exhausted", last_text, None

            if response.tool_calls:
                outcome, tool_results = self._run_tools(response, by_name, meter, steps)
                if outcome is not None:
                    return outcome, last_text, None
                continue

            if output_schema is None:
                return "ok", last_text, None

            try:
                # `unfence` strips a whole-output markdown fence and nothing
                # else. A frontier model returned the correct object wrapped in
                # ```json twice, burning the single retry `04` §3 allows, and a
                # fence is a chat-transport artifact rather than content (CR-52).
                parsed = json.loads(unfence(last_text))
            except json.JSONDecodeError:
                parsed = None
            violation: str | None = None
            if parsed is None:
                violation = "output is not JSON"
            else:
                try:
                    validate_against_schema(parsed, output_schema)
                except SchemaViolation as exc:
                    violation = str(exc)
                except UnsupportedSchema as exc:
                    # The caller's schema is unenforceable. Retrying would burn
                    # the single retry on a request that can never pass.
                    self._step(steps, "abort", {"error": str(exc)})
                    return (
                        "error",
                        last_text,
                        AdoptError(
                            ErrorCode.AGENT_OUTPUT_SCHEMA,
                            message=str(exc),
                            hint="The schema, not the model, is the problem.",
                        ),
                    )
            if violation is None:
                return "ok", parsed, None

            if schema_attempts >= AGENT_OUTPUT_SCHEMA_RETRIES:
                # AI spec §3: return the last raw output as text, with the code.
                return (
                    "error",
                    last_text,
                    AdoptError(
                        ErrorCode.AGENT_OUTPUT_SCHEMA,
                        message=f"output failed its schema twice: {violation}",
                        hint="The raw output is returned as text so it is not lost.",
                    ),
                )
            schema_attempts += 1
            self._step(steps, "validation_retry", {"violation": violation})
            user = _retry_user(base_user, violation)

    def _run_tools(
        self,
        response: AdapterResponse,
        by_name: Mapping[str, ToolSpec],
        meter: Meter,
        steps: list[TraceStep],
    ) -> tuple[AgentStatus | None, list[Mapping[str, Any]]]:
        """Answer the model's tool calls.

        Returns `(terminal status or None, results)`. `None` means the loop
        continues; a status means it stopped, and the caller returns it. Carrying
        the status rather than a boolean is what lets a cancellation and a budget
        crossing both stop here without the caller having to guess which
        happened -- they are different answers to "why did this end".
        """
        results: list[Mapping[str, Any]] = []
        for call in response.tool_calls:
            if self._cancelled():
                self._step(steps, "abort", {"reason": "cancelled"})
                return "cancelled", results
            # Checked **before** the call, not after: this is where the next
            # provider turn is committed to, and a meter checked only after
            # responses discovers the crossing one billed round trip late.
            tool_verdict = meter.may_call_tool()
            if tool_verdict.exhausted:
                self._step(steps, "abort", {"cap": tool_verdict.cap})
                return "budget_exhausted", results
            meter.record_tool_call()
            self._step(steps, "tool_call", {"name": call.name, "arguments": call.arguments})
            tool = by_name.get(call.name)
            if tool is None:
                payload = {"error": f"no tool named {call.name!r} was declared"}
            else:
                try:
                    payload = {"result": tool.handler(dict(call.arguments))}
                except Exception as exc:
                    # A tool that raises is surfaced to the model as a tool
                    # result, never as a crash. The model may recover; the
                    # process may not be taken down by a caller's handler.
                    payload = {"error": f"{type(exc).__name__}: {exc}"}
            self._step(steps, "tool_result", {"id": call.id, "payload": payload})
            results.append({"id": call.id, "name": call.name, "content": payload})
        return None, results

    # ---------------------------------------------------------------- annex

    def _record(self, request: AgentRequest, trace: Trace, cost: Cost, status: AgentStatus) -> None:
        self._annex.record_run(
            AgentRunRecord(
                id=new_id("ag"),
                scope_ref=self._scope_ref,
                idempotency_key=request.idempotency_key,
                skill_ref=trace.skill_ref,
                skill_sha256=trace.skill_sha256,
                inputs_sha256=trace.inputs_sha256,
                adapter=trace.adapter,
                model=trace.model,
                params_hash=trace.params_hash,
                status=status,
                input_tokens=cost.input_tokens,
                output_tokens=cost.output_tokens,
                cost_usd=cost.usd,
                wall_ms=cost.wall_ms,
                trace_json=trace.model_dump_json(),
                output_ref=None,
                created_at=format_timestamp(self._clock.now()),
            )
        )


def _replayed(recorded: AgentRunRecord) -> AgentResult:
    """Rebuild the recorded result. **Zero provider calls** (PRD F13.5).

    The output is `None` rather than reconstructed text: `agent_run` stores
    `output_ref`, a blob reference, and never the output itself (contracts §12).
    A replay that invented an output would be the annex quietly becoming the
    place a client's model responses accumulate in plain text.
    """
    return AgentResult(
        status=recorded.status,  # type: ignore[arg-type]  # a recorded AgentStatus
        output=None,
        artifacts=[],
        cost=Cost(
            input_tokens=recorded.input_tokens or 0,
            output_tokens=recorded.output_tokens or 0,
            usd=recorded.cost_usd or 0.0,
            wall_ms=recorded.wall_ms or 0,
        ),
        trace=Trace.model_validate_json(recorded.trace_json),
        error=None,
    )
