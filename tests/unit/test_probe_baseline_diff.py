"""Baselines, and the comparison against them.

*Fails when* a baseline stops being made from a run that observed something,
or when `diff` starts confusing *the probe changed* with *the system changed*.
*Matters because* those two sentences send an FDE to different places -- one to
the client's deployment, one to our own file -- and a tool that says the first
when it means the second burns its credibility on the second false alarm.
*No other instrument catches it because* the runner's tests prove what a run
records and the manifest's prove what a probe is; neither compares two runs, and
the comparison is the entire product of this sprint.

**The revision-guard test is the one that would fail if the guard were dropped**,
and it is written so that it cannot pass by accident: the probe is genuinely
edited, a genuine second revision is appended, and the run genuinely re-executes
-- so a `diff` that compared outputs without checking revisions would find real
textual difference and report real drift.

The HTTP fixture is a loopback `http.server` on an ephemeral port, matching
`test_probe_runner.py`. No sleeps anywhere: every clock is the injected one.
"""

import json
import textwrap
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest
from adopt_probe import (
    DRIFT,
    NO_BASELINE,
    PROBE_CHANGED,
    UNCHANGED,
    WITHIN_THRESHOLD,
    Baseline,
    ProbeOutcome,
    canonical_recorded_output,
    compare_run,
    execute_probe,
    parse_probe,
)
from adopt_probe.diff import compare_outputs
from adopt_probe.manifest import Expectation

from adopt_cli.commands._probe_support import add_probe, diff_probes, set_baselines
from adopt_model import BaselineVersion, ProbeObservation, ProbeRun
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

pytestmark = pytest.mark.unit


class _Handler(BaseHTTPRequestHandler):
    status = 200
    payload: ClassVar[dict[str, Any]] = {"order_id": "ord_1", "total": 12}

    def do_GET(self) -> None:
        body = json.dumps(type(self).payload).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        """Silence."""


@pytest.fixture
def server() -> Iterator[str]:
    _Handler.status = 200
    _Handler.payload = {"order_id": "ord_1", "total": 12}
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


def _probe_text(host: str, *, path: str = "/v1/checkout", fields: str = '["order_id"]') -> str:
    return textwrap.dedent(f"""\
        probe_id: checkout
        safe_path: sandbox
        network: {{ deny_by_default: true, allow: ["{host}"] }}
        side_effect_policy: prohibited
        runtime: {{ max_seconds: 20, max_memory_mb: 64, max_requests: 5 }}
        cost: {{ max_model_calls: 0, max_tokens: 100 }}
        output: {{ retain_raw: false, redaction_policy: pii-default }}
        cleanup: {{ required: true }}
        steps:
          - kind: http
            method: GET
            url: "http://{host}{path}"
            expect: {{ status: 200, json_fields: {fields} }}
        """)


def _run_stored(store: SqliteStoreHandle, scope: Scope, host: str, **kwargs: Any) -> Any:
    """Execute the scope's one probe at its active revision and record it."""
    from adopt_cli.commands._probe_support import active_revision, probes_in_scope

    probe = probes_in_scope(store, scope)[0]
    revision = active_revision(store, probe)
    assert revision is not None
    return execute_probe(
        parse_probe(revision.capability_manifest),
        probe_definition_id=probe.id,
        probe_definition_revision_id=revision.id,
        records=store.probe_run_records(),
        environ={},
        clock=store.clock,
        **kwargs,
    )


def _baseline_of(store: SqliteStoreHandle) -> BaselineVersion:
    rows = store.export_records().table_rows("baseline_version", BaselineVersion)
    assert len(rows) == 1
    return rows[0]


# -- what may become a baseline ---------------------------------------------


def test_set_versions_the_latest_run_and_names_which(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))
    _run_stored(s4_store, s4_scope, server)

    payload = set_baselines(s4_store, s4_scope)

    assert payload["baselines"] == 1
    entry = payload["set"][0]
    assert entry["from_outcome"] == ProbeOutcome.SUCCESS
    assert entry["steps"] == 1

    row = _baseline_of(s4_store)
    assert row.recorded_output is not None
    assert "ord_1" in row.recorded_output
    assert row.fingerprint
    # Copied from the manifest, so a baseline states the policy its text was
    # redacted under rather than leaving a reader to guess.
    assert row.redaction_policy == "pii-default"
    # No prompt step ran, so no model answered. Naming an adapter here would put
    # an environment fact into an exportable row that was never true.
    assert row.model_provider_version is None
    assert row.approved_at is not None


def test_a_failed_run_is_never_eligible(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """*Fails when* a fault can be versioned as how the system behaves.

    *Matters because* a baseline taken from a 500 makes the next healthy run
    read as drift **away from** the bug -- the queue then reports a regression
    every time the system works.
    """
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))
    _Handler.status = 500
    report = _run_stored(s4_store, s4_scope, server)
    assert report.outcome == ProbeOutcome.FAILURE

    with pytest.raises(AdoptError) as raised:
        set_baselines(s4_store, s4_scope)
    assert raised.value.code is ErrorCode.PROBE_BASELINE_MISSING


def test_set_with_no_runs_at_all_raises_baseline_missing(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """The second shape of the same code: a probe exists, nothing has run it."""
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))

    with pytest.raises(AdoptError) as raised:
        set_baselines(s4_store, s4_scope)
    assert raised.value.code is ErrorCode.PROBE_BASELINE_MISSING
    assert "run" in str(raised.value.hint)


def test_rebaselining_after_drift_is_permitted_and_reported(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """*Fails when* accepting drift becomes indistinguishable from confirming a state.

    *Matters because* `--set` after a drift **is** a human accepting a change,
    and a verb that printed the same line for both would let a regression be
    blessed without anyone noticing they did it.
    """
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))
    _run_stored(s4_store, s4_scope, server)
    set_baselines(s4_store, s4_scope)

    _Handler.payload = {"order_id": "ord_2", "total": 999999}
    drifted = _run_stored(s4_store, s4_scope, server, baseline=_current_baseline(s4_store))
    assert drifted.outcome == ProbeOutcome.DIFF

    payload = set_baselines(s4_store, s4_scope)
    assert payload["set"][0]["from_outcome"] == ProbeOutcome.DIFF


def _current_baseline(store: SqliteStoreHandle) -> Baseline:
    from adopt_probe import baseline_from_row

    return baseline_from_row(_newest_baseline(store))


def _newest_baseline(store: SqliteStoreHandle) -> BaselineVersion:
    rows = store.export_records().table_rows("baseline_version", BaselineVersion)
    return sorted(rows, key=lambda row: (row.created_at, row.id))[-1]


# -- the comparison itself ---------------------------------------------------


def _output(status: int, body: dict[str, Any]) -> str:
    return json.dumps(
        {"kind": "http", "status": status, "body": json.dumps(body)},
        sort_keys=True,
        separators=(",", ":"),
    )


def test_byte_identical_output_is_unchanged() -> None:
    same = _output(200, {"order_id": "ord_1"})
    verdict = compare_outputs(same, same, index=0, kind="http")
    assert verdict.verdict == UNCHANGED
    assert verdict.similarity == 1.0


def test_a_status_change_is_named_as_a_status_change() -> None:
    """*Fails when* a structural difference is reported as a similarity number.

    *Matters because* "similarity 0.71" sends a reader to read two bodies;
    "status 500, the baseline recorded 200" sends them to the deployment.
    """
    verdict = compare_outputs(
        _output(200, {"order_id": "ord_1"}),
        _output(500, {"order_id": "ord_1"}),
        index=0,
        kind="http",
        expect=Expectation(status=200),
    )
    assert verdict.verdict == DRIFT
    assert "status 500" in str(verdict.detail)
    assert "200" in str(verdict.detail)


def test_a_field_the_baseline_carried_and_the_run_lost_is_named() -> None:
    verdict = compare_outputs(
        _output(200, {"order_id": "ord_1", "total": 12}),
        _output(200, {"total": 12}),
        index=0,
        kind="http",
        expect=Expectation(status=200, json_fields=("order_id",)),
    )
    assert verdict.verdict == DRIFT
    assert "order_id" in str(verdict.detail)


def test_a_field_neither_side_ever_had_is_not_reported_as_a_change() -> None:
    """*Fails when* a probe's own long-standing broken invariant reads as drift.

    *Matters because* a probe declaring a field the system has never returned is
    a probe that has been failing since it was written -- the runner reports that
    as a failed run. Reporting it *here* as well would claim the system changed
    when it has been this way all along, which is the false-alarm class this
    module exists to avoid.
    """
    verdict = compare_outputs(
        _output(200, {"order_id": "ord_1"}),
        _output(200, {"order_id": "ord_1", "extra": 1}),
        index=0,
        kind="http",
        expect=Expectation(status=200, json_fields=("never_present",)),
    )
    assert "never_present" not in str(verdict.detail)


def test_a_small_difference_within_the_declared_threshold_is_reported_not_drift() -> None:
    """The threshold is the probe's, and being under it is still worth printing.

    A value creeping toward the threshold across a month is drift arriving; a
    verdict that printed nothing until the day it crossed would hide exactly
    that.
    """
    baseline = _output(200, {"note": "the quick brown fox jumps over the lazy dog today"})
    current = _output(200, {"note": "the quick brown fox jumps over the lazy dog todav"})
    verdict = compare_outputs(
        baseline, current, index=0, kind="http", expect=Expectation(min_similarity=0.5)
    )
    assert verdict.verdict == WITHIN_THRESHOLD
    assert verdict.similarity is not None
    assert verdict.similarity >= 0.5
    assert verdict.detail


def test_text_below_the_declared_threshold_is_drift() -> None:
    verdict = compare_outputs(
        _output(200, {"note": "approvals are handled by the refund worker"}),
        _output(200, {"note": "zzzz"}),
        index=0,
        kind="http",
        expect=Expectation(min_similarity=0.92),
    )
    assert verdict.verdict == DRIFT
    assert verdict.similarity is not None
    assert verdict.similarity < 0.92


def test_no_baseline_is_an_answer_not_a_finding() -> None:
    comparison = compare_run(
        probe="checkout", baseline=None, run_revision_id="pdrev_1", run_id="prun_1", outputs=["{}"]
    )
    assert comparison.verdict == NO_BASELINE
    assert not comparison.drifted


def test_a_revision_mismatch_is_probe_changed_and_scores_nothing() -> None:
    """**The guard.** *Fails when* editing our own probe file reads as the client's
    system changing.

    *Matters because* that is the one mistake that trains an FDE to ignore the
    command: they go looking for a deployment change that never happened. The
    outputs here are genuinely different, so a comparison that skipped the
    revision check would find real drift and report it.
    """
    baseline = Baseline(id="bv_1", revision_id="pdrev_1", outputs=(_output(200, {"a": 1}),))
    comparison = compare_run(
        probe="checkout",
        baseline=baseline,
        run_revision_id="pdrev_2",
        run_id="prun_9",
        outputs=[_output(500, {"totally": "different"})],
    )

    assert comparison.verdict == PROBE_CHANGED
    assert not comparison.drifted
    # Both ids travel: the claim is about our file, and a reader must be able to
    # check it.
    assert comparison.baseline_revision_id == "pdrev_1"
    assert comparison.run_revision_id == "pdrev_2"
    # No number is computed at all. A meaningless one that exists gets read.
    assert comparison.steps == ()


def test_a_step_the_run_stopped_producing_is_drift() -> None:
    baseline = Baseline(
        id="bv_1",
        revision_id="pdrev_1",
        outputs=(_output(200, {"a": 1}), _output(200, {"b": 2})),
    )
    comparison = compare_run(
        probe="checkout",
        baseline=baseline,
        run_revision_id="pdrev_1",
        run_id="prun_1",
        outputs=[_output(200, {"a": 1})],
    )
    assert comparison.verdict == DRIFT
    assert comparison.steps[1].detail


def test_recorded_output_round_trips_per_step() -> None:
    """The stored form must split back into steps, or `diff` cannot name one."""
    from adopt_probe import split_recorded_output

    outputs = [_output(200, {"a": 1}), _output(201, {"b": 2})]
    assert split_recorded_output(canonical_recorded_output(outputs)) == tuple(outputs)


# -- the run records what it compared ---------------------------------------


def test_a_drifted_run_records_outcome_diff_and_a_similarity(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """*Fails when* a comparison happens and leaves no trace in the store.

    *Matters because* `probe_run.outcome` and `probe_observation.similarity` are
    insert-only: they are decided when the run finishes or never, and `diff`
    re-derives rather than mutating. If the run did not record them, nothing
    would.
    """
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))
    _run_stored(s4_store, s4_scope, server)
    set_baselines(s4_store, s4_scope)

    _Handler.payload = {"order_id": "ord_1", "total": 99999999, "surcharge": "new field entirely"}
    report = _run_stored(s4_store, s4_scope, server, baseline=_current_baseline(s4_store))

    assert report.outcome == ProbeOutcome.DIFF
    runs = s4_store.export_records().table_rows("probe_run", ProbeRun)
    latest = sorted(runs, key=lambda row: (row.started_at, row.id))[-1]
    assert str(latest.outcome) == ProbeOutcome.DIFF
    assert latest.baseline_version_id == _newest_baseline(s4_store).id

    observations = [
        row
        for row in s4_store.export_records().table_rows("probe_observation", ProbeObservation)
        if row.probe_run_id == latest.id
    ]
    assert observations[0].similarity is not None


def test_an_unchanged_rerun_stays_success(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """The control for the test above: a stable system must not report drift.

    Without it, a comparison that called everything drift would pass every
    assertion in this file that looks for drift.
    """
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))
    _run_stored(s4_store, s4_scope, server)
    set_baselines(s4_store, s4_scope)

    report = _run_stored(s4_store, s4_scope, server, baseline=_current_baseline(s4_store))
    assert report.outcome == ProbeOutcome.SUCCESS
    assert report.comparison is not None
    assert report.comparison.verdict == UNCHANGED


# -- the command ------------------------------------------------------------


def test_diff_reports_clean_when_nothing_changed(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))
    _run_stored(s4_store, s4_scope, server)
    set_baselines(s4_store, s4_scope)
    _run_stored(s4_store, s4_scope, server, baseline=_current_baseline(s4_store))

    payload = diff_probes(s4_store, s4_scope)
    assert payload["drifted"] == 0
    assert payload["comparisons"][0]["verdict"] == UNCHANGED


def test_diff_names_the_drifted_step(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))
    _run_stored(s4_store, s4_scope, server)
    set_baselines(s4_store, s4_scope)

    _Handler.payload = {"order_id": "ord_1", "total": 7, "wholly": "different body text here"}
    _run_stored(s4_store, s4_scope, server, baseline=_current_baseline(s4_store))

    payload = diff_probes(s4_store, s4_scope)
    assert payload["drifted"] == 1
    comparison = payload["comparisons"][0]
    assert comparison["verdict"] == DRIFT
    assert comparison["steps"][0]["detail"]
    assert comparison["baseline"] == _newest_baseline(s4_store).id


def test_diff_with_no_baseline_anywhere_raises(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))
    _run_stored(s4_store, s4_scope, server)

    with pytest.raises(AdoptError) as raised:
        diff_probes(s4_store, s4_scope)
    assert raised.value.code is ErrorCode.PROBE_BASELINE_MISSING


def test_diff_says_probe_changed_after_the_file_is_edited(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """The end-to-end form of the guard, through the store and the command.

    The probe is genuinely edited, a genuine revision is appended and the run
    genuinely re-executes against a **changed** response -- so a `diff` that
    compared outputs without checking revisions would find real difference and
    call it drift. It must not.
    """
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server)))
    _run_stored(s4_store, s4_scope, server)
    set_baselines(s4_store, s4_scope)

    # Edit the probe: a different path is a different question.
    add_probe(s4_store, s4_scope, parse_probe(_probe_text(server, path="/v1/checkout-v2")))
    _Handler.payload = {"order_id": "ord_totally_different", "total": 4242}
    _run_stored(s4_store, s4_scope, server)

    payload = diff_probes(s4_store, s4_scope)
    comparison = payload["comparisons"][0]

    assert comparison["verdict"] == PROBE_CHANGED
    assert payload["drifted"] == 0, "an edit to our own probe file is not the system changing"
    assert comparison["baseline_revision"] != comparison["run_revision"]


# -- the model is asked again, every run ------------------------------------


class _RecordingAgent:
    """A stand-in for `adopt_agent.Runner` that remembers what it was asked.

    Deliberately *not* the real seam: the property under test is what the probe
    runner **sends**, and the real seam would answer it from its annex -- which
    is precisely the behaviour that hid the defect.
    """

    def __init__(self) -> None:
        self.keys: list[str] = []

    def run(self, request: Any) -> Any:
        self.keys.append(request.idempotency_key)

        class _Result:
            status = "ok"
            output: ClassVar[dict[str, str]] = {"reply": "the checkout policy is unchanged"}

        return _Result()


def _prompt_probe(host: str) -> str:
    return textwrap.dedent(f"""        probe_id: chat
        safe_path: sandbox
        network: {{ deny_by_default: true, allow: ["{host}"] }}
        side_effect_policy: prohibited
        runtime: {{ max_seconds: 20, max_memory_mb: 64, max_requests: 5 }}
        cost: {{ max_model_calls: 2, max_tokens: 1000 }}
        output: {{ retain_raw: false }}
        cleanup: {{ required: true }}
        steps:
          - kind: prompt
            input: "Summarize the checkout policy."
        """)


def test_two_runs_of_one_probe_ask_the_model_twice(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """*Fails when* a probe stops re-asking the model it is meant to be watching.

    *Matters because* the seam deduplicates by `idempotency_key`, and a replayed
    `AgentResult` carries **no output** by design (contracts §12: the annex holds
    an `output_ref`, never the text). A key that was constant across runs
    therefore made every rerun record an empty observation -- which reads as
    drift against its own baseline, forever, for the one system class this build
    exists for. *No other instrument catches it because* the runner's other tests
    pass an agent that answers whatever it is asked; only the key itself shows
    the difference.
    """
    add_probe(s4_store, s4_scope, parse_probe(_prompt_probe(server)))
    agent = _RecordingAgent()

    first = _run_stored(s4_store, s4_scope, server, agent=agent)
    second = _run_stored(s4_store, s4_scope, server, agent=agent)

    assert len(agent.keys) == 2, "the second run never reached the model at all"
    assert agent.keys[0] != agent.keys[1], (
        "both runs sent one idempotency key, so the seam would replay the first "
        "and the second run would record nothing"
    )
    # And what each run recorded is the real reply, not an empty replay.
    assert first.steps[0].output == second.steps[0].output
    assert "unchanged" in first.steps[0].output


def test_a_rerun_with_a_baseline_does_not_read_as_drift_on_a_prompt_step(
    s4_store: SqliteStoreHandle, s4_scope: Scope, server: str
) -> None:
    """The symptom the test above prevents, asserted where a reader would meet it.

    A model answering identically twice must compare `unchanged`. Without the
    key fix this went `drift` on every rerun with nothing having changed.
    """
    add_probe(s4_store, s4_scope, parse_probe(_prompt_probe(server)))
    agent = _RecordingAgent()
    _run_stored(s4_store, s4_scope, server, agent=agent)
    set_baselines(s4_store, s4_scope)

    rerun = _run_stored(
        s4_store, s4_scope, server, agent=agent, baseline=_current_baseline(s4_store)
    )

    assert rerun.outcome == ProbeOutcome.SUCCESS
    assert rerun.comparison is not None
    assert rerun.comparison.verdict == UNCHANGED
