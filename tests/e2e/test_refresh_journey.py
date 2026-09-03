"""Build 6's change matrix, end to end on real tree edits -- **invariant #6**.

v6.1 §4 R6's closed list gives this build two of the eight critical semantic
invariants, and both are here:

* **#6 classification matrix** -- each of the five classes produced by a *real
  tree edit* and asserted end to end, **including a comment/formatting-only edit
  that must not be SEMANTICS-CHANGED**;
* **#4 propagation correctness** -- a load-bearing change stales the bound item;
  a non-load-bearing or unrelated change does not.

**The tree is purpose-built and tiny, and that is a deliberate trade.** The
reference repositories are Build 1's instrument and cost minutes; this one costs
seconds, and what it must prove is different in kind. Build 1 asks "does the map
see what is really there", which only a real repository can answer. Build 6 asks
"when this exact thing changes, is it classified this exact way", which needs a
tree where every identity is known by name -- an edit whose *intended* class
cannot be stated in advance proves nothing about a classifier.

**Every edit below is a real edit to a real file**, made between two real
`adopt refresh` runs through the same CLI entry point the release binary
compiles. Nothing is planted in the store.
"""

import json
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

ENTRY_POINT = (
    Path(__file__).resolve().parents[2] / "packages" / "adopt-cli" / "src" / "adopt_cli" / "main.py"
)
ANSWERS = {"artifact_access": True, "deploy_signal": True, "safe_interaction": True}
SCOPE = "northwind/acme-erp/orders-api/prod"

#: Exit 4 is degraded-with-findings: the run worked and saw something. Named
#: here because a test asserting the integer would read as a magic number and
#: the distinction from exit 1 is the whole point of the code.
FOUND_CHANGES = 4

_APP = """from fastapi import FastAPI

app = FastAPI()


@app.post("/v1/orders")
def create_order() -> dict[str, str]:
    return {"status": "created"}


@app.post("/v1/refunds")
def create_refund() -> dict[str, str]:
    return {"status": "refunded"}
"""

#: `os.environ[...]` reads rather than bare assignments: the generic pack
#: extracts what the system will actually fail without, and a module-level
#: constant is not that. Picked by mapping the tree and reading the listing,
#: not by assuming -- an edit whose class cannot be stated in advance proves
#: nothing about a classifier.
_SETTINGS = """import os

DATABASE_URL = os.environ["DATABASE_URL"]
CACHE_TTL_SECONDS = int(os.environ["CACHE_TTL_SECONDS"])
"""

#: A second module, so the demo's comment-only edit lands in a file whose every
#: referent is otherwise untouched. In `main.py` the cosmetic edit would sit
#: beside two real ones and RENDER-ONLY -- which is a claim about a *referent*
#: whose file changed and whose meaning did not -- would have nothing to be true
#: of there.
_HEALTH = """from fastapi import APIRouter

app = APIRouter()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
"""

#: A well-known file, so the tree carries one **path-derived** identity. The
#: move case needs one: an endpoint's key is its method and path, so moving the
#: file it lives in changes nothing about it -- which is correct, and useless as
#: a demonstration that a relocated referent keeps its history.
_DOCKERFILE = """FROM python:3.12-slim
COPY . /app
CMD ["python", "-m", "app.main"]
"""


def _run(*argv: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRY_POINT), *argv],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _payload(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        parsed: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError:  # pragma: no cover -- only on a broken envelope
        pytest.fail(f"stdout was not the JSON envelope:\n{completed.stdout[:2000]}")
    return parsed


def _classes(payload: dict[str, Any]) -> dict[str, str]:
    """`uri -> class` for every actionable change in one refresh payload."""
    return {entry["uri"]: entry["class"] for entry in payload["changes"]}


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small web+ai tree whose every identity we can name."""
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "deploy").mkdir()
    (root / "app" / "main.py").write_text(_APP, encoding="utf-8")
    (root / "app" / "settings.py").write_text(_SETTINGS, encoding="utf-8")
    (root / "app" / "health.py").write_text(_HEALTH, encoding="utf-8")
    (root / "deploy" / "Dockerfile").write_text(_DOCKERFILE, encoding="utf-8")
    return root


@pytest.fixture
def mapped(tree: Path, tmp_path: Path) -> dict[str, Any]:
    """`init` + a first `map`, so a refresh has a baseline to compare against."""
    store = tmp_path / "store.db"
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")

    init = _run(
        "init",
        str(tree),
        "--scope",
        SCOPE,
        "--answers",
        str(answers),
        "--archetype",
        "web",
        "--store",
        str(store),
        "--json",
        cwd=tmp_path,
    )
    assert init.returncode == ExitCode.SUCCESS, init.stderr

    mapped_run = _run("map", str(tree), "--store", str(store), "--json", cwd=tmp_path)
    assert mapped_run.returncode == ExitCode.SUCCESS, mapped_run.stderr

    # A first refresh with no edits: establishes the file snapshot, so the
    # RENDER-ONLY comparison below has a baseline. It also proves the honest
    # degradation -- the very first run cannot report cosmetic change.
    first = _run("refresh", str(tree), "--store", str(store), "--json", cwd=tmp_path)
    return {"store": store, "tmp": tmp_path, "tree": tree, "first_refresh": first}


# -- the clean run ----------------------------------------------------------


@pytest.mark.e2e
def test_a_refresh_with_no_edits_writes_nothing_and_exits_zero(mapped: dict[str, Any]) -> None:
    """**Idempotence, the promise every other assertion rests on.**

    *Fails when* a refresh manufactures changes from an unchanged tree. *Matters
    because* a queue that fills up on its own is a queue people stop opening,
    and every class below would be indistinguishable from noise. *No other
    instrument catches it* because each classification test seeds an edit and
    would pass even if the diff reported everything always."""
    clean = _run(
        "refresh", str(mapped["tree"]), "--store", str(mapped["store"]), "--json", cwd=mapped["tmp"]
    )

    assert clean.returncode == ExitCode.SUCCESS, clean.stderr
    payload = _payload(clean)
    assert payload["changes"] == []
    assert payload["written"]["change_event"] is None
    assert payload["written"]["classifications"] == 0
    assert payload["review"]["batch"] is None


@pytest.mark.e2e
def test_the_first_refresh_reports_render_only_as_unavailable(mapped: dict[str, Any]) -> None:
    """*Fails when* a store with no snapshot reports zero cosmetic changes.
    *Matters because* "we could not tell" printed as "nothing changed" is a
    measurement nobody took -- CR-67's `0/0 covered (100%)` in a new costume."""
    payload = _payload(mapped["first_refresh"])

    assert payload["render_only"]["available"] is False
    assert payload["render_only"]["paths"] == []
    assert payload["render_only"]["referents"] == []


# -- invariant #6: the five classes from real edits -------------------------


@pytest.mark.e2e
def test_a_comment_only_edit_is_render_only_and_never_semantics_changed(
    mapped: dict[str, Any],
) -> None:
    """**Invariant #6's negative half, and the reason H5 exists.**

    *Fails when* a cosmetic edit stales bound knowledge. *Matters because* false
    staleness is the failure that makes an FDE abandon the review queue, and
    comment edits are the commonest edits there are -- a digest over raw bytes
    would stale the whole file's identities on every one of them. *No other
    instrument catches it* because the file genuinely changed: only the
    attribute digest knows the meaning did not."""
    main = mapped["tree"] / "app" / "main.py"
    main.write_text(
        _APP.replace(
            "app = FastAPI()",
            "app = FastAPI()  # the order service's public surface\n\n# TODO: pagination",
        ),
        encoding="utf-8",
    )

    refreshed = _run(
        "refresh", str(mapped["tree"]), "--store", str(mapped["store"]), "--json", cwd=mapped["tmp"]
    )

    payload = _payload(refreshed)
    assert "BINDING_INTACT_SEMANTICS_CHANGED" not in _classes(payload).values(), (
        "a comment-only edit was classified as a semantic change"
    )
    assert payload["changes"] == [], "a comment-only edit produced an actionable change"
    assert payload["render_only"]["available"] is True
    # Per referent, because the class says *this binding survived the edit* --
    # and naming the file too, because that is what the reader just changed.
    assert "app/main.py" in payload["render_only"]["paths"]
    assert all(entry["path"] == "app/main.py" for entry in payload["render_only"]["referents"]), (
        payload["render_only"]
    )


@pytest.mark.e2e
def test_a_removed_endpoint_is_dead(mapped: dict[str, Any]) -> None:
    """*Fails when* a deleted referent stops being reported. *Matters because*
    knowledge bound to a deleted endpoint is exactly the rot the product exists
    to catch."""
    main = mapped["tree"] / "app" / "main.py"
    main.write_text(_APP.split('@app.post("/v1/refunds")')[0].rstrip() + "\n", encoding="utf-8")

    refreshed = _run(
        "refresh", str(mapped["tree"]), "--store", str(mapped["store"]), "--json", cwd=mapped["tmp"]
    )

    assert refreshed.returncode == FOUND_CHANGES, refreshed.stderr
    classes = _classes(_payload(refreshed))
    dead = [uri for uri, name in classes.items() if name == "BINDING_DEAD"]
    assert any("refunds" in uri for uri in dead), classes


@pytest.mark.e2e
def test_a_new_endpoint_parameter_is_semantics_changed(mapped: dict[str, Any]) -> None:
    """*Fails when* a real attribute change goes unnoticed. *Matters because*
    this is the build's whole reason to exist -- a change to a mapped referent
    that nothing else in the system would report.

    The edit adds a parameter to an existing endpoint, which changes what the
    endpoint **is** (method + path + parameter names are its digest) while
    leaving its key -- method + path -- untouched. That combination is precisely
    what SEMANTICS-CHANGED means, and it is why the class is spelled
    `BINDING_INTACT_...`: the URI still resolves, so every binding survives, and
    what a reviewer must decide is whether the note about it is still true.
    """
    main = mapped["tree"] / "app" / "main.py"
    main.write_text(
        _APP.replace(
            "def create_order() -> dict[str, str]:",
            "def create_order(idempotency_key: str) -> dict[str, str]:",
        ),
        encoding="utf-8",
    )

    refreshed = _run(
        "refresh", str(mapped["tree"]), "--store", str(mapped["store"]), "--json", cwd=mapped["tmp"]
    )

    assert refreshed.returncode == FOUND_CHANGES, refreshed.stderr
    classes = _classes(_payload(refreshed))
    changed = [uri for uri, name in classes.items() if name == "BINDING_INTACT_SEMANTICS_CHANGED"]
    assert any("orders" in uri for uri in changed), classes
    # The key did not change, so the identity must not have been reported dead.
    assert "BINDING_DEAD" not in classes.values(), classes


@pytest.mark.e2e
def test_a_new_config_key_is_unbound_new(mapped: dict[str, Any]) -> None:
    """*Fails when* a newly added referent is silent. *Matters because*
    UNBOUND_NEW is what opens a coverage gap: an undocumented key is invisible
    until the map says it exists."""
    settings = mapped["tree"] / "app" / "settings.py"
    settings.write_text(
        _SETTINGS + 'FEATURE_FLAG_URL = os.environ["FEATURE_FLAG_URL"]\n', encoding="utf-8"
    )

    refreshed = _run(
        "refresh", str(mapped["tree"]), "--store", str(mapped["store"]), "--json", cwd=mapped["tmp"]
    )

    assert refreshed.returncode == FOUND_CHANGES, refreshed.stderr
    classes = _classes(_payload(refreshed))
    assert "UNBOUND_NEW" in classes.values(), classes


@pytest.mark.e2e
def test_a_moved_file_is_moved_not_dead_plus_new(mapped: dict[str, Any]) -> None:
    """*Fails when* a relocated referent is reported as a death and an arrival.
    *Matters because* every binding, probe and note attached to it would follow
    the death -- the orphaning the identity model exists to prevent -- and the
    reviewer would be asked twice about one event, with the second answer
    contradicting the first.

    The Dockerfile is the subject because its key **is** its path: moving it
    changes its URI, which is the only situation in which an alias has anything
    to do. Moving a file that holds an endpoint would demonstrate nothing -- the
    endpoint's key is its method and path, so its URI never moved."""
    moved_dir = mapped["tree"] / "infra"
    moved_dir.mkdir()
    (mapped["tree"] / "deploy" / "Dockerfile").rename(moved_dir / "Dockerfile")

    refreshed = _run(
        "refresh", str(mapped["tree"]), "--store", str(mapped["store"]), "--json", cwd=mapped["tmp"]
    )

    assert refreshed.returncode == FOUND_CHANGES, refreshed.stderr
    classes = _classes(_payload(refreshed))
    moved = [uri for uri, name in classes.items() if name == "BINDING_MOVED"]
    assert moved, classes
    assert all("Dockerfile" in uri for uri in moved), classes
    # The relocation is one event, not a death plus an arrival.
    assert "BINDING_DEAD" not in classes.values(), classes
    assert "UNBOUND_NEW" not in classes.values(), classes


# -- the instrument fence ---------------------------------------------------


# -- invariant #4 and the partial journey: refresh -> review -> ask STALE ----


@pytest.fixture
def with_knowledge(mapped: dict[str, Any]) -> dict[str, Any]:
    """A note bound load-bearingly to the orders endpoint, and one bound to nothing.

    Written through `adopt ingest` + `adopt bind` -- the real verbs -- so the
    bindings under test are the ones Build 2 creates rather than rows a test
    invented.
    """
    docs = mapped["tree"] / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "orders.md").write_text(
        "# Creating orders\n\nOrders are created by the orders endpoint.\n", encoding="utf-8"
    )
    (docs / "styleguide.md").write_text(
        "# Style guide\n\nWe name things in snake case.\n", encoding="utf-8"
    )
    common = ("--store", str(mapped["store"]), "--json")
    ingest = _run("ingest", str(docs), *common, cwd=mapped["tmp"])
    assert ingest.returncode == ExitCode.SUCCESS, ingest.stderr
    # Item ids come from ingest's own payload rather than from a title search:
    # the id is what `adopt bind` takes, and a test that guessed it would be
    # asserting against a row it hoped existed.
    items = {Path(entry["path"]).name: entry["item"] for entry in _payload(ingest)["ingested"]}

    listing = _payload(_run("map", str(mapped["tree"]), *common, "--report", cwd=mapped["tmp"]))
    # Matched on the **encoded key**, not on `"orders"`: the scope segment of
    # every URI in this tree is `.../orders-api/...`, so a loose substring picks
    # whichever endpoint the listing happens to return first. It did the right
    # thing while `main.py` held the only endpoints; adding `health.py` for the
    # demo's cosmetic edit is what made the looseness visible.
    orders = [
        row["uri"]
        for row in listing["listing"]
        if "%2Fv1%2Forders" in row["uri"] and row["kind"] == "endpoint"
    ]
    assert len(orders) == 1, orders
    orders_uri = orders[0]
    return {**mapped, "orders_uri": orders_uri, "docs": docs, "items": items}


@pytest.mark.e2e
def test_a_load_bearing_change_stales_its_item_and_ask_says_why(
    with_knowledge: dict[str, Any],
) -> None:
    """**Invariant #4 (positive) and the partial journey, in one sequence.**

    *Fails when* a semantic change to a bound referent leaves its note serving
    as current. *Matters because* that is the rot the whole product exists to
    delete: an FDE asks a question and gets an answer that was true last month.
    *No other instrument catches it* because the note, the binding and the
    identity are all individually intact -- only the relationship went stale.

    The `ask` line is v6.1 §6's third demo line: *"answers STALE, naming the
    change"*. It is asserted through the CLI rather than against the store,
    because "the state is stale" and "the operator is told it is stale" are
    different claims and only the second is the product.
    """
    common = ("--store", str(with_knowledge["store"]), "--json")
    # Bind the note to the endpoint it describes, load-bearing.
    bound = _run(
        "bind",
        with_knowledge["items"]["orders.md"],
        with_knowledge["orders_uri"],
        *common,
        cwd=with_knowledge["tmp"],
    )
    assert bound.returncode == ExitCode.SUCCESS, bound.stderr

    # A real semantic edit to the bound referent.
    main = with_knowledge["tree"] / "app" / "main.py"
    main.write_text(
        _APP.replace(
            "def create_order() -> dict[str, str]:",
            "def create_order(idempotency_key: str) -> dict[str, str]:",
        ),
        encoding="utf-8",
    )

    refreshed = _run(
        "refresh",
        str(with_knowledge["tree"]),
        *common,
        cwd=with_knowledge["tmp"],
    )
    assert refreshed.returncode == FOUND_CHANGES, refreshed.stderr
    payload = _payload(refreshed)

    # Propagation happened, and the queue names the item and its cause.
    assert payload["written"]["bindings_staled"] >= 1, payload["written"]
    assert payload["review"]["items"] >= 1, payload["review"]
    assert payload["review"]["queued"][0]["causes"], payload["review"]

    # `adopt review` shows the population with its cause.
    queue = _payload(_run("review", *common, cwd=with_knowledge["tmp"]))
    assert any(entry["source"] == "refresh" for entry in queue["queue"]), queue
    assert any(cause["class"] == "BINDING_INTACT_SEMANTICS_CHANGED" for cause in queue["causes"]), (
        queue
    )

    # And the answer is served STALE with the deciding rule named.
    answered = _payload(_run("ask", "How are orders created?", *common, cwd=with_knowledge["tmp"]))
    assert answered["branch"] == "stale", answered
    # The rule that decided it, named -- v6.1's "answers STALE, naming the
    # change". A branch without its cause is a verdict without a reason.
    assert answered["cause"] == "load_bearing_binding_stale", answered


@pytest.mark.e2e
def test_an_unrelated_item_is_not_staled(with_knowledge: dict[str, Any]) -> None:
    """**Invariant #4's negative case.**

    *Fails when* a refresh stales knowledge it has no reason to touch. *Matters
    because* mass false staleness is indistinguishable to a reviewer from real
    staleness, and it is the failure that makes people stop believing the
    freshness state at all -- the same corrosion H5 prevents on the digest side.
    """
    common = ("--store", str(with_knowledge["store"]), "--json")
    main = with_knowledge["tree"] / "app" / "main.py"
    main.write_text(
        _APP.replace(
            "def create_order() -> dict[str, str]:",
            "def create_order(idempotency_key: str) -> dict[str, str]:",
        ),
        encoding="utf-8",
    )

    refreshed = _run("refresh", str(with_knowledge["tree"]), *common, cwd=with_knowledge["tmp"])
    payload = _payload(refreshed)

    # The style guide is bound to nothing, so nothing about it may be staled or
    # queued: the only queued items are those with a load-bearing binding.
    queued_items = {entry["item_id"] for entry in payload["review"]["queued"]}
    assert with_knowledge["items"]["styleguide.md"] not in queued_items, payload["review"]


@pytest.mark.e2e
def test_the_run_reports_what_it_did_not_judge(mapped: dict[str, Any]) -> None:
    """*Fails when* exemptions and re-baselines stop being rendered. *Matters
    because* both are the run saying "I did not look at this": a report showing
    only findings would make a run that examined nothing look clean."""
    payload = _payload(mapped["first_refresh"])

    assert "exempt" in payload
    assert "rebaselined" in payload
    assert payload["probes"]["ran"] is False


# -- the probe half: a system that changes with no commit -------------------
#
# The artifact half above asks "did the repository change?". This half asks the
# question a repository cannot answer: **the client's system changed and nothing
# of ours did**. No file was edited, no commit exists -- the provider simply
# answers differently -- and the knowledge bound to what the probe exercises has
# to stale exactly as if a file had changed, through the same mechanism, into
# the same review batch.

_STEADY: dict[str, Any] = {"order_id": "ord_1", "status": "accepted"}
#: The same endpoint, behaving differently. This is the whole premise of the
#: Behaviour Baseline: nothing in the repository moved.
_CHANGED: dict[str, Any] = {"order_id": "ord_1", "status": "held for manual review"}


class _Handler(BaseHTTPRequestHandler):
    payload: ClassVar[dict[str, Any]] = dict(_STEADY)

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
    _Handler.payload = dict(_STEADY)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    # `poll_interval` is what `shutdown()` waits on; the 0.5s default is paid on
    # every teardown. Shortening it is not a sleep -- nothing asserts on it.
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


def _probe_file(host: str, uri: str, *, note: str = "") -> str:
    """One http step, one exercised identity. No prompt step, deliberately.

    A `prompt` step would drag the agent seam and a recorded adapter into a test
    whose subject is the cascade, and would give the comparison a second thing
    that could vary. `exercises` is the only probe -> identity link there is, so
    it is what this file exists to carry.
    """
    return textwrap.dedent(f"""\
        probe_id: checkout-happy-path
        safe_path: sandbox
        network: {{ deny_by_default: true, allow: ["{host}"] }}
        http_methods: {{ allow: [GET] }}
        side_effect_policy: prohibited
        runtime: {{ max_seconds: 30, max_memory_mb: 256, max_requests: 10 }}
        cost: {{ max_model_calls: 0, max_tokens: 100 }}
        output: {{ retain_raw: false, redaction_policy: pii-default }}
        cleanup: {{ required: true }}
        exercises: ["{uri}"]
        diff_method: exact
        steps:
          - kind: http
            method: GET
            url: "http://{host}/v1/checkout"{note}
            expect: {{ status: 200, json_fields: ["order_id"] }}
        """)


def _sql(store: Path, query: str, *args: object) -> list[tuple[Any, ...]]:
    """Read the store directly, so one bug cannot both write a row and vouch for it."""
    with sqlite3.connect(store) as connection:
        return list(connection.execute(query, args).fetchall())


@pytest.fixture
def with_probe(with_knowledge: dict[str, Any], system: str) -> dict[str, Any]:
    """A probe exercising the bound orders endpoint, run once and baselined."""
    common = ("--store", str(with_knowledge["store"]), "--json")
    probes = with_knowledge["tree"] / "probes"
    probes.mkdir(exist_ok=True)
    probe_path = probes / "checkout.yaml"
    probe_path.write_text(_probe_file(system, with_knowledge["orders_uri"]), encoding="utf-8")

    # Bind the note load-bearingly to the referent the probe exercises, so a
    # drift has somewhere to propagate.
    bound = _run(
        "bind",
        with_knowledge["items"]["orders.md"],
        with_knowledge["orders_uri"],
        *common,
        cwd=with_knowledge["tmp"],
    )
    assert bound.returncode == ExitCode.SUCCESS, bound.stderr

    added = _run("probe", "add", str(probe_path), *common, cwd=with_knowledge["tmp"])
    assert added.returncode == ExitCode.SUCCESS, added.stderr
    ran = _run("probe", "run", "--all", *common, cwd=with_knowledge["tmp"])
    assert ran.returncode == ExitCode.SUCCESS, ran.stderr
    baselined = _run("probe", "baseline", "--set", *common, cwd=with_knowledge["tmp"])
    assert baselined.returncode == ExitCode.SUCCESS, baselined.stderr

    return {**with_knowledge, "probe_path": probe_path, "system": system, "common": common}


@pytest.mark.e2e
def test_probe_drift_becomes_a_provider_change_event_and_stales_the_bound_note(
    with_probe: dict[str, Any],
) -> None:
    """**The build's other half of "what changed", and the half a repository
    cannot see.**

    *Fails when* a drifted probe leaves the knowledge bound to what it exercises
    reading as current. *Matters because* an FDE's notes about a checkout flow
    are wrong the moment the flow changes, and no commit will ever tell them so
    -- this is the only sensor in the free product that watches the running
    system. *No other instrument catches it because* the artifact half reports a
    perfectly clean run: nothing in the tree moved, and it is right about that.
    """
    _Handler.payload = dict(_CHANGED)

    refreshed = _run(
        "refresh", str(with_probe["tree"]), *with_probe["common"], cwd=with_probe["tmp"]
    )

    assert refreshed.returncode == FOUND_CHANGES, refreshed.stderr
    payload = _payload(refreshed)
    assert payload["probes"]["ran"] is True, payload["probes"]
    assert payload["probes"]["drifted"] == ["checkout-happy-path"], payload["probes"]
    assert with_probe["orders_uri"] in payload["probes"]["referents"], payload["probes"]

    # A **`provider`** event, separate from the artifact one: "what did the
    # probes see this run" has to be answerable from the store, and the artifact
    # half of this very run found nothing.
    assert payload["changes"] == [], "the tree did not change; the system did"
    event_id = payload["probes"]["change_event"]
    assert event_id is not None
    sources = _sql(with_probe["store"], "SELECT source FROM change_event WHERE id = ?", event_id)
    assert sources == [("provider",)], sources
    classified = _sql(
        with_probe["store"],
        "SELECT class, decided_by FROM classification WHERE change_event_id = ?",
        event_id,
    )
    assert classified == [("BINDING_INTACT_SEMANTICS_CHANGED", "cascade_step_3")], classified

    # One batch for the run, and `adopt ask` says STALE through the same rule an
    # edited file produces -- one freshness mechanism everywhere.
    assert payload["review"]["items"] >= 1, payload["review"]
    answered = _payload(
        _run("ask", "How are orders created?", *with_probe["common"], cwd=with_probe["tmp"])
    )
    assert answered["branch"] == "stale", answered
    assert answered["cause"] == "load_bearing_binding_stale", answered


@pytest.mark.e2e
def test_an_edited_probe_is_probe_changed_and_never_a_change_event(
    with_probe: dict[str, Any],
) -> None:
    """**The rule that makes the probe half trustworthy at all.**

    *Fails when* editing our own probe file is reported as a change in the
    client's system. *Matters because* it is the one mistake that would train an
    FDE to ignore both `adopt probe diff` and `adopt refresh`: every probe
    improvement would stale a pile of knowledge, and the noise would be
    indistinguishable from the signal. *No other instrument catches it because*
    the system really did change too -- the assertion below only means something
    because the payload would otherwise be a drift.
    """
    # Both at once: the probe is edited **and** the system changes. Without the
    # revision fence this is a drift; with it, it is a probe we edited.
    _Handler.payload = dict(_CHANGED)
    with_probe["probe_path"].write_text(
        _probe_file(with_probe["system"], with_probe["orders_uri"], note="  # widened"),
        encoding="utf-8",
    )
    revised = _run(
        "probe", "add", str(with_probe["probe_path"]), *with_probe["common"], cwd=with_probe["tmp"]
    )
    assert revised.returncode == ExitCode.SUCCESS, revised.stderr
    assert _payload(revised)["outcome"] == "revised", _payload(revised)

    refreshed = _run(
        "refresh", str(with_probe["tree"]), *with_probe["common"], cwd=with_probe["tmp"]
    )

    payload = _payload(refreshed)
    assert refreshed.returncode == ExitCode.SUCCESS, refreshed.stderr
    assert payload["probes"]["ran"] is True, payload["probes"]
    assert payload["probes"]["drifted"] == [], payload["probes"]
    assert payload["probes"]["change_event"] is None, payload["probes"]
    # And the store holds no `provider` event for the run at all.
    assert _sql(with_probe["store"], "SELECT id FROM change_event WHERE source = 'provider'") == []

    # **The verdict, named.** Without this the test would also pass if the
    # fixture change had never reached the server -- "no drift because nothing
    # changed" and "no drift because we changed the probe" are the same empty
    # list, and only one of them is what this test is about.
    diffed = _payload(_probe_diff(with_probe))
    verdicts = {row["probe"]: row["verdict"] for row in diffed["comparisons"]}
    assert verdicts["checkout-happy-path"] == "probe_changed", verdicts


def _probe_diff(with_probe: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    """`adopt probe diff`, which reports the verdict refresh acted on."""
    return _run("probe", "diff", *with_probe["common"], cwd=with_probe["tmp"])


@pytest.mark.e2e
def test_no_probes_skips_the_probe_half_and_says_so(with_probe: dict[str, Any]) -> None:
    """*Fails when* `--no-probes` quietly runs them anyway, or reports a skipped
    half as a clean one. *Matters because* the flag exists so a refresh in CI, or
    against a system whose sandbox is down, can still answer the artifact
    question -- and a run that opened sockets an operator asked it not to open is
    the one thing a safe-execution product may never do."""
    _Handler.payload = dict(_CHANGED)

    refreshed = _run(
        "refresh",
        str(with_probe["tree"]),
        "--no-probes",
        *with_probe["common"],
        cwd=with_probe["tmp"],
    )

    assert refreshed.returncode == ExitCode.SUCCESS, refreshed.stderr
    payload = _payload(refreshed)
    assert payload["probes"]["ran"] is False, payload["probes"]
    assert payload["probes"]["reason"] == "--no-probes", payload["probes"]
    assert payload["probes"]["change_event"] is None, payload["probes"]


@pytest.mark.e2e
def test_a_probe_with_no_baseline_is_reported_not_silently_ignored(
    with_knowledge: dict[str, Any], system: str
) -> None:
    """*Fails when* an unbaselined probe makes a refresh look like it checked the
    system. *Matters because* "we have never recorded how this behaves" and
    "this behaves as before" are different answers, and printing the second for
    the first is the measurement-nobody-took failure the render-only half
    already refuses to make."""
    common = ("--store", str(with_knowledge["store"]), "--json")
    probes = with_knowledge["tree"] / "probes"
    probes.mkdir(exist_ok=True)
    path = probes / "checkout.yaml"
    path.write_text(_probe_file(system, with_knowledge["orders_uri"]), encoding="utf-8")
    added = _run("probe", "add", str(path), *common, cwd=with_knowledge["tmp"])
    assert added.returncode == ExitCode.SUCCESS, added.stderr

    refreshed = _run("refresh", str(with_knowledge["tree"]), *common, cwd=with_knowledge["tmp"])

    payload = _payload(refreshed)
    assert payload["probes"]["ran"] is True, payload["probes"]
    reasons = [entry["reason"] for entry in payload["probes"]["skipped"]]
    assert any("baseline" in reason for reason in reasons), payload["probes"]
    assert payload["probes"]["change_event"] is None, payload["probes"]


# -- the Build Definition of Done: v6.1 §6's demo, verbatim -----------------


@pytest.fixture
def demo(mapped: dict[str, Any]) -> dict[str, Any]:
    """Three notes, each bound load-bearingly to a referent about to change.

    One per action the demo's last line offers, because an action with nothing
    to act on proves only that the flag parses. Written through `adopt ingest`
    and `adopt bind` -- the real verbs -- so the bindings under test are Build
    2's rather than rows this file invented.
    """
    common = ("--store", str(mapped["store"]), "--json")
    docs = mapped["tree"] / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "orders.md").write_text(
        "# Creating orders\n\nOrders are created by the orders endpoint.\n", encoding="utf-8"
    )
    (docs / "refunds.md").write_text(
        "# Issuing refunds\n\nRefunds are issued by the refunds endpoint.\n", encoding="utf-8"
    )
    (docs / "deploy.md").write_text(
        "# Deploying\n\nThe service ships from the Dockerfile in this repository.\n",
        encoding="utf-8",
    )
    ingest = _run("ingest", str(docs), *common, cwd=mapped["tmp"])
    assert ingest.returncode == ExitCode.SUCCESS, ingest.stderr
    items = {Path(entry["path"]).name: entry["item"] for entry in _payload(ingest)["ingested"]}

    listing = _payload(_run("map", str(mapped["tree"]), *common, "--report", cwd=mapped["tmp"]))
    uris = {row["uri"]: row for row in listing["listing"]}

    def uri_for(fragment: str, kind: str) -> str:
        """One identity by an **encoded key** fragment, not by a loose substring.

        The scope segment of every URI here is `.../orders-api/...`, so matching
        on `"orders"` matches the health endpoint and the Dockerfile too. A
        fixture that bound a note to whichever referent the listing returned
        first would make this test's failures unreadable and its passes
        accidental -- so the fragment is the percent-encoded key.
        """
        found = [uri for uri, row in uris.items() if fragment in uri and row["kind"] == kind]
        assert len(found) == 1, f"{fragment!r} matched {found}"
        return found[0]

    targets = {
        "orders.md": uri_for("%2Fv1%2Forders", "endpoint"),
        "refunds.md": uri_for("%2Fv1%2Frefunds", "endpoint"),
        "deploy.md": uri_for("Dockerfile", "metadata_component"),
    }
    for document, uri in targets.items():
        bound = _run("bind", items[document], uri, *common, cwd=mapped["tmp"])
        assert bound.returncode == ExitCode.SUCCESS, bound.stderr

    return {**mapped, "common": common, "items": items, "targets": targets}


@pytest.mark.e2e
def test_the_build_6_demo_runs_verbatim(demo: dict[str, Any]) -> None:
    """**The Build Definition of Done, run as v6.1 §6 writes it.**

    *Fails when* any line of the demo stops working end to end: real edits stop
    becoming classified change events, the queue stops naming all four classes,
    a cosmetic edit starts manufacturing one, `adopt ask` stops saying STALE and
    why, an action stops having a store consequence, or a resolved item fails to
    come back into service. *Matters because* v6.1 §4 R1 requires every build to
    end in a verb an FDE runs on a real engagement and gets value from that day,
    and this sequence **is** that day. *No other instrument catches it because*
    every other test in this file proves one class or one action in isolation;
    only this one proves that a single refresh run holds all of them at once and
    that the review session it opens can actually be worked to completion.
    """
    tree, common, tmp = demo["tree"], demo["common"], demo["tmp"]

    # ---- the edits, all real, all at once -------------------------------
    # 1. rename an endpoint: the old one dies, the new one arrives
    # 2. change what an endpoint *is*, without changing its key
    renamed = _APP.replace("/v1/refunds", "/v1/reimbursements").replace(
        "def create_order() -> dict[str, str]:",
        "def create_order(idempotency_key: str) -> dict[str, str]:",
    )
    (tree / "app" / "main.py").write_text(renamed, encoding="utf-8")
    # 3. remove a config key
    (tree / "app" / "settings.py").write_text(
        _SETTINGS.replace('CACHE_TTL_SECONDS = int(os.environ["CACHE_TTL_SECONDS"])\n', ""),
        encoding="utf-8",
    )
    # 4. move a mapped file
    (tree / "infra").mkdir()
    (tree / "deploy" / "Dockerfile").rename(tree / "infra" / "Dockerfile")
    # 5. AND a comment/formatting-only edit to a mapped file
    (tree / "app" / "health.py").write_text(
        _HEALTH.replace("app = APIRouter()", "app = APIRouter()  # liveness only, no auth"),
        encoding="utf-8",
    )

    # ---- adopt refresh --------------------------------------------------
    refreshed = _run("refresh", str(tree), *common, cwd=tmp)
    assert refreshed.returncode == FOUND_CHANGES, refreshed.stderr
    payload = _payload(refreshed)
    classes = set(_classes(payload).values())
    assert {
        "BINDING_DEAD",
        "BINDING_MOVED",
        "BINDING_INTACT_SEMANTICS_CHANGED",
        "UNBOUND_NEW",
    } <= classes, _classes(payload)
    # The comment-only edit is in the report as cosmetic and **nowhere** in the
    # actionable list: H5's promise, asserted on the run that also carries four
    # real changes, because a digest over raw bytes would pass every other test
    # in this file and fail here.
    assert "app/health.py" in payload["render_only"]["paths"], payload["render_only"]
    assert not any(
        entry["evidence"].startswith("attribute digest") and "health" in entry["uri"]
        for entry in payload["changes"]
    ), payload["changes"]

    # ---- adopt review ---------------------------------------------------
    queue = _payload(_run("review", *common, cwd=tmp))
    queued_classes = {cause["class"] for cause in queue["causes"]}
    assert {
        "BINDING_DEAD",
        "BINDING_MOVED",
        "BINDING_INTACT_SEMANTICS_CHANGED",
    } == queued_classes, queue["causes"]
    # NEW has no knowledge to queue against, so it renders informationally and
    # its disposition path is `adopt gaps` -- nothing is silent, and nothing is
    # asked of a reviewer that they cannot answer (plan decision D6).
    informational = {entry["class"] for entry in queue["informational"]}
    assert "UNBOUND_NEW" in informational, queue["informational"]
    assert "BINDING_INTACT_RENDER_ONLY" in informational, queue["informational"]

    # ---- adopt ask "<question about the changed area>" -------------------
    answered = _payload(_run("ask", "How are orders created?", *common, cwd=tmp))
    assert answered["branch"] == "stale", answered
    assert answered["cause"] == "load_bearing_binding_stale", answered

    # ---- adopt review --resolve <item> --action ... ---------------------
    by_item = {entry["item_id"]: entry["review_item"] for entry in _review_entries(queue)}
    confirmed = _resolve(demo, by_item[demo["items"]["orders.md"]], "confirm-current")
    assert confirmed["resolution"] == "confirmed", confirmed
    assert confirmed["freshened_bindings"], confirmed
    assert confirmed["still_stale"] == [], confirmed

    retired = _resolve(demo, by_item[demo["items"]["refunds.md"]], "retire")
    assert retired["resolution"] == "corrected", retired
    assert retired["revision"] is not None, retired

    rebound = _resolve(demo, by_item[demo["items"]["deploy.md"]], "rebind")
    assert rebound["resolution"] == "corrected", rebound
    assert rebound["superseded_bindings"], rebound
    assert rebound["new_binding"] is not None, rebound
    # The default target came from the alias the map recorded, not from a flag.
    assert "infra/Dockerfile" in (rebound["rebound_to"] or ""), rebound

    # ---- and the consequences hold --------------------------------------
    served = _payload(_run("ask", "How are orders created?", *common, cwd=tmp))
    assert served["branch"] == "known", served
    assert _freshness(demo, demo["items"]["refunds.md"])["state"] == "retired"
    assert _freshness(demo, demo["items"]["deploy.md"])["state"] != "stale"

    # ---- a refresh with nothing left to find is clean --------------------
    clean = _run("refresh", str(tree), *common, cwd=tmp)
    assert clean.returncode == ExitCode.SUCCESS, clean.stderr
    assert _payload(clean)["written"]["change_event"] is None


def _review_entries(queue: dict[str, Any]) -> list[dict[str, Any]]:
    """`review_item` rows of the refresh population, with the item each is about.

    The queue payload lists causes by review item and the queue by review item;
    the join back to a knowledge item is what a test needs to say "resolve *the
    orders note*" rather than "resolve whatever came first".
    """
    causes = {cause["review_item"] for cause in queue["causes"]}
    return [
        {"review_item": entry["review_item"], "item_id": entry["item"]}
        for entry in queue["queue"]
        if entry["source"] == "refresh" and entry["review_item"] in causes
    ]


def _resolve(demo: dict[str, Any], review_item: str, action: str) -> dict[str, Any]:
    """One `adopt review --resolve --action`, returning what it did."""
    done = _run(
        "review", "--resolve", review_item, "--action", action, *demo["common"], cwd=demo["tmp"]
    )
    assert done.returncode == ExitCode.SUCCESS, done.stderr
    resolution: dict[str, Any] = _payload(done)["resolutions"][0]
    return resolution


def _freshness(demo: dict[str, Any], item_id: str) -> dict[str, Any]:
    """`adopt freshness resolve` for one item -- the state and the rule."""
    done = _run("freshness", "resolve", "--item", item_id, *demo["common"], cwd=demo["tmp"])
    assert done.returncode == ExitCode.SUCCESS, done.stderr
    return _payload(done)
