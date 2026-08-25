"""The probe runner: the only module in the programme that opens a probe socket.

**That sentence is a CI-enforced import contract** (`probe-io`), proven by a
planted violation, and it is the whole safety argument of Build 5. v6.1 D2 says
probes are data; the F8 audit upheld dropping the sandbox *because* a probe
contains no executable content and only this module reaches the network. If the
socket moved anywhere else, the audit's conclusion would no longer hold.

**The allow-list is enforced here, at connection time, not at `add` time.**
`adopt probe add` refuses an undeclared host too, and that check is a courtesy
that saves an operator a round trip. This one is the invariant: a revision could
reach the runner without passing through `add` -- an imported bundle, a store
written by an older binary, a caller composing the library directly -- and the
enforcement has to be where the connection is made. The unit test for this
deliberately bypasses `add` and constructs the revision directly, because a test
that went through `add` would prove only that `add` works.

**Secrets are resolved at send and redacted at record**, and the two are
different moments on purpose. `{{secret.NAME}}` is interpolated into the request
that goes out; every observation, log line and error message carries
`[redacted:NAME]` in its place. A secret that reached an observation would be a
secret in the client's export bundle forever, and `probe_observation.output` is
an exportable column.

**Three budgets, all enforced, none of them advisory:** the wall clock over the
whole probe (`runtime.max_seconds`, itself capped by `PROBE_TIMEOUT_SECONDS`),
the request count (`runtime.max_requests`), and the model spend, which is the
existing seam's meter rather than a second implementation of one.

**No redirects are followed.** A 3xx is recorded as the status it is. Following
one would mean a probe whose declared host is `api.example` reaching wherever
that host's operator points it -- an allow-list that means nothing after the
first hop.
"""

import datetime as _dt
import hashlib
import json
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from adopt_const import AGENT_ADAPTER_TIMEOUT_S
from adopt_model import ProbeObservation, ProbeRun
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, get_logger, new_id
from adopt_probe.baseline import Baseline
from adopt_probe.diff import ProbeComparison, compare_run
from adopt_probe.manifest import Expectation, HttpStep, ProbeSpec, PromptStep, Step
from adopt_probe.ports import ProbeRunRecords, SensorSink

__all__ = ["ProbeOutcome", "RunReport", "StepResult", "execute_probe", "redact"]

_log = get_logger(__name__)

#: The prompt carrier for a `prompt` step. The probe's own text is the input;
#: this skill is a pass-through whose job is to exist, because
#: `AgentRequest.skill_ref` must name loadable prompt bytes for `skill_sha256` to
#: mean anything (AI spec §5.2 rule 3).
PROMPT_SKILL_REF: Final[str] = "probe-001/v1"

#: Heartbeat outcome per run outcome. **Drift is content, not sensor health**:
#: a probe that observed a change did not go silent, and freshness reads silence.
#: Mapping it to anything but `success` would degrade the scope's freshness for
#: the sensor working exactly as intended.
_HEARTBEAT_FOR_OUTCOME: Final[dict[str, str]] = {
    "success": "success",
    "diff": "success",
    "failure": "failure",
    "blocked_by_manifest": "skipped",
}

#: How much of a step's output is recorded. `probe_observation.output` is an
#: exportable column, so an unbounded response body would travel in the client's
#: bundle and sit in their store forever. A truncation cap on recorded text is a
#: different quantity from `DETECT_MAX_SNIFF_BYTES` (how many bytes `adopt detect`
#: reads from a file to classify it) and merely shares its value. Not promoted to
#: `adopt_const`: v6.1 §8's tunable list permits exactly one new constant for
#: this build, and the sprint plan's D-9 spends it on `PROBE_TIMEOUT_SECONDS`.
# const-sync: ok -- see above; shares a value with DETECT_MAX_SNIFF_BYTES, means something else
_MAX_RECORDED_CHARS: Final[int] = 8192


class _StepFailure(Exception):
    """A step did not complete, and no *registered* code describes why.

    **Deliberately not an `AdoptError`.** v6.1 §6 B5 authorizes exactly three new
    error codes for this build, and a missing environment variable, a transport
    error or an unconfigured adapter are none of them -- they are `probe_run`
    **outcomes**, which is what the sprint plan's outcome table says: `failure`
    covers "transport error, invariant violation, missing secret, budget cut
    mid-flight". Inventing `PROBE_SECRET_UNRESOLVED` here would have added a
    registry row the architecture never authorized, to say something the outcome
    column already says.

    It never escapes the package: `execute_probe` turns it into an outcome and a
    redacted message.
    """


@dataclass(frozen=True, slots=True)
class StepResult:
    """What one step observed, already redacted."""

    index: int
    kind: str
    ok: bool
    output: str
    fingerprint: str
    latency_ms: int
    violation: str | None = None


@dataclass(slots=True)
class RunReport:
    """One probe's execution, as the CLI reports it and the store recorded it."""

    probe_name: str
    probe_definition_id: str
    probe_definition_revision_id: str
    run_id: str | None
    outcome: str
    steps: list[StepResult] = field(default_factory=list)
    refusal: str | None = None
    #: The comparison against the baseline this run was stamped with, when one
    #: existed for **this** revision. `None` means there was nothing to compare
    #: against, or that the baseline belongs to another revision -- two facts
    #: `adopt probe diff` reports separately and the runner does not conflate.
    comparison: ProbeComparison | None = None
    #: When the run finished, by the injected clock. Carried on the report so a
    #: caller writing a row *about* the run -- a conflict's `detected_at` -- dates
    #: it from the run rather than from a second clock reading taken later.
    finished_at: _dt.datetime | None = None

    @property
    def failed(self) -> bool:
        return self.outcome in {"failure", "blocked_by_manifest"}

    @property
    def drifted(self) -> bool:
        return self.outcome == ProbeOutcome.DIFF


class ProbeOutcome:
    """The `probe_outcome` vocabulary, spelled once."""

    SUCCESS: Final[str] = "success"
    DIFF: Final[str] = "diff"
    FAILURE: Final[str] = "failure"
    BLOCKED: Final[str] = "blocked_by_manifest"


def redact(text: str, secrets: Mapping[str, str]) -> str:
    """Replace every resolved secret value with `[redacted:NAME]`.

    Applied to everything recorded, logged or raised. Longest value first, so a
    secret that contains another secret cannot leave a fragment behind.
    """
    rendered = text
    for name, value in sorted(secrets.items(), key=lambda kv: len(kv[1]), reverse=True):
        if value:
            rendered = rendered.replace(value, f"[redacted:{name}]")
    return rendered


def _resolve_secrets(spec: ProbeSpec, environ: Mapping[str, str]) -> dict[str, str]:
    """Read the declared refs from the environment. A missing one fails the run.

    Raises:
        _StepFailure: naming the *reference*, never a value -- the whole point of
            the indirection.
    """
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for name in sorted(spec.secret_refs):
        value = environ.get(name)
        if value is None or value == "":
            missing.append(name)
        else:
            resolved[name] = value
    if missing:
        raise _StepFailure(
            f"declared secret ref(s) {missing} are not set in the environment; "
            "export the variable, or remove the ref from `secret_refs`"
        )
    return resolved


def _interpolate(value: Any, secrets: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        rendered = value
        for name, secret in secrets.items():
            rendered = rendered.replace(f"{{{{secret.{name}}}}}", secret)
        return rendered
    if isinstance(value, dict):
        return {k: _interpolate(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, secrets) for v in value]
    return value


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_output(payload: Mapping[str, Any]) -> str:
    """One rendering for everything recorded, so fingerprints are comparable.

    **Latency is deliberately not in here**, and that is the whole reason this
    function is separate from the report. `probe_observation.output` is what a
    later run is compared against; a measurement that differs by a millisecond
    every time would make every rerun read as drift, and a reviewer who sees
    drift on every run stops reading the queue -- the exact false-staleness
    failure v6.1 H5 redefined the map digest to avoid, arriving here by a
    different road. What a probe *observed* is the status and the body; how long
    it took is a property of the network that day, checked against
    `expect.latency_under_ms` at run time and reported to the operator, never
    recorded as part of the system's behaviour.
    """
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_field_present(body: Any, path: str) -> bool:
    current = body
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return False
        current = current[segment]
    return True


def _check_expectations(
    expect: Expectation, *, status: int | None, body_text: str, latency_ms: int
) -> str | None:
    """The declared invariants, in the order a human would read them.

    Structural failures are named as themselves. A status mismatch reported as a
    similarity number is a diagnosis nobody can act on.
    """
    if expect.status is not None and status != expect.status:
        return f"status {status} != expected {expect.status}"
    if expect.latency_under_ms is not None and latency_ms > expect.latency_under_ms:
        return f"latency {latency_ms}ms exceeded {expect.latency_under_ms}ms"
    if expect.json_fields:
        try:
            parsed = json.loads(body_text)
        except (ValueError, TypeError):
            return f"expected JSON fields {list(expect.json_fields)} but the body is not JSON"
        absent = [f for f in expect.json_fields if not _json_field_present(parsed, f)]
        if absent:
            return f"missing JSON field(s) {absent}"
    return None


class _Budget:
    """The three limits a probe declares, enforced together.

    A class rather than three locals because each is checked before every step
    and a check someone forgets to copy into the second call site is the failure
    mode this exists to prevent.

    **`max_model_calls` is counted here rather than handed to the seam**, because
    `adopt_agent.Budget` has no such field -- it bounds money, wall time, tokens
    and *tool* calls. The manifest declares a model-call ceiling and Build 0's
    validator requires it, so something has to enforce it; the runner does, the
    same way it enforces `max_requests`. The seam's own meter still bounds spend
    and tokens underneath, which is why this is a second limit and not a second
    implementation of one.
    """

    def __init__(self, *, max_seconds: int, max_requests: int, max_model_calls: int) -> None:
        self._deadline = time.monotonic() + max_seconds
        self._max_seconds = max_seconds
        self._remaining_requests = max_requests
        self._remaining_model_calls = max_model_calls

    def check_clock(self) -> None:
        if time.monotonic() > self._deadline:
            raise AdoptError(
                ErrorCode.PROBE_BUDGET_EXCEEDED,
                message=f"the probe exceeded its {self._max_seconds}s wall-clock budget",
                hint="Raise `runtime.max_seconds` (up to the programme ceiling) or make "
                "the probe smaller. An unbounded probe in a client environment is "
                "the failure the manifest exists to prevent.",
            )

    def spend_request(self) -> None:
        if self._remaining_requests <= 0:
            raise AdoptError(
                ErrorCode.PROBE_BUDGET_EXCEEDED,
                message="the probe exceeded its declared `runtime.max_requests`",
                hint="Raise the limit in the probe's manifest, or remove steps.",
            )
        self._remaining_requests -= 1

    def spend_model_call(self) -> None:
        if self._remaining_model_calls <= 0:
            raise AdoptError(
                ErrorCode.PROBE_BUDGET_EXCEEDED,
                message="the probe exceeded its declared `cost.max_model_calls`",
                hint="Raise the limit in the probe's manifest, or remove prompt steps. "
                "A probe that spent past its own declared ceiling would be spending "
                "a client's money on the authority of nobody.",
            )
        self._remaining_model_calls -= 1

    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - time.monotonic())


def _send(
    step: HttpStep,
    *,
    secrets: Mapping[str, str],
    declared_hosts: frozenset[str],
    timeout: float,
) -> tuple[int | None, str, int]:
    """Perform one HTTP request. **The only place a probe socket is opened.**

    The allow-list is checked here rather than by the caller, because "the check
    is next to the connection" is the property that survives refactoring.
    """
    if step.host not in declared_hosts:
        raise AdoptError(
            ErrorCode.PROBE_HOST_UNDECLARED,
            message=f"step host {step.host!r} is not in `network.allow`",
            hint=f"Declared hosts are {sorted(declared_hosts)}. The allow-list is the "
            "statement this runner enforces, and a target outside it is an "
            "undeclared egress.",
        )

    body = _interpolate(step.body, secrets)
    payload: bytes | None = None
    headers = {k: str(v) for k, v in _interpolate(step.headers, secrets).items()}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(  # noqa: S310 - scheme checked in `manifest._host_of`
        step.url, data=payload, headers=headers, method=step.method
    )
    context = ssl.create_default_context()
    started = time.monotonic()
    try:
        # `urlopen` follows redirects by default; a probe must not. The opener
        # below is built without `HTTPRedirectHandler`, so a 3xx surfaces as the
        # response it is rather than as a hop to an undeclared host.
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context),
            _NoRedirect(),
        )
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = int(exc.code)
    except urllib.error.URLError as exc:
        # `exc.reason` can carry the URL, and the URL can carry an interpolated
        # secret in a query string. Redaction happens at the call site, which is
        # why this raises rather than recording.
        raise _StepFailure(f"the request to {step.host} failed: {exc.reason}") from exc
    latency_ms = int((time.monotonic() - started) * 1000)
    text = raw.decode("utf-8", errors="replace")
    return status, text, latency_ms


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect handler that redirects nothing. See `_send`."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _run_http_step(
    step: HttpStep,
    index: int,
    *,
    spec: ProbeSpec,
    secrets: Mapping[str, str],
    budget: _Budget,
) -> StepResult:
    budget.check_clock()
    budget.spend_request()
    timeout = min(float(AGENT_ADAPTER_TIMEOUT_S), budget.remaining_seconds()) or 1.0
    status, body, latency_ms = _send(
        step, secrets=secrets, declared_hosts=spec.declared_hosts, timeout=timeout
    )
    budget.check_clock()

    violation = _check_expectations(
        step.expect, status=status, body_text=body, latency_ms=latency_ms
    )
    recorded = redact(body, secrets)[:_MAX_RECORDED_CHARS]
    output = _canonical_output({"kind": "http", "status": status, "body": recorded})
    return StepResult(
        index=index,
        kind="http",
        ok=violation is None,
        output=output,
        fingerprint=_fingerprint(output),
        latency_ms=latency_ms,
        violation=violation,
    )


def _run_prompt_step(
    step: PromptStep,
    index: int,
    *,
    spec: ProbeSpec,
    secrets: Mapping[str, str],
    budget: _Budget,
    agent: Any,
    idempotency_key: str,
) -> StepResult:
    """Execute a `prompt` step through the seam. No second model door exists.

    The budget handed to the seam is the probe's declared `cost` block, so a
    probe's spend is bounded by the document a human approved rather than by a
    programme default.
    """
    budget.check_clock()
    budget.spend_model_call()
    from adopt_agent import AgentRequest, Budget

    started = time.monotonic()
    result = agent.run(
        AgentRequest(
            skill_ref=PROMPT_SKILL_REF,
            inputs={"probe_input": step.input},
            # `max_tokens` and the wall clock come from the probe; `max_usd` keeps
            # the seam's default, because the manifest declares no money ceiling
            # and inventing one here would be a limit nobody approved.
            budget=Budget(
                max_tokens=spec.max_tokens,
                max_wall_seconds=int(budget.remaining_seconds()) or 1,
            ),
            idempotency_key=idempotency_key,
        )
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    budget.check_clock()

    if result.status == "budget_exhausted":
        raise AdoptError(
            ErrorCode.PROBE_BUDGET_EXCEEDED,
            message="the probe's declared model budget was exhausted",
            hint="Raise `cost.max_model_calls` or `cost.max_tokens` in the probe's "
            "manifest. The seam's meter is what refused it, not a second budget.",
        )
    if result.status != "ok":
        raise _StepFailure(f"the prompt step ended {result.status}")

    text = _prompt_text(result)
    violation = _check_expectations(step.expect, status=None, body_text=text, latency_ms=latency_ms)
    recorded = redact(text, secrets)[:_MAX_RECORDED_CHARS]
    output = _canonical_output({"kind": "prompt", "text": recorded})
    return StepResult(
        index=index,
        kind="prompt",
        ok=violation is None,
        output=output,
        fingerprint=_fingerprint(output),
        latency_ms=latency_ms,
        violation=violation,
    )


def _prompt_text(result: Any) -> str:
    """The model's reply as text, whatever shape the seam handed back."""
    output = getattr(result, "output", None)
    if isinstance(output, str):
        return output
    if isinstance(output, Mapping):
        return _canonical_output(output)
    return "" if output is None else str(output)


def execute_probe(
    spec: ProbeSpec,
    *,
    probe_definition_id: str,
    probe_definition_revision_id: str,
    records: ProbeRunRecords | None,
    environ: Mapping[str, str],
    agent: Any = None,
    sensor: SensorSink | None = None,
    clock: Clock | None = None,
    baseline: Baseline | None = None,
) -> RunReport:
    """Run one probe, record what it observed, and report the outcome.

    Args:
        spec: The validated probe.
        probe_definition_id: The definition this revision belongs to.
        probe_definition_revision_id: The revision executed. It travels with
            every observation so Build 6 can tell "the probe changed" from "the
            system changed" without guessing.
        records: Where rows land. `None` runs without persisting -- `adopt probe
            run FILE`'s authoring loop, which is also the rogue negative control.
        environ: Where declared secret refs are resolved from.
        agent: An `adopt_agent.Runner`, required only if a `prompt` step exists.
        sensor: Where the per-run health fact goes.
        clock: Injected clock; tests pass `ManualClock`.
        baseline: What this run is compared against, when one has been set.
            Comparing **here** rather than only in `adopt probe diff` is what
            lets `probe_run.outcome` be `diff` and `probe_observation.similarity`
            be a number: both are insert-only columns decided once, when the run
            finishes, because a run that could be re-judged later is a record of
            an opinion rather than of an event. `diff` re-derives the same
            comparison to report it and writes nothing.

    Returns:
        A `RunReport`. A refusal is reported, not raised: the caller needs the
        recorded run and its outcome, and `run --all` must not stop at the first
        probe that fails.
    """
    the_clock: Clock = clock if clock is not None else SystemClock()
    started_at = the_clock.now()
    # Minted **before** the steps run, not at record time, because it is what
    # keys each prompt step's idempotency (see `_run_step`). A probe run is a
    # measurement, and two measurements of the same system are two events.
    run_id = new_id("prun")
    report = RunReport(
        probe_name=spec.name,
        probe_definition_id=probe_definition_id,
        probe_definition_revision_id=probe_definition_revision_id,
        run_id=None,
        outcome=ProbeOutcome.SUCCESS,
    )

    secrets: dict[str, str] = {}
    budget = _Budget(
        max_seconds=spec.max_seconds,
        max_requests=spec.max_requests,
        max_model_calls=spec.max_model_calls,
    )
    try:
        secrets = _resolve_secrets(spec, environ)
        for index, step in enumerate(spec.steps):
            report.steps.append(
                _run_step(
                    step,
                    index,
                    spec=spec,
                    secrets=secrets,
                    budget=budget,
                    agent=agent,
                    execution_id=run_id,
                )
            )
        if any(not step.ok for step in report.steps):
            report.outcome = ProbeOutcome.FAILURE
            report.refusal = next(s.violation for s in report.steps if s.violation)
        else:
            # Only a run that completed and held its declared invariants can be
            # compared. A failed run's outputs are a fault's outputs, and calling
            # the difference between a fault and the baseline "drift" would tell
            # an FDE the system's behaviour changed when what happened is that
            # the probe broke.
            report.comparison = _compare(report, spec=spec, baseline=baseline)
            if report.comparison is not None and report.comparison.drifted:
                report.outcome = ProbeOutcome.DIFF
    except AdoptError as error:
        report.outcome = _outcome_for(error, ran_any=bool(report.steps))
        report.refusal = redact(str(error.message), secrets)
        _log.warn(
            "probe_run_refused",
            probe=spec.name,
            code=str(error.code),
            outcome=report.outcome,
        )
    except _StepFailure as failure:
        report.outcome = ProbeOutcome.FAILURE
        report.refusal = redact(str(failure), secrets)
        _log.warn("probe_run_failed", probe=spec.name, outcome=report.outcome)

    finished_at = the_clock.now()
    report.finished_at = finished_at
    if records is not None:
        report.run_id = _record(
            report,
            run_id=run_id,
            records=records,
            started_at=started_at,
            finished_at=finished_at,
            baseline_version_id=None if baseline is None else baseline.id,
        )
    if sensor is not None:
        sensor.heartbeat(
            outcome=_HEARTBEAT_FOR_OUTCOME[report.outcome],
            detail=None if report.outcome == ProbeOutcome.SUCCESS else report.outcome,
        )
    return report


def _run_step(
    step: Step,
    index: int,
    *,
    spec: ProbeSpec,
    secrets: Mapping[str, str],
    budget: _Budget,
    agent: Any,
    execution_id: str,
) -> StepResult:
    """One step. The `execution_id` is this run's, and it keys the model call.

    **A probe must re-ask the model on every run, and that is a repaired defect.**
    S5.1 keyed a prompt step on `probe:{revision}:{index}`, which is constant
    across runs -- so the seam's idempotency found the first run's record and
    replayed it. `Runner._replayed` returns `output=None` by design (the annex
    stores an `output_ref`, never the text -- contracts §12), so every rerun
    recorded an **empty** observation and read as drift against its own baseline.
    A probe against an AI deployment could therefore never observe anything after
    its first run, which is the single system class this build exists for.

    Idempotency is protection against paying twice for one request. Two runs of a
    probe are not one request: asking the system again *is* the product.
    """
    if isinstance(step, HttpStep):
        return _run_http_step(step, index, spec=spec, secrets=secrets, budget=budget)
    if agent is None:
        raise _StepFailure(
            "the probe has a prompt step and no model adapter is configured; set "
            "ADOPT_ADAPTER (and ADOPT_MODEL) and pass --allow-network, or remove the "
            "prompt step -- a probe with only `http` steps needs no model"
        )
    return _run_prompt_step(
        step,
        index,
        spec=spec,
        secrets=secrets,
        budget=budget,
        agent=agent,
        idempotency_key=f"probe:{execution_id}:{index}",
    )


def _outcome_for(error: AdoptError, *, ran_any: bool) -> str:
    """`blocked_by_manifest` when the manifest refused it; `failure` otherwise.

    The distinction is what a reader needs: a probe the allow-list stopped never
    reached the system, while a probe cut off mid-flight may have. So an
    undeclared host is always `blocked_by_manifest` -- the allow-list refused the
    connection -- while an exhausted budget is `blocked_by_manifest` only if it
    was already spent before the first step ran, and `failure` after that.
    """
    if error.code is ErrorCode.PROBE_HOST_UNDECLARED:
        return ProbeOutcome.BLOCKED
    if error.code is ErrorCode.PROBE_BUDGET_EXCEEDED and not ran_any:
        return ProbeOutcome.BLOCKED
    return ProbeOutcome.FAILURE


def _compare(
    report: RunReport, *, spec: ProbeSpec, baseline: Baseline | None
) -> ProbeComparison | None:
    """This run against its baseline, or `None` when there is nothing to compare.

    A baseline belonging to a **different** revision returns `None` rather than a
    `probe_changed` comparison, and the distinction is deliberate: this function
    decides an *outcome*, and `probe_changed` is not one -- `probe_outcome` has
    four values and none of them means "the question changed". The run is
    `success`, its observations record what was seen, and `adopt probe diff` is
    where a human is told the baseline no longer applies. Writing `diff` here
    would claim the system changed on the evidence of an edit to our own file.
    """
    if baseline is None or baseline.revision_id != report.probe_definition_revision_id:
        return None
    return compare_run(
        probe=spec.name,
        baseline=baseline,
        run_revision_id=report.probe_definition_revision_id,
        run_id=None,
        outputs=[step.output for step in report.steps],
        steps=spec.steps,
    )


def _record(
    report: RunReport,
    *,
    run_id: str,
    records: ProbeRunRecords,
    started_at: _dt.datetime,
    finished_at: _dt.datetime,
    baseline_version_id: str | None,
) -> str:
    """One transaction: the run and every observation it produced."""
    with records.transaction():
        records.insert_probe_run(
            ProbeRun(
                id=run_id,
                probe_definition_revision_id=report.probe_definition_revision_id,
                baseline_version_id=baseline_version_id,
                started_at=started_at,
                finished_at=finished_at,
                outcome=report.outcome,  # type: ignore[arg-type]
                # v1 does not verify cleanup (v6.1 F8's stated residual). Recorded
                # false rather than omitted: the column is the place the trigger
                # will be honoured, and a NULL-ish default would read as "checked".
                cleanup_verified=False,
            )
        )
        # Similarity needs a baseline of **this** revision to compare against.
        # Absent one it stays NULL rather than 1.0: a fabricated perfect score
        # would be a claim that the run matched something, and nothing was there.
        scores = (
            {step.index: step.similarity for step in report.comparison.steps}
            if report.comparison is not None
            else {}
        )
        for step in report.steps:
            records.insert_probe_observation(
                ProbeObservation(
                    id=new_id("pobs"),
                    probe_run_id=run_id,
                    output=step.output,
                    fingerprint=step.fingerprint,
                    similarity=scores.get(step.index),
                    judge_verdict=None,
                )
            )
    return run_id


def summarize(reports: Sequence[RunReport]) -> dict[str, Any]:
    """The `--json` slice for a set of runs.

    `failed` and `drifted` are counted separately and neither is derived from
    the other. A drifted run **worked** -- it reached the system, held its
    invariants and observed a change -- so folding it into a failure count would
    make `adopt probe run` exit non-zero for the product doing its job.
    """
    return {
        "probes": len(reports),
        "runs": [
            {
                "probe": report.probe_name,
                "run": report.run_id,
                "outcome": report.outcome,
                "steps": len(report.steps),
                "refusal": report.refusal,
                "drifted_steps": (
                    []
                    if report.comparison is None
                    else [step.index for step in report.comparison.steps if step.drifted]
                ),
            }
            for report in reports
        ],
        "failed": sum(1 for report in reports if report.failed),
        "drifted": sum(1 for report in reports if report.drifted),
    }
