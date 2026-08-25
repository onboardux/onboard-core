"""The Build 5 demo journey -- v6.1 §6's Build 5 lines, run verbatim.

*Fails when* any line of the demo stops working end to end: a probe stops
validating, stops executing safely, stops being versionable as a baseline, stops
naming what changed, stops turning a contradiction into a conflict a client can
read -- or stops refusing an undeclared host. *Matters because* this is the
**Build Definition of Done**, and v6.1 §4 R1 requires every build to end in a
verb an FDE runs on a real engagement and gets value from that day. *No other
instrument catches it because* every unit test in this suite hands the runner or
the differ values it constructed; only this one proves the CLI writes a real
store, reads it back, and produces a document a client would receive.

**This is the proxy, never the gate.** v6.1 §10 makes gate G1 a safe repeatable
probe against a **real client AI deployment**, and that is a manual owner action
CI cannot perform. What runs here is a loopback `http.server` and the recorded
fake adapter: the whole machinery, none of the client. The session log records
the real run when it happens.

**Every step goes through the CLI as a subprocess**, the same entry-point module
the release binary compiles (CR-56), and every store assertion reads the SQLite
file directly -- so one bug cannot both write the wrong row and vouch for it.

The drift is produced by changing what the loopback fixture returns, which is
the honest analogue of the thing this build exists for: nothing in the
repository changed, no commit happened, and the system answers differently.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from adopt_obs import ExitCode

pytestmark = pytest.mark.e2e

ENTRY_POINT = (
    Path(__file__).resolve().parents[2] / "packages" / "adopt-cli" / "src" / "adopt_cli" / "main.py"
)
PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repos" / "web" / "fastapi_orders"
SCOPE = "northwind/acme-erp/orders-api/prod"
ANSWERS = {"artifact_access": True, "deploy_signal": True, "safe_interaction": True}

#: What the fake model replies, every run, in both processes. Identical by
#: construction: each CLI invocation is a fresh subprocess replaying turn 0, so
#: the prompt step contributes no drift of its own and the http step is the only
#: thing that can change. A prompt step that varied would make this journey
#: report drift whatever the fixture did.
FAKE_REPLY = {"reply": "Checkout requires an approved payment method and a stocked SKU."}

STEADY: dict[str, Any] = {"order_id": "ord_1", "total": 12, "status": "accepted"}
#: The system, changed underneath its documentation. No commit, no deploy of
#: ours -- the provider simply answers differently, which is the entire premise
#: of the Behaviour Baseline.
CHANGED: dict[str, Any] = {
    "order_id": "ord_1",
    "total": 12,
    "status": "held for manual review pending fraud scoring",
    "review_queue": "risk-ops",
}


class _Handler(BaseHTTPRequestHandler):
    payload: ClassVar[dict[str, Any]] = dict(STEADY)

    def do_GET(self) -> None:
        body = json.dumps(type(self).payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        """Silence. The suite's output is not this server's log."""


@pytest.fixture
def system() -> Iterator[str]:
    """The client's system: a loopback server whose answers we can change."""
    _Handler.payload = dict(STEADY)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    # `poll_interval` is the granularity `shutdown()` waits on, and the default
    # 0.5s is paid **per test** on teardown -- ~0.5s x every test in this file,
    # which is a measurable slice of the unit suite's hard-fail budget spent
    # waiting for a loop to notice it should stop. Shortening it is not a sleep
    # (the ban is on tests that wait for wall time to make an assertion true);
    # it is the teardown of a fixture nothing is asserting about.
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


def _run(
    *argv: str, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRY_POINT), *argv],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=None if env is None else {**os.environ, **env},
    )


def _payload(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        parsed: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError:  # pragma: no cover -- only on a broken envelope
        pytest.fail(f"stdout was not the JSON envelope:\n{completed.stdout[:2000]}")
    return parsed


def _sql(store: Path, query: str, *args: object) -> list[tuple[Any, ...]]:
    with sqlite3.connect(store) as connection:
        return list(connection.execute(query, args).fetchall())


def _git(*argv: str, cwd: Path) -> None:
    done = subprocess.run(["git", *argv], cwd=str(cwd), check=False, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def _probe_file(host: str, uri: str) -> str:
    """The demo's probe: one http step, one prompt step, one exercised identity.

    `exercises` is what turns a drift into a conflict. Without it the probe would
    observe a change nobody said was about anything, and this journey would prove
    only half of Bet 4.
    """
    return textwrap.dedent(f"""\
        probe_id: checkout-happy-path
        safe_path: sandbox
        network: {{ deny_by_default: true, allow: ["{host}"] }}
        http_methods: {{ allow: [GET] }}
        side_effect_policy: prohibited
        runtime: {{ max_seconds: 30, max_memory_mb: 256, max_requests: 10 }}
        cost: {{ max_model_calls: 2, max_tokens: 8000 }}
        output: {{ retain_raw: false, redaction_policy: pii-default }}
        cleanup: {{ required: true }}
        exercises: ["{uri}"]
        diff_method: exact
        steps:
          - kind: http
            method: GET
            url: "http://{host}/v1/checkout"
            expect: {{ status: 200, json_fields: ["order_id", "total"] }}
          - kind: prompt
            input: "Summarize the checkout policy for a customer."
            expect: {{ min_similarity: 0.92 }}
        """)


ROGUE = textwrap.dedent("""\
    probe_id: rogue
    safe_path: sandbox
    network: { deny_by_default: true, allow: ["127.0.0.1:9"] }
    side_effect_policy: prohibited
    runtime: { max_seconds: 5, max_memory_mb: 64, max_requests: 1 }
    cost: { max_model_calls: 0, max_tokens: 100 }
    output: { retain_raw: false }
    cleanup: { required: true }
    steps:
      - kind: http
        method: GET
        url: "http://169.254.169.254/latest/meta-data/"
    """)


DOC_TEMPLATE = """---
audience: client_ops
kind: procedure
---
# Checkout acceptance

The checkout endpoint `{uri}` accepts an order immediately and returns
`status: accepted`. There is no manual review step: a customer who completes
checkout has an accepted order, and support can tell them so.
"""


@pytest.fixture
def journey(tmp_path: Path, system: str) -> dict[str, Any]:
    """`init` -> `map` -> `ingest` -> a probe file naming a real identity.

    The document is written **after** the map deliberately: it cites an identity
    URI the map produced, which is Build 2's one auto-binding tier, and a
    document written before the map could not name one.
    """
    if not shutil.which("git"):  # pragma: no cover -- every CI runner ships git
        pytest.skip("git is not on PATH, and ingest reads a real checkout")

    checkout = tmp_path / "orders-api"
    shutil.copytree(FIXTURE, checkout)
    _git("init", "-q", cwd=checkout)
    _git("add", "-A", cwd=checkout)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "initial", cwd=checkout)

    store = tmp_path / "store.db"
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")

    init = _run(
        "init",
        ".",
        "--scope",
        SCOPE,
        "--answers",
        str(answers),
        "--archetype",
        "web",
        "--store",
        str(store),
        "--json",
        cwd=checkout,
    )
    assert init.returncode == ExitCode.SUCCESS, init.stderr

    mapped = _run("map", ".", "--store", str(store), "--json", cwd=checkout)
    assert mapped.returncode == ExitCode.SUCCESS, mapped.stderr

    # A real identity the map produced. Sorted, so the journey names the same
    # one on every machine.
    rows = _sql(store, "SELECT uri FROM identity ORDER BY uri LIMIT 1")
    assert rows, "the map produced no identities, so nothing could be exercised"
    uri = str(rows[0][0])

    (checkout / "docs").mkdir(exist_ok=True)
    (checkout / "docs" / "checkout.md").write_text(DOC_TEMPLATE.format(uri=uri), encoding="utf-8")
    ingested = _run("ingest", "docs", "--store", str(store), "--json", cwd=checkout)
    assert ingested.returncode == ExitCode.SUCCESS, ingested.stderr

    probes = checkout / "probes"
    probes.mkdir()
    (probes / "checkout-happy-path.yaml").write_text(_probe_file(system, uri), encoding="utf-8")
    (probes / "rogue.yaml").write_text(ROGUE, encoding="utf-8")

    recorded = tmp_path / "recorded.json"
    recorded.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "text": json.dumps(FAKE_REPLY),
                        "tool_calls": [],
                        "input_tokens": 10,
                        "output_tokens": 5,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    return {
        "checkout": checkout,
        "store": store,
        "uri": uri,
        "out": tmp_path / "handover",
        "env": {
            "ADOPT_ADAPTER": "fake_recorded",
            "ADOPT_ADAPTER_ENDPOINT": str(recorded),
            "ADOPT_PROMPTS_DIR": str(PROMPTS),
        },
    }


def _probe(journey: dict[str, Any], *argv: str) -> subprocess.CompletedProcess[str]:
    return _run(
        "probe",
        *argv,
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
        env=journey["env"],
    )


def _through_baseline(journey: dict[str, Any]) -> None:
    """Demo lines 1-3: add, run, baseline --set."""
    added = _probe(journey, "add", "probes/checkout-happy-path.yaml")
    assert added.returncode == ExitCode.SUCCESS, added.stderr

    ran = _probe(journey, "run", "--all")
    assert ran.returncode == ExitCode.SUCCESS, ran.stderr
    assert _payload(ran)["runs"][0]["outcome"] == "success"

    versioned = _probe(journey, "baseline", "--set")
    assert versioned.returncode == ExitCode.SUCCESS, versioned.stderr


def _drift(journey: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    """The system changes underneath the documentation, and the probe reruns."""
    _Handler.payload = dict(CHANGED)
    rerun = _probe(journey, "run", "--all")
    assert rerun.returncode == ExitCode.SUCCESS, rerun.stderr
    return rerun


# -- demo line 1: the probe validates ---------------------------------------


def test_add_validates_the_safe_path_declared_hosts_and_budget(journey: dict[str, Any]) -> None:
    """`adopt probe add probes/checkout-happy-path.yaml`, the demo's first line."""
    added = _probe(journey, "add", "probes/checkout-happy-path.yaml")
    assert added.returncode == ExitCode.SUCCESS, added.stderr

    payload = _payload(added)
    assert payload["outcome"] == "created"
    assert payload["safe_path"] == "sandbox"
    assert payload["declared_hosts"]
    assert payload["steps"] == 2
    assert payload["exercises"] == [journey["uri"]]

    definitions = _sql(journey["store"], "SELECT name, current_revision_id FROM probe_definition")
    assert len(definitions) == 1
    assert definitions[0][0] == "checkout-happy-path"
    assert definitions[0][1], "the definition's head pointer was never advanced"


# -- demo line 2: it executes and records -----------------------------------


def test_run_executes_and_records_the_run_and_its_observations(journey: dict[str, Any]) -> None:
    """`adopt probe run --all`, the demo's second line.

    The prompt step goes through the real `adopt_agent.Runner` against the
    recorded fake adapter -- the seam, the skill and the budget are all exercised
    and only the model's reply is scripted. No network, no credential.
    """
    added = _probe(journey, "add", "probes/checkout-happy-path.yaml")
    assert added.returncode == ExitCode.SUCCESS, added.stderr

    ran = _probe(journey, "run", "--all")
    assert ran.returncode == ExitCode.SUCCESS, ran.stderr

    payload = _payload(ran)
    assert payload["probes"] == 1
    assert payload["runs"][0]["outcome"] == "success"
    assert payload["runs"][0]["steps"] == 2

    runs = _sql(journey["store"], "SELECT id, outcome, cleanup_verified FROM probe_run")
    assert len(runs) == 1
    assert runs[0][1] == "success"
    # v6.1 F8's stated residual: v1 verifies no cleanup and records that it did not.
    assert runs[0][2] == 0

    observations = _sql(
        journey["store"],
        "SELECT output, fingerprint, similarity FROM probe_observation ORDER BY id",
    )
    assert len(observations) == 2
    assert all(row[1] for row in observations), "an observation was recorded unfingerprinted"
    # No baseline existed, so similarity is absent rather than a fabricated 1.0.
    assert all(row[2] is None for row in observations)

    # The probe is a sensor from day one, so Builds 6 and 8 need no rework.
    sensors = _sql(journey["store"], "SELECT kind FROM sensor")
    assert ("probe",) in sensors
    assert _sql(journey["store"], "SELECT outcome FROM sensor_heartbeat") == [("success",)]


# -- demo line 3: the baseline is versioned ---------------------------------


def test_baseline_set_versions_the_recorded_run(journey: dict[str, Any]) -> None:
    """`adopt probe baseline --set`, the demo's third line."""
    _through_baseline(journey)

    baselines = _sql(
        journey["store"],
        "SELECT id, recorded_output, fingerprint, approved_at, redaction_policy "
        "FROM baseline_version",
    )
    assert len(baselines) == 1
    assert baselines[0][1] and "ord_1" in baselines[0][1]
    assert baselines[0][2], "the baseline was versioned without a fingerprint"
    assert baselines[0][3], "`--set` is the approval; it must be stamped"
    assert baselines[0][4] == "pii-default"


# -- demo line 4: run + diff names what changed -----------------------------


def test_diff_names_what_changed_and_exits_four(journey: dict[str, Any]) -> None:
    """`adopt probe run --all && adopt probe diff`, the demo's fourth line.

    Nothing in the repository changed. The system simply answers differently --
    which is the whole premise of the Behaviour Baseline and the thing no
    artifact-reading build in this programme can see.
    """
    _through_baseline(journey)
    rerun = _drift(journey)

    # A drifted run **worked**: it reached the system, held its declared
    # invariants and observed a change. Exiting non-zero for that would make the
    # product's success look like a failure.
    assert _payload(rerun)["runs"][0]["outcome"] == "diff"
    assert _payload(rerun)["drifted"] == 1

    diffed = _probe(journey, "diff")
    assert diffed.returncode == ExitCode.DEGRADED_WITH_FINDINGS, diffed.stderr

    payload = _payload(diffed)
    assert payload["drifted"] == 1
    comparison = payload["comparisons"][0]
    assert comparison["verdict"] == "drift"
    # Named per step: the http step changed, the prompt step did not.
    http_step = comparison["steps"][0]
    assert http_step["verdict"] == "drift"
    assert http_step["detail"], "drift was reported without naming what changed"
    assert comparison["steps"][1]["verdict"] == "unchanged"

    # The comparison the run recorded, not a re-judgement: `diff` writes nothing.
    latest = _sql(
        journey["store"],
        "SELECT outcome FROM probe_run ORDER BY started_at DESC, id DESC LIMIT 1",
    )
    assert latest[0][0] == "diff"


def test_diff_is_clean_and_exits_zero_when_the_system_is_steady(
    journey: dict[str, Any],
) -> None:
    """The control. Without it, a `diff` that reported drift unconditionally
    would satisfy every assertion in the test above."""
    _through_baseline(journey)

    rerun = _probe(journey, "run", "--all")
    assert rerun.returncode == ExitCode.SUCCESS, rerun.stderr
    assert _payload(rerun)["runs"][0]["outcome"] == "success"

    diffed = _probe(journey, "diff")
    assert diffed.returncode == ExitCode.SUCCESS, diffed.stderr
    assert _payload(diffed)["drifted"] == 0
    assert _payload(diffed)["comparisons"][0]["verdict"] == "unchanged"


def test_editing_the_probe_reports_probe_changed_not_drift(journey: dict[str, Any]) -> None:
    """**The guard, end to end.** *Fails when* editing our own file reads as the
    client's system changing.

    *Matters because* that sentence sends an FDE to a deployment that never
    changed, and the second false alarm is the one after which they stop
    looking. The system's answer is changed **as well**, so a comparison that
    skipped the revision check would find real difference and call it drift.
    """
    _through_baseline(journey)

    edited = _probe_file(_host_of(journey), journey["uri"]).replace(
        "/v1/checkout", "/v1/checkout-v2"
    )
    (journey["checkout"] / "probes" / "checkout-happy-path.yaml").write_text(
        edited, encoding="utf-8"
    )
    revised = _probe(journey, "add", "probes/checkout-happy-path.yaml")
    assert revised.returncode == ExitCode.SUCCESS, revised.stderr
    assert _payload(revised)["outcome"] == "revised"

    _drift(journey)

    diffed = _probe(journey, "diff")
    assert diffed.returncode == ExitCode.SUCCESS, "an edit to our own probe is not a finding"
    comparison = _payload(diffed)["comparisons"][0]
    assert comparison["verdict"] == "probe_changed"
    assert comparison["baseline_revision"] != comparison["run_revision"]


def _host_of(journey: dict[str, Any]) -> str:
    """The loopback host the probe file already names.

    Read back out of the written file rather than threaded through the fixture,
    so the edited probe cannot accidentally point somewhere else.
    """
    text = (journey["checkout"] / "probes" / "checkout-happy-path.yaml").read_text(encoding="utf-8")
    marker = 'allow: ["'
    start = text.index(marker) + len(marker)
    return text[start : text.index('"', start)]


# -- Bet 4: the contradiction becomes a deliverable -------------------------


def test_the_drift_becomes_a_conflict_the_gaps_queue_and_the_pack_both_show(
    journey: dict[str, Any],
) -> None:
    """v6.1 Bet 4: *a conflict between intent and behaviour is a deliverable.*

    *Fails when* a probe contradicting confirmed knowledge leaves no trace a
    human will see. *Matters because* the whole reason to watch behaviour is to
    find out that the document is now wrong -- and a finding that reaches no
    reader is not a finding.
    """
    _through_baseline(journey)
    _drift(journey)

    conflicts = _sql(
        journey["store"],
        "SELECT identity_id, intent_revision_id, actual_revision_id, disposition FROM conflict",
    )
    assert len(conflicts) == 1, "the drift contradicted confirmed knowledge and nothing recorded it"
    assert conflicts[0][1], "the conflict cites no confirmed revision on the intent side"
    # v1 writes no knowledge from probe output: nothing is fabricated to point at.
    assert conflicts[0][2] is None
    assert conflicts[0][3] == "open"

    # It reaches the FDE's queue...
    listed = _payload(
        _run("gaps", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )
    assert listed["conflicts"], "`adopt gaps` did not carry the conflict"
    assert listed["conflicts"][0]["uri"] == journey["uri"]

    # ...and the client's document.
    packed = _run(
        "pack",
        "--audience",
        "client_ops",
        "--out",
        str(journey["out"]),
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert packed.returncode == ExitCode.SUCCESS, packed.stderr
    assert _payload(packed)["conflicts"] == 1

    document = (journey["out"] / "client_ops.md").read_text(encoding="utf-8")
    assert "Contradicted by observation" in document
    assert journey["uri"] in document


def test_rerunning_the_drift_does_not_file_the_conflict_twice(
    journey: dict[str, Any],
) -> None:
    """*Fails when* one disagreement is filed on every run.

    *Matters because* a reviewer who sees the same row five times stops reading
    the list -- and then the sixth, which is a different disagreement, goes
    unread too.
    """
    _through_baseline(journey)
    _drift(journey)
    _drift(journey)

    assert _sql(journey["store"], "SELECT count(*) FROM conflict") == [(1,)]


def test_the_pack_is_byte_stable_with_a_conflict_in_it(journey: dict[str, Any]) -> None:
    """Build 4's promise survives Build 5's addition to the appendix."""
    _through_baseline(journey)
    _drift(journey)

    def _pack() -> bytes:
        done = _run(
            "pack",
            "--audience",
            "client_ops",
            "--out",
            str(journey["out"]),
            "--store",
            str(journey["store"]),
            "--json",
            cwd=journey["checkout"],
        )
        assert done.returncode == ExitCode.SUCCESS, done.stderr
        return (journey["out"] / "client_ops.md").read_bytes()

    assert _pack() == _pack()


# -- the negative control ---------------------------------------------------


def test_a_rogue_probe_is_refused_naming_the_undeclared_host(journey: dict[str, Any]) -> None:
    """`adopt probe run rogue.yaml`, the demo's last line.

    *Fails when* a probe reaches a host its manifest never declared. *Matters
    because* this refusal is the whole safety argument for dropping v4's sandbox
    (v6.1 D2, upheld by the F8 audit): a probe carries no executable content and
    only the runner opens a socket, so the allow-list *is* the boundary.
    """
    refused = _probe(journey, "run", "probes/rogue.yaml")

    assert refused.returncode == ExitCode.POLICY_REFUSAL
    envelope = refused.stdout + refused.stderr
    assert "PROBE_HOST_UNDECLARED" in envelope, envelope[:2000]
    assert "169.254.169.254" in envelope

    # And it left nothing behind: a probe nobody added has no definition, no
    # revision and no run (sprint-plan D-10).
    assert _sql(journey["store"], "SELECT count(*) FROM probe_definition") == [(0,)]
    assert _sql(journey["store"], "SELECT count(*) FROM probe_run") == [(0,)]
