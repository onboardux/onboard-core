"""S8.3 T3: `adopt ci-sense` runs the declared probes and carries what they saw.

*Fails when* the probe half of a sense payload stops carrying what the plane
needs to compare, or when it starts writing to the replica it was handed.

*Matters because* this is the half that watches a system whose behaviour changes
without a commit -- the single system class Build 5 exists for -- and it is the
half that runs inside a customer's network, where the plane has no reach. Two
things can go wrong silently: a payload that omits the revision id makes every
probe edit arrive at the plane as a client incident, and a run that recorded
anything locally would be a canon write from a caller that is not the writer,
clobbered by the next `adopt pull` and invisible until somebody wondered why
their probe history had gaps.

*No other instrument catches it because* `test_ci_sense_payload.py` pins a
rendering built by hand, and the plane's own tests parse a fixture. Only this
file runs a real probe against a real socket and looks at what came back.

The HTTP fixture is a loopback `http.server` on an ephemeral port, matching
`test_probe_runner.py` and `test_probe_baseline_diff.py`. No sleeps: nothing
here waits for wall time to make an assertion true.
"""

import json
import textwrap
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest
from adopt_probe import ProbeOutcome, parse_probe

from adopt_cli.commands._ci_sense_probes import ProbeSection, run_payload_probes
from adopt_cli.commands._probe_support import add_probe
from adopt_model import ProbeObservation, ProbeRun, SensorHeartbeat
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

pytestmark = pytest.mark.unit


class _Handler(BaseHTTPRequestHandler):
    payload: ClassVar[dict[str, Any]] = {"order_id": "ord_1", "total": 12}

    def do_GET(self) -> None:
        body = json.dumps(type(self).payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        """Silence."""


@pytest.fixture
def server() -> Iterator[str]:
    _Handler.payload = {"order_id": "ord_1", "total": 12}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield f"127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _probe_text(host: str, *, name: str = "checkout", exercises: str = "[]") -> str:
    return textwrap.dedent(f"""\
        probe_id: {name}
        safe_path: sandbox
        network: {{ deny_by_default: true, allow: ["{host}"] }}
        side_effect_policy: prohibited
        runtime: {{ max_seconds: 20, max_memory_mb: 64, max_requests: 5 }}
        cost: {{ max_model_calls: 0, max_tokens: 100 }}
        output: {{ retain_raw: false, redaction_policy: pii-default }}
        cleanup: {{ required: true }}
        exercises: {exercises}
        steps:
          - kind: http
            method: GET
            url: "http://{host}/v1/checkout"
            expect: {{ status: 200, json_fields: ["order_id"] }}
        """)


def test_a_declared_probe_runs_and_its_result_travels(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """The whole of T3 in one assertion set: it ran, and the plane can use it.

    The revision id is the field this test exists for. Without it the plane
    cannot tell a run of an edited probe from a run of the same one, so every
    probe edit would be reported to a reviewer as their system changing --
    Build 5's `probe_changed` guard reaching across the wire.
    """
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))

    section = run_payload_probes(s4_store, s4_scope)

    assert section.ran is True
    assert section.reason is None
    assert len(section.results) == 1
    result = section.results[0]
    assert result["probe"] == "checkout"
    assert result["outcome"] == ProbeOutcome.SUCCESS
    assert result["probe_definition_revision_id"], (
        "without the revision id the plane cannot tell `the probe changed` from "
        "`the system changed`, which is the one mistake that trains an FDE to "
        "ignore both commands"
    )
    assert result["refusal"] is None
    # One step, so one output and one fingerprint, and the output is the client
    # system's own answer -- which is what `compare_run` needs and cannot get
    # from a digest.
    assert len(result["outputs"]) == 1
    assert "ord_1" in str(result["outputs"][0])
    assert len(result["fingerprints"]) == 1

    assert section.heartbeats == [{"probe": "checkout", "outcome": "success", "detail": None}]


def test_the_run_writes_nothing_at_all_to_the_replica(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """*Fails when* ci-sense becomes a second writer of an operated system's canon.

    *Matters because* R9 makes the plane the sole writer after activation. A run,
    an observation or a heartbeat written here would be clobbered by the next
    `adopt pull` -- so the symptom is not an error but a history with holes in
    it, discovered by whoever asks why a probe seems to have skipped a week.

    *No other instrument catches it because* the payload assertions above pass
    identically whether or not rows were also written: this is a claim about
    what is **absent**, and only a row count can make it.
    """
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))
    records = s4_store.export_records()

    section = run_payload_probes(s4_store, s4_scope)
    assert section.results, "the negative below proves nothing if nothing ran"

    assert records.table_rows("probe_run", ProbeRun) == []
    assert records.table_rows("probe_observation", ProbeObservation) == []
    assert records.table_rows("sensor_heartbeat", SensorHeartbeat) == []


def test_a_scope_with_no_probes_says_so_rather_than_reporting_a_clean_run(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """*Fails when* "nothing to look at" becomes indistinguishable from "all clear".

    *Matters because* the plane reads `ran` to decide whether the probe half of
    this observation is evidence. A section that reported `ran: true` with no
    results would tell the plane every probe passed, which for a scope that has
    no probes is a claim about a measurement nobody made.
    """
    section = run_payload_probes(s4_store, s4_scope)

    assert section.ran is False
    assert section.reason == "no probes are defined in this scope"
    assert section.payload()["results"] == []


def test_the_rendered_section_is_ordered_by_probe_name(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """*Fails when* the payload stops being a function of what was found.

    *Matters because* the committed fixture is compared byte for byte across two
    repositories: a section whose order came from store iteration would fail the
    pin intermittently rather than never, which is the failure mode that gets a
    test deleted instead of read.
    """
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server, name="zeta")))
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server, name="alpha")))

    rendered = run_payload_probes(s4_store, s4_scope).payload()

    assert [row["probe"] for row in rendered["results"]] == ["alpha", "zeta"]
    assert [row["probe"] for row in rendered["heartbeats"]] == ["alpha", "zeta"]


def test_the_rendered_probe_result_has_the_shape_the_fixture_pins(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """*Fails when* the producer grows a field the committed fixture does not have.

    *Matters because* `test_ci_sense_payload.py` builds its probe section **by
    hand** -- executing a probe needs a socket and a store, which a rendering pin
    should not -- so a field added to `_result` would land in the real payload
    and never in the fixture. The plane would then be pinned against a shape the
    producer no longer emits, which is precisely the silent two-repository drift
    the fixture exists to prevent.

    *No other instrument catches it because* both fixtures stay internally
    consistent: the hand-built one renders, the real one runs, and nothing
    compares them.
    """
    from tests.unit.test_ci_sense_payload import _probe_section

    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))
    real = run_payload_probes(s4_store, s4_scope).payload()
    pinned = _probe_section().payload()

    assert real.keys() == pinned.keys()
    assert real["results"][0].keys() == pinned["results"][0].keys()
    assert real["heartbeats"][0].keys() == pinned["heartbeats"][0].keys()


def test_the_no_probes_section_still_declares_itself(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """`--no-probes` parity with `adopt refresh`: a reason, not an omission.

    *Fails when* skipping the probe half becomes indistinguishable from a build
    of the CLI that has no probe half. *Matters because* the plane must be able
    to tell "this operator chose not to probe today" from "this producer cannot
    probe" -- the first leaves the sensor's silence meaningful and the second
    does not.
    """
    section = ProbeSection(ran=False, reason="--no-probes")

    rendered = section.payload()
    assert rendered["ran"] is False
    assert rendered["reason"] == "--no-probes"
    assert rendered["heartbeats"] == [], (
        "a skipped half emits no heartbeat, because no channel attempted anything "
        "-- a heartbeat here would make silence look like health"
    )
