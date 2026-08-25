"""The runner: what it reaches, what it spends, and what it writes down.

*Fails when* a probe execution stops enforcing one of the four things that make
probes safe without a sandbox -- the host allow-list, the declared budgets,
secret redaction, or honest outcome recording. *Matters because* v6.1 D2 dropped
v4's sandbox apparatus **on the strength of those four**, so each one is load
bearing rather than defensive. *No other instrument catches it because* the
`probe-io` import contract proves only that the socket lives in this module; it
cannot say whether the module checks anything before opening one.

**The allow-list test deliberately bypasses `adopt probe add`.** `add` refuses an
undeclared host too, so a test that went through it would pass even if the runner
checked nothing -- and a revision can reach the runner without passing through
`add` (an imported bundle, an older binary, a library caller). The spec is
constructed directly, which is the only way to prove the check is enforcement
rather than courtesy.

The HTTP fixture is a loopback `http.server` on an ephemeral port. No sleeps: the
server is started and joined by the fixture, and every timing assertion is about
a budget the test sets, never about wall time it waits for.
"""

import json
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest
from adopt_probe import ProbeOutcome, execute_probe, parse_probe
from adopt_probe.manifest import Expectation, HttpStep, ProbeSpec
from adopt_probe.runner import redact

from adopt_model import BaselineVersion, Conflict, ProbeDefinition, ProbeObservation, ProbeRun
from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit


class _Recorder:
    """An in-memory `ProbeRunRecords`. Records what the runner asked to store."""

    def __init__(self) -> None:
        self.runs: list[ProbeRun] = []
        self.observations: list[ProbeObservation] = []
        self.baselines: list[BaselineVersion] = []
        self.conflicts: list[Conflict] = []

    @contextmanager
    def _noop(self) -> Iterator[None]:
        yield

    def transaction(self) -> AbstractContextManager[None]:
        return self._noop()

    def insert_probe_run(self, row: ProbeRun) -> None:
        self.runs.append(row)

    def insert_probe_observation(self, row: ProbeObservation) -> None:
        self.observations.append(row)

    def insert_baseline_version(self, row: BaselineVersion) -> None:
        self.baselines.append(row)

    def insert_conflict(self, row: Conflict) -> None:
        self.conflicts.append(row)

    def list_probe_definitions(self, **_: Any) -> list[ProbeDefinition]:
        return []

    def latest_baseline(self, **_: Any) -> BaselineVersion | None:
        return None

    def latest_run(self, **_: Any) -> None:
        return None

    def open_conflicts(self, **_: Any) -> list[Conflict]:
        return []


class _Sensor:
    """A `SensorSink` that remembers every heartbeat it was handed."""

    def __init__(self) -> None:
        self.beats: list[tuple[str, str | None]] = []

    def heartbeat(self, *, outcome: str, detail: str | None = None) -> None:
        self.beats.append((outcome, detail))


class _Handler(BaseHTTPRequestHandler):
    status = 200
    payload: ClassVar[dict[str, Any]] = {"order_id": "ord_1", "total": 12}
    seen_headers: ClassVar[list[dict[str, str]]] = []
    #: Echo the `Authorization` header back in the body. Off by default; the
    #: redaction test turns it on, because a real API reflecting a token in an
    #: error body is exactly how a secret reaches an exportable column -- and a
    #: redaction test against a server that never echoes anything is a test that
    #: passes by having nothing to redact.
    echo_auth = False

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        self._respond()

    def _respond(self) -> None:
        type(self).seen_headers.append(dict(self.headers))
        payload = dict(type(self).payload)
        if type(self).echo_auth:
            payload["echoed_authorization"] = self.headers.get("Authorization", "")
        body = json.dumps(payload).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        """Silence. The suite's output is not this server's log."""


@pytest.fixture
def server() -> Iterator[str]:
    _Handler.status = 200
    _Handler.payload = {"order_id": "ord_1", "total": 12}
    _Handler.seen_headers = []
    _Handler.echo_auth = False
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _probe_text(host: str, *, steps: str | None = None, secret: bool = False) -> str:
    body = steps or (
        "steps:\n"
        "  - kind: http\n"
        "    method: POST\n"
        f'    url: "http://{host}/v1/checkout"\n'
        + ('    headers: { Authorization: "Bearer {{secret.PROBE_TOKEN}}" }\n' if secret else "")
        + "    body: { sku: demo }\n"
        '    expect: { status: 200, json_fields: ["order_id"] }\n'
    )
    return (
        "probe_id: checkout\n"
        "safe_path: sandbox\n"
        f'network: {{ deny_by_default: true, allow: ["{host}"] }}\n'
        "side_effect_policy: prohibited\n"
        + ("secret_refs: [env:PROBE_TOKEN]\n" if secret else "")
        + "runtime: { max_seconds: 20, max_memory_mb: 64, max_requests: 5 }\n"
        "cost: { max_model_calls: 1, max_tokens: 1000 }\n"
        "output: { retain_raw: false, redaction_policy: pii-default }\n"
        "cleanup: { required: true }\n" + body
    )


def _run(spec: ProbeSpec, **kwargs: Any) -> Any:
    defaults: dict[str, Any] = {
        "probe_definition_id": "pd_1",
        "probe_definition_revision_id": "pdrev_1",
        "records": None,
        "environ": {},
    }
    defaults.update(kwargs)
    return execute_probe(spec, **defaults)


def test_happy_path_records_run_and_observation(server: str) -> None:
    records = _Recorder()
    report = _run(parse_probe(_probe_text(server)), records=records)

    assert report.outcome == ProbeOutcome.SUCCESS
    assert len(records.runs) == 1
    assert len(records.observations) == 1

    run = records.runs[0]
    assert run.probe_definition_revision_id == "pdrev_1"
    assert run.outcome == "success"
    # v6.1 F8's stated residual: v1 verifies no cleanup and says so in the row.
    assert run.cleanup_verified is False

    observation = records.observations[0]
    assert observation.probe_run_id == run.id
    assert observation.fingerprint
    # No baseline exists yet, so similarity is absent rather than a fabricated 1.0.
    assert observation.similarity is None
    assert "ord_1" in str(observation.output)


def test_undeclared_host_is_refused_by_the_runner_not_by_add(server: str) -> None:
    """The enforcement-not-courtesy proof. `parse_probe` is bypassed on purpose.

    A revision can reach the runner without passing through `add`; if the only
    check lived there, this probe would open a socket to an undeclared host.
    """
    spec = ProbeSpec(
        name="rogue",
        safe_path="sandbox",
        diff_method="exact",
        steps=(
            HttpStep(
                method="GET",
                url="http://169.254.169.254/latest/meta-data/",
                host="169.254.169.254",
                headers={},
                body=None,
                expect=Expectation(),
            ),
        ),
        exercises=(),
        declared_hosts=frozenset({server}),
        secret_refs=frozenset(),
        max_seconds=5,
        max_requests=3,
        max_model_calls=0,
        max_tokens=100,
        redaction_policy=None,
        capability_manifest="",
        interaction="",
    )
    records = _Recorder()
    report = _run(spec, records=records)

    assert report.outcome == ProbeOutcome.BLOCKED
    assert "169.254.169.254" in str(report.refusal)
    # The refusal is recorded: a probe stopped by its manifest is a fact, and a
    # run that left no row would be indistinguishable from a probe never run.
    assert records.runs[0].outcome == "blocked_by_manifest"
    assert not records.observations


def test_secret_reaches_the_request_and_never_the_observation(server: str) -> None:
    """Resolved at send, redacted at record. `probe_observation.output` is exported.

    **The fixture echoes the token back in the body**, which is what makes this a
    test rather than a formality: an API that reflects an `Authorization` header
    into an error payload is the ordinary way a secret reaches a recorded
    observation, and against a server that never echoed anything this assertion
    would pass with redaction deleted.
    """
    _Handler.echo_auth = True
    records = _Recorder()
    spec = parse_probe(_probe_text(server, secret=True))
    report = _run(spec, records=records, environ={"PROBE_TOKEN": "s3cret-value"})

    assert report.outcome == ProbeOutcome.SUCCESS
    sent = _Handler.seen_headers[-1]
    assert sent["Authorization"] == "Bearer s3cret-value"

    recorded = str(records.observations[0].output)
    # The response really did carry it back...
    assert "echoed_authorization" in recorded
    # ...and what was recorded names the reference instead of the value.
    assert "s3cret-value" not in recorded
    assert "[redacted:PROBE_TOKEN]" in recorded
    assert "s3cret-value" not in str(report.refusal)


def test_missing_secret_fails_the_run_naming_the_ref_not_the_value(server: str) -> None:
    records = _Recorder()
    report = _run(parse_probe(_probe_text(server, secret=True)), records=records, environ={})

    assert report.outcome == ProbeOutcome.FAILURE
    assert "PROBE_TOKEN" in str(report.refusal)
    assert records.runs[0].outcome == "failure"


def test_status_violation_is_named_as_itself(server: str) -> None:
    """A structural failure is reported structurally, never as a similarity number."""
    _Handler.status = 503
    report = _run(parse_probe(_probe_text(server)))

    assert report.outcome == ProbeOutcome.FAILURE
    assert "503" in str(report.refusal)


def test_missing_json_field_is_named(server: str) -> None:
    _Handler.payload = {"total": 12}
    report = _run(parse_probe(_probe_text(server)))

    assert report.outcome == ProbeOutcome.FAILURE
    assert "order_id" in str(report.refusal)


def test_request_budget_is_enforced(server: str) -> None:
    """Two steps against a one-request budget: the second never opens a socket."""
    steps = (
        "steps:\n"
        "  - kind: http\n"
        "    method: GET\n"
        f'    url: "http://{server}/a"\n'
        "  - kind: http\n"
        "    method: GET\n"
        f'    url: "http://{server}/b"\n'
    )
    text = _probe_text(server, steps=steps).replace("max_requests: 5", "max_requests: 1")
    records = _Recorder()
    report = _run(parse_probe(text), records=records)

    assert report.outcome == ProbeOutcome.FAILURE
    assert "max_requests" in str(report.refusal)
    # The first step ran and was recorded; the budget cut the second.
    assert len(records.observations) == 1


def test_model_call_budget_is_enforced_without_an_adapter() -> None:
    """`cost.max_model_calls: 0` refuses before the seam is reached at all.

    The manifest's model-call ceiling is the runner's to enforce -- the agent
    seam's `Budget` has no such field -- so a probe declaring zero must be
    refused by this check rather than by an adapter that happens to be absent.
    """
    text = (
        "probe_id: p\n"
        "safe_path: mock\n"
        "network: { deny_by_default: true, allow: [] }\n"
        "side_effect_policy: prohibited\n"
        "runtime: { max_seconds: 5, max_memory_mb: 8, max_requests: 0 }\n"
        "cost: { max_model_calls: 0, max_tokens: 10 }\n"
        "output: { retain_raw: false }\n"
        "cleanup: { required: true }\n"
        "steps:\n"
        "  - kind: prompt\n"
        '    input: "hello"\n'
    )
    report = _run(parse_probe(text), agent=object())

    assert report.outcome == ProbeOutcome.BLOCKED
    assert "max_model_calls" in str(report.refusal)


def test_prompt_step_without_an_adapter_fails_rather_than_crashing() -> None:
    """R3: a probe with only http steps needs no model. One with a prompt says so."""
    text = (
        "probe_id: p\n"
        "safe_path: mock\n"
        "network: { deny_by_default: true, allow: [] }\n"
        "side_effect_policy: prohibited\n"
        "runtime: { max_seconds: 5, max_memory_mb: 8, max_requests: 0 }\n"
        "cost: { max_model_calls: 1, max_tokens: 10 }\n"
        "output: { retain_raw: false }\n"
        "cleanup: { required: true }\n"
        "steps:\n"
        "  - kind: prompt\n"
        '    input: "hello"\n'
    )
    report = _run(parse_probe(text), agent=None)

    assert report.outcome == ProbeOutcome.FAILURE
    assert "adapter" in str(report.refusal)


def test_sensor_hears_success_for_a_clean_run(server: str) -> None:
    sensor = _Sensor()
    _run(parse_probe(_probe_text(server)), sensor=sensor)
    assert sensor.beats == [("success", None)]


def test_sensor_hears_failure_when_the_probe_failed(server: str) -> None:
    """Health is about the channel. A failed run is a channel that did report."""
    _Handler.status = 500
    sensor = _Sensor()
    _run(parse_probe(_probe_text(server)), sensor=sensor)
    assert sensor.beats[0][0] == "failure"


def test_no_records_means_no_rows(server: str) -> None:
    """`run FILE` executes and persists nothing -- D-10's authoring loop."""
    report = _run(parse_probe(_probe_text(server)), records=None)
    assert report.outcome == ProbeOutcome.SUCCESS
    assert report.run_id is None


def test_redact_handles_a_secret_containing_another() -> None:
    """Longest first, so no fragment of a longer secret survives."""
    assert redact("abc-123 and abc", {"LONG": "abc-123", "SHORT": "abc"}) == (
        "[redacted:LONG] and [redacted:SHORT]"
    )


def test_identical_responses_fingerprint_identically(server: str) -> None:
    """Two runs against an unchanged system produce one fingerprint.

    **This test found a real defect on first run.** `latency_ms` was inside the
    fingerprinted payload, so two identical responses hashed differently and
    every rerun would have reported drift -- the baseline mechanism working
    perfectly and saying "changed" forever. It is v6.1 H5's false-staleness
    failure reached by a different road: a measurement that varies on its own
    must never sit inside the thing compared for change.

    The recorded output therefore carries status and body only. Latency is
    checked against `expect.latency_under_ms` at run time and reported to the
    operator, and it is not part of what the system is judged to have done.
    """
    first = _run(parse_probe(_probe_text(server)))
    second = _run(parse_probe(_probe_text(server)))

    assert first.steps[0].fingerprint == second.steps[0].fingerprint
    assert first.steps[0].output == second.steps[0].output
    assert "latency" not in first.steps[0].output


def test_undeclared_host_raises_the_registered_code_directly() -> None:
    """The code is `PROBE_HOST_UNDECLARED`, exiting 3 with the policy refusals."""
    from adopt_probe.runner import _send

    with pytest.raises(AdoptError) as caught:
        _send(
            HttpStep(
                method="GET",
                url="http://elsewhere.example/x",
                host="elsewhere.example",
                headers={},
                body=None,
                expect=Expectation(),
            ),
            secrets={},
            declared_hosts=frozenset({"127.0.0.1:1"}),
            timeout=1.0,
        )
    assert caught.value.code is ErrorCode.PROBE_HOST_UNDECLARED
