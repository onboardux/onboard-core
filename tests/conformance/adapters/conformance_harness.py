"""One way to drive one conformance case against any adapter. AI spec §7.1.

**The point of this module is that the thirteen cases are written once.** A suite
with a per-adapter branch in each case is thirteen cases times four adapters of
divergent assertions, and the divergence is exactly where a contract stops being
one contract. So a case declares a request and its recorded turns, and this module
decides what "run it" means for the adapter under test:

* `fake_recorded` replays the case's recorded turns from a fixture file.
* every other adapter is handed the same request and answers it for real.

**A recorded turn is a fixture for the fake and documentation for the rest.** It
records what a conforming provider reply looks like for that case, which is what
lets someone reading the case see what the assertion expects without running it.

**An adapter that cannot be reached fails here, with the reason.** Not a skip:
`conformance-matrix` counts green adapters and a skip would make an unexercised
adapter indistinguishable from a passing one.

**Bare module import, deliberately.** `tests/conformance/adapters/` has no
`__init__.py`, so pytest puts this directory on `sys.path` and `import
conformance_harness` resolves without a package path -- the pattern CR-40 forced
on the durability suite, kept here for the same reason: a suite reachable only
through one repository's package layout is a suite that cannot be run from
anywhere else.
"""

import datetime as _dt
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pytest

from adopt_agent import AgentRequest, AgentResult, Budget, Runner, ToolSpec
from adopt_agent.adapters.base import REGISTRY
from adopt_obs import log as _log
from adopt_store.annex import open_annex

__all__ = ["CaseRun", "TickingClock", "drive", "recorded_turn", "requires"]

_SCOPE: Final[str] = "northwind/acme-erp"
_START: Final[_dt.datetime] = _dt.datetime(2026, 8, 6, 12, 0, 0, tzinfo=_dt.UTC)

#: Environment keys a real adapter needs. Read from the environment rather than
#: from `adopt_cli.config` because the suite must not depend on the CLI package
#: to test the seam -- and because `03` §3 says a credential is read from the
#: environment and never from a file.
_ENDPOINT_ENV: Final[str] = "ADOPT_ADAPTER_ENDPOINT"
_MODEL_ENV: Final[str] = "ADOPT_MODEL"


class TickingClock:
    """A clock that advances a fixed delta on every reading.

    The wall-clock cap is the one budget dimension nothing else moves: a recorded
    fake answers instantly and a real provider answers in a time the suite cannot
    predict. `ManualClock` cannot be advanced from inside a run, and
    implementation spec §5 bans sleeping, so the remaining honest instrument is a
    clock whose every reading is later than the last. The seam reads it once per
    trace step, so elapsed time becomes a deterministic function of how far the
    run got -- which is exactly what case 8 needs to assert.
    """

    def __init__(self, tick: _dt.timedelta, *, start: _dt.datetime = _START) -> None:
        self._now = start
        self._tick = tick

    def now(self) -> _dt.datetime:
        current = self._now
        self._now += self._tick
        return current


def recorded_turn(
    text: str = "",
    *,
    tool_calls: Sequence[dict[str, Any]] = (),
    input_tokens: int = 10,
    output_tokens: int = 5,
    reported_usd: float | None = None,
) -> dict[str, Any]:
    """One provider turn as the recorded fake replays it."""
    return {
        "text": text,
        "tool_calls": list(tool_calls),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reported_usd": reported_usd,
    }


@dataclass(frozen=True)
class CaseRun:
    """What one driven case produced."""

    result: AgentResult
    #: Everything the structured logger emitted during the run. Case 13 asserts
    #: over this as well as over the trace, because "no payload in any log line"
    #: is half of PRD N11 and the trace is the other half.
    logs: str
    #: How many times the adapter was asked for a turn, or `None` when the
    #: adapter cannot report it. The recorded fake counts; a real provider client
    #: is not instrumented, so a case needing an exact count says so.
    provider_calls: int | None
    replayed: AgentResult | None = None
    tool_arguments: list[dict[str, Any]] = field(default_factory=list)

    @property
    def why(self) -> str:
        """Everything needed to tell a seam defect from a provider refusal.

        **`assert run.result.status == "ok"` alone is an assertion that cannot
        diagnose.** The first real-model run of this suite produced ten
        `assert 'error' == 'ok'` lines and not one of them said *why* -- and the
        two adapters had failed for entirely different reasons, one a credential
        or model id and the other a seam defect on the follow-up turn. Telling
        those apart from the log is the whole job at that moment, and it cost a
        CI round trip that this property removes.

        The error's `code` and `message` are safe to print: `_wire.py` builds the
        message from a status code and never from a response body, precisely
        because a provider's error payload routinely echoes the request back and
        `04` §8.3 says prompt text is not retrievable from our artifacts. The
        trace step *types* are likewise structural -- `['request', 'abort']` says
        the first call never returned, `[..., 'tool_result', 'abort']` says the
        follow-up turn did.
        """
        error = self.result.error
        detail = "no error attached"
        if error is not None:
            detail = f"{error.code.value}: {error.message}"
        steps = [step.type for step in self.result.trace.steps]
        cost = self.result.cost
        return (
            f"status={self.result.status!r} · {detail} · trace={steps} · "
            f"tokens in/out={cost.input_tokens}/{cost.output_tokens} · "
            f"provider_calls={self.provider_calls}"
        )


def requires(adapter_id: str) -> tuple[bool, str | None, str | None]:
    """`(offline, model, endpoint)` for this adapter, or fail with the reason.

    Hosted adapters are constructed **online**, which is the only way they can be
    constructed at all (`04` §2), so a hosted adapter in `--adapters` is an
    explicit request to make real calls. Local adapters stay offline: `04` §2's
    Offline column says `local_openai` is allowed, and running the suite against a
    loopback endpoint must not require the process to drop its default posture.
    """
    import os

    entry = REGISTRY.get(adapter_id)
    if entry is None:
        pytest.fail(
            f"--adapters named {adapter_id!r}, which is not a registered adapter. "
            f"Registered: {', '.join(sorted(REGISTRY))}."
        )
    if adapter_id == "fake_recorded":
        return True, None, None

    model = os.environ.get(_MODEL_ENV)
    endpoint = os.environ.get(_ENDPOINT_ENV)
    if not model:
        pytest.fail(
            f"the conformance suite was asked for {adapter_id!r} and {_MODEL_ENV} is unset. "
            f"This is a failure rather than a skip on purpose: `conformance-matrix` counts "
            f"green adapters, and a skipped adapter would be indistinguishable from a "
            f"passing one. Set {_MODEL_ENV}, or do not name {adapter_id!r}."
        )
    if entry.needs_endpoint and not endpoint:
        pytest.fail(
            f"the conformance suite was asked for {adapter_id!r} and {_ENDPOINT_ENV} is unset. "
            f"A local adapter needs an OpenAI-compatible endpoint to answer against."
        )
    return entry.kind != "hosted", model, endpoint


def drive(
    adapter_id: str,
    *,
    tmp_path: Path,
    system: str,
    inputs: dict[str, Any],
    recorded: Sequence[dict[str, Any]],
    budget: Budget | None = None,
    output_schema: dict[str, Any] | None = None,
    tools: Sequence[ToolSpec] = (),
    cancel_after: int | None = None,
    clock_tick: _dt.timedelta | None = None,
    replay: bool = False,
    idempotency_key: str = "conformance-1",
) -> CaseRun:
    """Run one case and collect everything the assertions may look at."""
    offline, model, endpoint = requires(adapter_id)

    skill_dir = tmp_path / "skills" / "conformance" / "v1"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: conformance\ndescription: One conformance case.\n---\n\n{system}\n",
        encoding="utf-8",
    )

    if adapter_id == "fake_recorded":
        fixture = tmp_path / "recorded.json"
        fixture.write_text(json.dumps({"turns": list(recorded)}), encoding="utf-8")
        endpoint = str(fixture)

    asks = {"count": 0}

    def _cancelled() -> bool:
        if cancel_after is None:
            return False
        asks["count"] += 1
        return asks["count"] > cancel_after

    request = AgentRequest(
        skill_ref="conformance/v1",
        inputs=inputs,
        tools=list(tools),
        budget=budget if budget is not None else Budget(max_usd=1.0, max_wall_seconds=120),
        output_schema=output_schema,
        idempotency_key=idempotency_key,
    )

    captured = io.StringIO()
    original_sink = _log._sink
    _log.set_sink(captured, min_level=_log.LogLevel.DEBUG)
    try:
        with open_annex(tmp_path / ".adopt" / "runtime.db") as annex:
            runner = Runner(
                annex=annex,
                scope_ref=_SCOPE,
                skills_root=tmp_path / "skills",
                offline=offline,
                adapter_id=adapter_id,
                model=model,
                endpoint=endpoint,
                clock=TickingClock(clock_tick) if clock_tick is not None else None,
                cancelled=_cancelled if cancel_after is not None else None,
            )
            result = runner.run(request)
            replayed = runner.run(request) if replay else None
    finally:
        _log.set_sink(original_sink)

    return CaseRun(
        result=result,
        logs=captured.getvalue(),
        provider_calls=_provider_calls(result, adapter_id),
        replayed=replayed,
    )


def _provider_calls(result: AgentResult, adapter_id: str) -> int | None:
    """How many provider turns this run took, from the trace.

    Counted from `request` trace steps rather than from the adapter object,
    because the seam builds its own adapter and a real client is not
    instrumented. The trace is the record both a test and an auditor read, so a
    count taken from it is a count taken from the artifact that has to be right
    anyway.
    """
    return sum(1 for step in result.trace.steps if step.type == "request")
