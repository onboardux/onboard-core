"""The thirteen adapter-conformance cases. AI spec §7.1, one test each.

*Fails when* an adapter or the seam breaks the contract every model call in the
programme depends on. *Matters because* PRD F13.8 and N12 make "green on ≥ 2
adapters, one local" a release gate: the BYO-model claim is decorative unless a
second adapter is exercised, and `conformance-matrix` is the instrument. *No other
instrument catches it because* `tests/unit/test_agent_runner.py` drives the seam
through one adapter -- it would pass with every real adapter broken in the same
place, which is precisely the single-vendor dependency the seam exists to prevent.

**This suite tests the adapter contract, not model quality** (`04` §7.1). That
distinction is what makes the gate achievable with a small local model, and it is
why every assertion below is about shape, ordering, counting or refusal -- never
about whether an answer was *good*. A suite only a frontier model could pass would
quietly recreate the dependency it is meant to disprove.

**Case numbers are `04` §7.1's row numbers** and are in the test names on purpose:
a failing CI line should name the row of the table it violated without anyone
opening this file.
"""

import datetime as _dt
import json
from pathlib import Path
from typing import Any

import conformance_harness as harness
import pytest

from adopt_agent import Budget, ToolSpec
from adopt_agent.pricing import price_for
from adopt_agent.schema_check import validate_against_schema
from adopt_const import AGENT_ABORT_GRACE_MS
from adopt_obs import ErrorCode

pytestmark = pytest.mark.conformance

#: A satisfiable, strict, closed schema. The archetype vocabulary is `02` §2.1's,
#: so a model that answers this case at all answers it in the product's own
#: vocabulary rather than in one invented for a test.
_ARCHETYPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["archetype"],
    "properties": {"archetype": {"enum": ["web", "platform", "lowcode", "data", "ai"]}},
}

#: A schema **no** JSON value can satisfy: `impossible` is required and
#: `additionalProperties` is false with no properties declared. This is what makes
#: case 3 deterministic against a real model -- the retry is forced by the schema
#: rather than by hoping the model answers wrongly twice, which no prompt can
#: guarantee and which would make the case flaky for the wrong reason.
_UNSATISFIABLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["impossible"],
    "properties": {},
}

_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fact"],
    "properties": {"fact": {"type": "string"}},
}


def _recording_tool(seen: list[dict[str, Any]], *, explode: bool = False) -> ToolSpec:
    def handler(arguments: dict[str, Any]) -> str:
        seen.append(arguments)
        if explode:
            raise RuntimeError("the caller's handler failed")
        return "recorded"

    return ToolSpec(
        name="record_fact",
        description="Record one fact. Call this exactly once with the fact you were given.",
        input_schema=_TOOL_SCHEMA,
        handler=handler,
    )


def _tool_call(fact: str = "the sky is blue", call_id: str = "c1") -> dict[str, Any]:
    return {"id": call_id, "name": "record_fact", "arguments": {"fact": fact}}


# --------------------------------------------------------------- 1. echo


def test_case_01_structured_echo_returns_the_text_unchanged(
    adapter_id: str, tmp_path: Path
) -> None:
    """Text returned unchanged, no wrapper leakage.

    The assertion is that the adapter hands back the provider's text and nothing
    of its own -- no JSON envelope, no markdown fence, no role prefix. A wrapper
    is invisible to a caller reading `output` casually and breaks every downstream
    parse, so it is checked at the seam's boundary where it would first appear.
    """
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system="Reply with exactly the word ECHO. No punctuation, no explanation, no formatting.",
        inputs={"say": "ECHO"},
        recorded=[harness.recorded_turn("ECHO")],
    )

    assert run.result.status == "ok", run.why
    assert isinstance(run.result.output, str)
    text = run.result.output.strip()
    assert text == "ECHO"
    assert not text.startswith("{"), "a JSON wrapper leaked into a text response"
    assert "```" not in text, "a markdown fence leaked into a text response"


# --------------------------------------------------------------- 2. schema ok


def test_case_02_json_validates_and_output_is_a_dict(adapter_id: str, tmp_path: Path) -> None:
    """Output validates against the schema, and `output` is a `dict`.

    The type matters as much as the validity: a seam that returned the raw JSON
    *text* on success would satisfy "it validated" while making every caller parse
    it again, and `02` §10.1 types `output` as `str | dict | None` precisely so
    that a schema-validated result arrives parsed.
    """
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system=(
            "Reply with a single JSON object matching the schema and nothing else: "
            '{"archetype": "web"}. No prose, no markdown fences, no preamble.'
        ),
        inputs={"evidence": "a Django service under version control"},
        recorded=[harness.recorded_turn(json.dumps({"archetype": "web"}))],
        output_schema=_ARCHETYPE_SCHEMA,
    )

    # `04` §3 returns the last raw output as text on a double schema failure,
    # and this case's inputs are fixed synthetic fixtures -- there is no client
    # content here to leak. Without it, "output is not JSON" cannot distinguish
    # a markdown fence from a prose preamble, and the two have different remedies.
    assert run.result.status == "ok", f"{run.why} | raw={str(run.result.output)[:300]!r}"
    assert isinstance(run.result.output, dict)
    validate_against_schema(run.result.output, _ARCHETYPE_SCHEMA)


# --------------------------------------------------------------- 3. one retry


def test_case_03_schema_failure_retries_exactly_once(adapter_id: str, tmp_path: Path) -> None:
    """Exactly one retry, then `AGENT_OUTPUT_SCHEMA`.

    Asserted by **equality** on the retry steps rather than `>= 1`: "at least one"
    passes an implementation that retries forever, which is the unbounded spend the
    budget exists to stop. The raw output is returned as text rather than discarded
    (`04` §3), so a caller can see what the model actually said.
    """
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system="Reply with a single JSON object and nothing else.",
        inputs={"anything": "at all"},
        recorded=[harness.recorded_turn('{"a":1}'), harness.recorded_turn('{"a":2}')],
        output_schema=_UNSATISFIABLE_SCHEMA,
    )

    retries = [step for step in run.result.trace.steps if step.type == "validation_retry"]
    assert len(retries) == 1, run.why
    assert run.result.status == "error", run.why
    assert run.result.error is not None
    assert run.result.error.code is ErrorCode.AGENT_OUTPUT_SCHEMA
    assert isinstance(run.result.output, str)


# --------------------------------------------------------------- 4-6. tools


def test_case_04_a_single_tool_call_is_invoked_once_with_valid_arguments(
    adapter_id: str, tmp_path: Path
) -> None:
    """Invoked once, with arguments that satisfy the tool's own input schema.

    Validating the arguments is the half that is easy to omit and expensive to
    omit: a tool handler is the caller's code, and handing it arguments nobody
    checked makes the seam the place model output reaches a caller unvalidated.
    """
    seen: list[dict[str, Any]] = []
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system=(
            "You must call the tool `record_fact` exactly once, passing the fact you are "
            "given as the `fact` argument. Then reply with the word DONE."
        ),
        inputs={"fact": "the sky is blue"},
        recorded=[
            harness.recorded_turn("", tool_calls=[_tool_call()]),
            harness.recorded_turn("DONE"),
        ],
        tools=[_recording_tool(seen)],
        budget=Budget(max_usd=1.0, max_wall_seconds=120, max_tool_calls=2),
    )

    assert run.result.status == "ok", run.why
    assert len(seen) == 1, f"the tool was invoked {len(seen)} times, not once"
    validate_against_schema(seen[0], _TOOL_SCHEMA)


def test_case_05_a_multi_turn_tool_loop_terminates_and_respects_the_cap(
    adapter_id: str, tmp_path: Path
) -> None:
    """Terminates, and `max_tool_calls` is respected.

    Termination is the claim that cannot be made by inspection: a tool loop is the
    one place the seam can run forever, and the cap is what bounds it. The case
    scripts *more* tool calls than the budget allows, so a seam that honoured the
    cap only after the fact would be caught by the count rather than by hanging.

    **At least one call is required, and that lower bound was missing.** The first
    run of this suite against a real model passed this case with `seen == []` --
    the model never called the tool at all, and "respect the cap" is trivially true
    of a run that made no calls. The case was reporting green having exercised
    nothing. `1 <= len(seen) <= 2` is the actual claim: the loop ran, and it
    stopped where the budget said.
    """
    seen: list[dict[str, Any]] = []
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system=(
            "Call the tool `record_fact` once for each fact you are given, one call per "
            "turn, then reply with the word DONE."
        ),
        inputs={"facts": ["one", "two", "three"]},
        recorded=[
            harness.recorded_turn("", tool_calls=[_tool_call("one", "c1")]),
            harness.recorded_turn("", tool_calls=[_tool_call("two", "c2")]),
            harness.recorded_turn("", tool_calls=[_tool_call("three", "c3")]),
            harness.recorded_turn("DONE"),
        ],
        tools=[_recording_tool(seen)],
        budget=Budget(max_usd=1.0, max_wall_seconds=120, max_tool_calls=2),
    )

    assert seen, f"the loop made no tool call at all, so nothing about it was tested -- {run.why}"
    assert len(seen) <= 2, f"{len(seen)} tool calls were made against a cap of 2"
    assert run.result.status in {"ok", "budget_exhausted"}
    if run.result.status == "budget_exhausted":
        assert run.result.trace.steps[-1].type == "abort"


def test_case_06_a_tool_that_raises_is_surfaced_not_crashed(
    adapter_id: str, tmp_path: Path
) -> None:
    """The exception becomes a tool result. The process survives.

    A caller's handler must not be able to take the seam down, and the model may
    still recover from a tool error -- so the failure is reported back into the
    conversation rather than raised. The `tool_result` step is what proves the
    surface happened rather than the exception being swallowed.
    """
    seen: list[dict[str, Any]] = []
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system=(
            "Call the tool `record_fact` once with the fact you are given. If it reports an "
            "error, reply with the word RECOVERED and do not call it again."
        ),
        inputs={"fact": "the sky is blue"},
        recorded=[
            harness.recorded_turn("", tool_calls=[_tool_call()]),
            harness.recorded_turn("RECOVERED"),
        ],
        tools=[_recording_tool(seen, explode=True)],
        budget=Budget(max_usd=1.0, max_wall_seconds=120, max_tool_calls=2),
    )

    kinds = [step.type for step in run.result.trace.steps]
    assert "tool_result" in kinds, f"a raising tool produced no tool_result step -- {run.why}"
    assert run.result.status == "ok", run.why


# --------------------------------------------------------------- 7-8. budgets


def test_case_07_token_exhaustion_returns_partial_output_and_accurate_cost(
    adapter_id: str, tmp_path: Path
) -> None:
    """`budget_exhausted`, partial output, and a cost that is not zero.

    **"Cost > 0" is asserted as token counts, not dollars**, and that is not a
    weakening. `pricing.py` deliberately carries no row for a locally served model
    -- a local model costs the operator's own hardware, and `None` is the honest
    answer to "what did that cost in dollars we can see". Asserting `usd > 0` here
    would fail the local adapter for being priced correctly, so the case asserts
    dollars only where the model is priced.
    """
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system="Reply with one short sentence.",
        inputs={"say": "anything"},
        recorded=[harness.recorded_turn("a partial answer", input_tokens=40, output_tokens=40)],
        budget=Budget(max_usd=1000.0, max_wall_seconds=120, max_tokens=8),
    )

    assert run.result.status == "budget_exhausted", run.why
    assert run.result.output is not None, "an abort discarded the partial output"
    cost = run.result.cost
    assert cost.input_tokens + cost.output_tokens > 0
    if price_for(run.result.trace.adapter, run.result.trace.model) is not None:
        assert cost.usd > 0


def test_case_08_wall_clock_exhaustion_aborts_within_the_grace(
    adapter_id: str, tmp_path: Path
) -> None:
    """The run stops within `AGENT_ABORT_GRACE_MS` of crossing the wall cap.

    Driven by a clock that advances on every reading rather than by sleeping
    (implementation spec §5 bans sleeps): the seam reads the clock once per trace
    step, so elapsed time is a deterministic function of how far the run got.

    **The assertion is the gap between the crossing and the abort, not the total
    wall time.** The first version of this case asserted
    `wall_ms <= cap + grace` and failed at `12000 <= 4000` -- because with a
    coarse tick the total is dominated by *how many times the clock was read*,
    which is a property of the instrument and not of the seam. Measuring from the
    step before the abort asserts what `04` §3 actually promises -- that the seam
    stopped promptly once it knew -- and is independent of how long a provider
    took or of how many readings a run makes.

    The schema no reply can satisfy is what forces a second turn, so the crossing
    happens mid-run rather than at the end of a single round trip.
    """
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system="Reply with a single JSON object and nothing else.",
        inputs={"anything": "at all"},
        recorded=[harness.recorded_turn('{"a":1}'), harness.recorded_turn('{"a":2}')],
        output_schema=_UNSATISFIABLE_SCHEMA,
        budget=Budget(max_usd=1000.0, max_wall_seconds=1),
        clock_tick=_dt.timedelta(milliseconds=250),
    )

    assert run.result.status == "budget_exhausted", run.why
    steps = run.result.trace.steps
    assert steps[-1].type == "abort"
    gap_ms = (steps[-1].at - steps[-2].at).total_seconds() * 1000
    assert gap_ms <= AGENT_ABORT_GRACE_MS, (
        f"the seam took {gap_ms:.0f}ms to stop after the crossing, over the "
        f"{AGENT_ABORT_GRACE_MS}ms grace"
    )
    # And it stopped rather than taking one more billed round trip.
    assert steps[-2].type != "request"


# --------------------------------------------------------------- 9. cancel


def test_case_09_cancellation_stops_the_run_and_the_trace_records_abort(
    adapter_id: str, tmp_path: Path
) -> None:
    """An in-flight run is cancelled; the trace records `abort`.

    Cancellation is asked at the same two points the budget is (CR-47), so the
    case forces a second turn -- via a schema no reply can satisfy -- and cancels
    before it. One provider turn happened and the second never did, which is what
    "in-flight" means and what distinguishes this from refusing to start.
    """
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system="Reply with a single JSON object and nothing else.",
        inputs={"anything": "at all"},
        recorded=[harness.recorded_turn('{"a":1}'), harness.recorded_turn("never reached")],
        output_schema=_UNSATISFIABLE_SCHEMA,
        cancel_after=1,
    )

    assert run.result.status == "cancelled", run.why
    assert run.result.trace.steps[-1].type == "abort"
    assert run.provider_calls == 1, (
        f"{run.provider_calls} provider turns happened; a cancellation before the second "
        "turn must not have paid for it"
    )


# --------------------------------------------------------------- 10-11. records


def test_case_10_cost_matches_the_token_counts(adapter_id: str, tmp_path: Path) -> None:
    """Non-zero, monotonic, and equal to what the price table says of the tokens.

    Monotonicity across a run is the property `tests/property/
    test_agent_cost_monotonic.py` quantifies; here the claim is *agreement*: the
    dollars reported are exactly what this adapter's model costs for the tokens
    reported. A cost computed from anything else -- a different model's row, a
    stale total -- diverges here and nowhere else.
    """
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system="Reply with one short sentence.",
        inputs={"say": "anything"},
        recorded=[harness.recorded_turn("a sentence", input_tokens=30, output_tokens=12)],
    )

    cost = run.result.cost
    assert cost.input_tokens > 0, run.why
    assert cost.input_tokens + cost.output_tokens > 0
    row = price_for(run.result.trace.adapter, run.result.trace.model)
    if row is not None:
        expected = (
            cost.input_tokens * row.input_usd_per_million
            + cost.output_tokens * row.output_usd_per_million
        ) / 1_000_000
        assert cost.usd == pytest.approx(expected, abs=1e-6)
    else:
        # An unpriced model is not free: `price_for` returns `None` and the cost
        # in dollars is what we know, which is nothing. Zero here is the honest
        # reading, and it is asserted so that a price row appearing later cannot
        # silently change what this case means.
        assert cost.usd == 0.0


def test_case_11_the_trace_carries_every_scalar_an_audit_needs(
    adapter_id: str, tmp_path: Path
) -> None:
    """adapter, model, `params_hash`, `skill_sha256`, `inputs_sha256`, all present.

    These six are exactly `02` §12's `agent_run` columns beside `trace_json`. A
    trace missing one cannot be audited inside a client environment without the
    provider, which is the entire reason it is kept.
    """
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system="Reply with one short sentence.",
        inputs={"say": "anything"},
        recorded=[harness.recorded_turn("a sentence")],
    )

    trace = run.result.trace
    assert trace.adapter == adapter_id
    for field_name in ("model", "params_hash", "skill_ref", "skill_sha256", "inputs_sha256"):
        assert getattr(trace, field_name), f"trace.{field_name} is empty"
    assert trace.steps, "a run produced no trace steps at all"


# --------------------------------------------------------------- 12. replay


def test_case_12_a_replay_returns_the_recorded_result_with_no_provider_call(
    adapter_id: str, tmp_path: Path
) -> None:
    """Same key, recorded result, zero provider calls.

    **The fixture records one turn and the run is driven twice.** For the recorded
    fake that makes a second provider call impossible rather than merely
    unobserved -- the script would be exhausted and the adapter raises. For every
    adapter, the replayed trace being *identical* is the check: a re-driven run
    would produce new step timestamps, so equality of the serialized trace is
    equality of the run.
    """
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system="Reply with one short sentence.",
        inputs={"say": "anything"},
        recorded=[harness.recorded_turn("a sentence")],
        replay=True,
    )

    assert run.replayed is not None
    assert run.replayed.trace.model_dump_json() == run.result.trace.model_dump_json()
    assert run.replayed.cost.input_tokens == run.result.cost.input_tokens
    # `02` §12: the annex stores `output_ref`, never the output, so a replay
    # returns `None` rather than inventing the text back. Asserted so nobody
    # "fixes" it into the place client model output accumulates in plain text.
    assert run.replayed.output is None


# --------------------------------------------------------------- 13. privacy


def test_case_13_no_payload_reaches_the_trace_or_any_log_line(
    adapter_id: str, tmp_path: Path
) -> None:
    """No prompt text, output text or tool argument in the trace or a log line.

    Both halves of PRD N11 in one case, because they fail independently: the trace
    is a persisted artifact and the log is a stream, and a leak into either ends
    the claim that this can run inside a client environment. Asserted over the
    **serialized** trace, which cannot be evaded by adding a field somewhere the
    case did not think to look.
    """
    secret = "planted-conformance-secret"
    seen: list[dict[str, Any]] = []
    run = harness.drive(
        adapter_id,
        tmp_path=tmp_path,
        system=f"The passphrase is {secret}. Reply with the word OK.",
        inputs={"passphrase": secret},
        recorded=[
            harness.recorded_turn("", tool_calls=[_tool_call(secret)]),
            harness.recorded_turn(f"OK {secret}"),
        ],
        tools=[_recording_tool(seen)],
        budget=Budget(max_usd=1.0, max_wall_seconds=120, max_tool_calls=2),
    )

    serialized = run.result.trace.model_dump_json()
    assert secret in json.dumps({"system": secret}), "the guard itself is broken"
    assert run.result.trace.steps, "an empty trace would satisfy this case vacuously"
    assert secret not in serialized, "the planted passphrase reached the trace"
    assert secret not in run.logs, "the planted passphrase reached a log line"
