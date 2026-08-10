"""The `AgentRunner` seam: contracts §10.1's obligations, one per test.

*Fails when* an obligation the seam owns moves or breaks -- a replay calls a
provider, the schema retry runs twice or not at all, a payload reaches a trace,
a budget crossing is discovered a round trip late, a tool exception takes the
process down, or a hosted adapter is constructible offline. *Matters because* AI
spec §1 makes this the one door every model call in the programme passes
through: an adapter that could own any of these would own it differently in each
of the four adapters, and the one that got it wrong would be the one nobody ran.
*No other instrument catches them because* the conformance suite proves an
adapter satisfies the contract, and these are the parts of the contract the
**seam** implements -- the suite would pass against every adapter with all of
them broken in the same place.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from adopt_agent import AgentRequest, Budget, Runner, ToolSpec
from adopt_agent.adapters.base import build_adapter, describe_adapters
from adopt_agent.annex import AnnexRecords
from adopt_agent.runner import _retry_user
from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_store.annex import open_annex

pytestmark = pytest.mark.unit

_SCOPE = "northwind/acme-erp"
_SKILL = "---\nname: detect\ndescription: Classify a system.\n---\n\nYou classify systems.\n"


def _turn(
    text: str = "hello",
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    input_tokens: int = 10,
    output_tokens: int = 5,
    reported_usd: float | None = None,
) -> dict[str, Any]:
    return {
        "text": text,
        "tool_calls": tool_calls or [],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reported_usd": reported_usd,
    }


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    (root / "detect" / "v1").mkdir(parents=True)
    (root / "detect" / "v1" / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    return root


@pytest.fixture
def annex(tmp_path: Path) -> Iterator[AnnexRecords]:
    with open_annex(tmp_path / ".adopt" / "runtime.db") as records:
        yield records


def _fixture(tmp_path: Path, *turns: dict[str, Any]) -> str:
    path = tmp_path / "recorded.json"
    path.write_text(json.dumps({"turns": list(turns)}), encoding="utf-8")
    return str(path)


def _runner(
    annex: AnnexRecords, skills_root: Path, endpoint: str, clock: ManualClock | None = None
) -> Runner:
    return Runner(
        annex=annex,
        scope_ref=_SCOPE,
        skills_root=skills_root,
        offline=True,
        adapter_id="fake_recorded",
        endpoint=endpoint,
        clock=clock,
    )


def _request(**overrides: Any) -> AgentRequest:
    base: dict[str, Any] = {
        "skill_ref": "detect/v1",
        "inputs": {"path": "/repo"},
        "budget": Budget(max_usd=1.0, max_wall_seconds=30),
        "idempotency_key": "k-1",
    }
    return AgentRequest(**{**base, **overrides})


# ------------------------------------------------------------------ the seam


def test_a_plain_run_returns_ok_with_the_model_text(
    tmp_path: Path, annex: AnnexRecords, skills_root: Path
) -> None:
    runner = _runner(annex, skills_root, _fixture(tmp_path, _turn("web")))

    result = runner.run(_request())

    assert result.status == "ok"
    assert result.output == "web"


def test_a_replay_returns_the_recorded_result_with_zero_provider_calls(
    tmp_path: Path, annex: AnnexRecords, skills_root: Path
) -> None:
    """PRD F13.5, and the count is the assertion.

    A replay that returned the right answer *after* calling the provider looks
    identical from the outside, so the fixture records exactly one turn: a
    second provider call would exhaust the script and raise.
    """
    endpoint = _fixture(tmp_path, _turn("web"))
    _runner(annex, skills_root, endpoint).run(_request())

    replayed = _runner(annex, skills_root, endpoint).run(_request())

    assert replayed.status == "ok"
    assert replayed.trace.skill_sha256 != ""
    # The recorded run is returned rather than re-driven; the single recorded
    # turn was consumed by the first run and never asked for again.


def test_the_schema_retry_happens_exactly_once(
    tmp_path: Path, annex: AnnexRecords, skills_root: Path
) -> None:
    """`AGENT_OUTPUT_SCHEMA_RETRIES` is 1: not zero, and not "until it works".

    Asserted by equality on the retry steps rather than by `>= 1` -- "at least
    one" passes an implementation that retries forever, which is exactly the
    unbounded spend the budget exists to stop.
    """
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    runner = _runner(annex, skills_root, _fixture(tmp_path, _turn("nope"), _turn("still nope")))

    result = runner.run(_request(output_schema=schema))

    retries = [step for step in result.trace.steps if step.type == "validation_retry"]
    assert len(retries) == 1
    assert result.status == "error"
    assert result.error is not None
    assert result.error.code is ErrorCode.AGENT_OUTPUT_SCHEMA
    assert result.output == "still nope"  # the raw output is returned, not lost


def test_a_retry_that_succeeds_returns_the_parsed_object(
    tmp_path: Path, annex: AnnexRecords, skills_root: Path
) -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    runner = _runner(
        annex, skills_root, _fixture(tmp_path, _turn("nope"), _turn(json.dumps({"a": "yes"})))
    )

    result = runner.run(_request(output_schema=schema))

    assert result.status == "ok"
    assert result.output == {"a": "yes"}


def test_the_trace_carries_every_scalar_an_audit_needs(
    tmp_path: Path, annex: AnnexRecords, skills_root: Path
) -> None:
    """AI spec §7.1 case 11. A trace missing one of these cannot be audited
    inside a client environment, which is the whole reason it is kept."""
    runner = _runner(annex, skills_root, _fixture(tmp_path, _turn()))

    trace = runner.run(_request()).trace

    assert trace.adapter == "fake_recorded"
    assert trace.model
    assert trace.params_hash
    assert trace.skill_ref == "detect/v1"
    assert trace.skill_sha256
    assert trace.inputs_sha256


def test_no_payload_text_reaches_the_trace(
    tmp_path: Path, annex: AnnexRecords, skills_root: Path
) -> None:
    """AI spec §7.1 case 13 and §8.3, asserted over the **serialized** trace.

    Checking field by field would pass an implementation that stashed the text
    somewhere the test did not think to look; serializing the whole thing and
    searching it is the check that cannot be evaded by adding a field.
    """
    secret = "sk-planted-secret-value"
    runner = _runner(annex, skills_root, _fixture(tmp_path, _turn(secret)))

    result = runner.run(_request(inputs={"token": secret}))

    assert secret not in result.trace.model_dump_json()
    assert "You classify systems" not in result.trace.model_dump_json()


def test_cost_is_accurate_on_the_budget_abort_path(
    tmp_path: Path, annex: AnnexRecords, skills_root: Path
) -> None:
    """AI spec §7.1 case 7: partial output, and cost > 0.

    A meter that checked before charging would report `budget_exhausted` beside
    a cost that excludes the response which exhausted it -- which is the one
    number an operator wants at exactly that moment.
    """
    runner = _runner(annex, skills_root, _fixture(tmp_path, _turn("partial", reported_usd=5.0)))

    result = runner.run(_request(budget=Budget(max_usd=1.0, max_wall_seconds=30)))

    assert result.status == "budget_exhausted"
    assert result.output == "partial"
    assert result.cost.usd > 0


def test_the_retry_names_the_violation_and_keeps_the_original_request() -> None:
    """The single schema retry has to be actionable, or it is not a retry.

    *Fails when* the retry stops telling the model what was wrong, or starts
    re-shaping the request into an envelope of its own. *Matters because*
    `AGENT_OUTPUT_SCHEMA_RETRIES` is `1` -- this is the only correction a run ever
    gets, and one that names no violation spends it on nothing. *No other
    instrument catches it because* `fake_recorded` replays its script whatever it
    is asked, so **every test in this file passed while the retry was broken**.

    It was broken. The first version re-sent
    `{"inputs": …, "retry": "previous reply failed its schema"}`, and against a
    real local model the observed second reply was the right answer wrapped in
    those very keys -- `{"archetype": "web", "inputs": {…}, "retry": "…"}` --
    failing the schema again *because of the retry*. Asserted here as a pure
    function, which is the level the defect actually lived at.
    """
    retried = _retry_user('{"evidence":"a Django service"}', "$: has undeclared property 'x'")

    assert retried.startswith('{"evidence":"a Django service"}')
    assert "has undeclared property 'x'" in retried
    # The old envelope's keys must not reappear: they are what the model mirrored.
    assert '"retry":' not in retried
    assert '"inputs":' not in retried


# ------------------------------------------------------------------ tools


def test_a_tool_is_invoked_once_with_the_models_arguments(
    tmp_path: Path, annex: AnnexRecords, skills_root: Path
) -> None:
    """AI spec §7.1 case 4."""
    seen: list[dict[str, Any]] = []
    tool = ToolSpec(
        name="lookup",
        description="Look something up.",
        input_schema={"type": "object"},
        handler=lambda args: seen.append(args) or "done",
    )
    endpoint = _fixture(
        tmp_path,
        _turn("", tool_calls=[{"id": "c1", "name": "lookup", "arguments": {"q": "x"}}]),
        _turn("answered"),
    )
    runner = _runner(annex, skills_root, endpoint)

    result = runner.run(
        _request(tools=[tool], budget=Budget(max_usd=1.0, max_wall_seconds=30, max_tool_calls=2))
    )

    assert seen == [{"q": "x"}]
    assert result.status == "ok"


def test_a_tool_that_raises_is_surfaced_rather_than_crashing(
    tmp_path: Path, annex: AnnexRecords, skills_root: Path
) -> None:
    """AI spec §7.1 case 6. A caller's handler must not be able to take the
    process down -- the model may still recover from a tool error."""

    def explode(_: dict[str, Any]) -> str:
        raise RuntimeError("boom")

    tool = ToolSpec(
        name="lookup",
        description="Look something up.",
        input_schema={"type": "object"},
        handler=explode,
    )
    endpoint = _fixture(
        tmp_path,
        _turn("", tool_calls=[{"id": "c1", "name": "lookup", "arguments": {}}]),
        _turn("recovered"),
    )
    runner = _runner(annex, skills_root, endpoint)

    result = runner.run(
        _request(tools=[tool], budget=Budget(max_usd=1.0, max_wall_seconds=30, max_tool_calls=2))
    )

    assert result.status == "ok"
    assert result.output == "recovered"


def test_tools_are_refused_when_the_budget_allows_none(
    tmp_path: Path, annex: AnnexRecords, skills_root: Path
) -> None:
    """`max_tool_calls` defaults to 0 (contracts §10.1). A request that declares
    tools and omits the budget gets none rather than unlimited."""
    tool = ToolSpec(
        name="lookup",
        description="Look something up.",
        input_schema={"type": "object"},
        handler=lambda args: "done",
    )
    endpoint = _fixture(
        tmp_path, _turn("", tool_calls=[{"id": "c1", "name": "lookup", "arguments": {}}])
    )

    result = _runner(annex, skills_root, endpoint).run(_request(tools=[tool]))

    assert result.status == "budget_exhausted"


# ------------------------------------------------------------------ adapters


def test_a_hosted_adapter_is_denied_offline_before_anything_is_formed() -> None:
    """PRD F13.7 and S7's validation item 4.

    The refusal happens before `import_module`, so no provider code is loaded
    and no client constructed -- a stronger claim than "before any socket".
    """
    with pytest.raises(AdoptError) as raised:
        build_adapter("anthropic", offline=True, model="x")

    assert raised.value.code is ErrorCode.AGENT_OFFLINE_ADAPTER_DENIED


def test_an_unregistered_adapter_is_a_usage_error() -> None:
    with pytest.raises(AdoptError) as raised:
        build_adapter("gemini", offline=False, model="x")

    assert raised.value.code is ErrorCode.AGENT_ADAPTER_UNKNOWN


def test_the_seam_never_substitutes_when_no_adapter_is_configured(
    tmp_path: Path, annex: AnnexRecords, skills_root: Path
) -> None:
    """AI spec §2: the seam raises rather than picking one. A silent
    substitution changes cost, behaviour and data residency unannounced."""
    runner = Runner(annex=annex, scope_ref=_SCOPE, skills_root=skills_root)

    with pytest.raises(AdoptError) as raised:
        runner.run(_request())

    assert raised.value.code is ErrorCode.AGENT_ADAPTER_UNKNOWN


def test_the_recorded_fake_is_typed_test_and_not_local() -> None:
    """Load-bearing typing: `conformance-matrix` needs one **local** adapter
    green, and a fake counting as local would let a pipeline exercising no real
    model satisfy the gate that exists to prove one was exercised."""
    kinds = {info.id: info.kind for info in describe_adapters(offline=True)}

    assert kinds["fake_recorded"] == "test"
    assert kinds["local_openai"] == "local"
    assert kinds["anthropic"] == "hosted"


def test_adapter_availability_says_which_fix_is_needed() -> None:
    offline = {info.id: info for info in describe_adapters(offline=True)}
    online = {info.id: info for info in describe_adapters(offline=False, model="m")}

    assert offline["anthropic"].available is False
    assert "offline" in (offline["anthropic"].reason or "")
    assert online["anthropic"].available is True
    assert online["local_openai"].available is False
    assert "ENDPOINT" in (online["local_openai"].reason or "")


def test_a_malformed_skill_is_refused_before_any_adapter_is_built(
    tmp_path: Path, annex: AnnexRecords
) -> None:
    """The ordering AI spec §6 requires: a broken skill costs nothing.

    `anthropic` is named as the adapter and the process is offline, so a runner
    that built the adapter first would raise `AGENT_OFFLINE_ADAPTER_DENIED`.
    Seeing `MANIFEST_INVALID` is what proves the skill was loaded first.
    """
    root = tmp_path / "skills"
    (root / "bad" / "v1").mkdir(parents=True)
    (root / "bad" / "v1" / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")
    runner = Runner(annex=annex, scope_ref=_SCOPE, skills_root=root, adapter_id="anthropic")

    with pytest.raises(AdoptError) as raised:
        runner.run(_request(skill_ref="bad/v1"))

    assert raised.value.code is ErrorCode.MANIFEST_INVALID


@pytest.mark.unit
class TestProviderNegotiation:
    """A 400 that names a parameter is answered by changing it, once.

    *Fails when* the negotiation rules stop matching what a provider rejects.
    *Matters because* `04` §2 forbids hard-coding a model identifier, so the
    parameter set cannot be chosen from the model name -- the provider naming
    the field is the only authority available, and the first real run against
    GPT-5.6 was rejected three separate times for `temperature`, `max_tokens`
    and `reasoning_effort`. *No other instrument catches it because* the matrix
    only executes where credentials exist, and there a wiring defect and a
    vendor outage look identical.
    """

    def test_the_output_cap_is_renamed_never_dropped(self) -> None:
        """Dropping a cap turns a bounded request into an unbounded one."""
        from adopt_agent.adapters.openai_compatible import negotiate

        payload = {"max_completion_tokens": 64}

        assert negotiate(payload, "unsupported_parameter", "max_completion_tokens") is True
        assert payload == {"max_tokens": 64}, "the cap must survive under the other spelling"

        assert negotiate(payload, "unsupported_parameter", "max_tokens") is True
        assert payload == {"max_completion_tokens": 64}, "and back, for a newer endpoint"

    @pytest.mark.parametrize("param", ["temperature", "reasoning_effort"])
    def test_a_droppable_parameter_is_dropped(self, param: str) -> None:
        from adopt_agent.adapters.openai_compatible import negotiate

        payload = {"model": "m", param: "x"}

        assert negotiate(payload, "unsupported_value", param) is True
        assert param not in payload
        assert payload == {"model": "m"}, "nothing else may be disturbed"

    def test_an_unknown_parameter_is_not_negotiated(self) -> None:
        """Otherwise a genuine 400 -- a bad tool schema -- reads as a parameter
        problem and the request is retried into the same refusal."""
        from adopt_agent.adapters.openai_compatible import negotiate

        payload = {"model": "m", "tools": []}

        assert negotiate(payload, "invalid_request_error", "tools") is False
        assert payload == {"model": "m", "tools": []}


@pytest.mark.unit
class TestUnfence:
    """A whole-output markdown fence is stripped; nothing else is.

    *Fails when* the seam starts hunting for JSON inside prose, or stops
    tolerating the wrapper a frontier model actually emitted. *Matters because*
    conformance case 2 burned both attempts on ```json-wrapped output that was
    otherwise exactly correct. *No other instrument catches it because* the
    recorded fake replays unfenced fixtures and never produces the wrapper.
    """

    @pytest.mark.parametrize(
        ("raw", "expected", "why"),
        [
            ('```json\n{"a": 1}\n```', '{"a": 1}', "the case that was observed failing"),
            ('```\n{"a": 1}\n```', '{"a": 1}', "a fence with no language tag"),
            ('{"a": 1}', '{"a": 1}', "unfenced output is untouched"),
            ('  ```json\n{"a": 1}\n```  ', '{"a": 1}', "surrounding whitespace"),
        ],
    )
    def test_it_strips_exactly_a_whole_output_fence(
        self, raw: str, expected: str, why: str
    ) -> None:
        from adopt_agent.schema_check import unfence

        assert unfence(raw) == expected, why

    def test_prose_around_a_fence_is_left_alone(self) -> None:
        """*Not* scanned for JSON: a guess at which object was meant is not a
        wrong answer but a different referent -- CR-32's argument, on output."""
        from adopt_agent.schema_check import unfence

        raw = 'Here you go:\n```json\n{"a": 1}\n```\nHope that helps.'

        assert unfence(raw) == raw
