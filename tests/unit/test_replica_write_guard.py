"""No canon-writing verb may write into a store `adopt pull` maintains.

*Fails when* a verb that writes canon reaches a replica's store file. *Matters
because* the next `adopt pull` replaces that file wholesale: the write is not
merely misplaced, it is gone with no trace it ever existed, and R9 makes the
plane the sole writer of an operated system's canon in the first place. *No
other instrument catches it because* the write **succeeds** -- every existing
test of every one of these verbs asserts a row landed, and a row does land; only
the file it lands in is doomed.

**Two verbs remembered and eleven did not.** `refresh` and `handover` each
carried their own copy of the check (`REFRESH_TARGET_IS_REPLICA`,
`HANDOVER_TARGET_IS_REPLICA`); `init`, `map`, `ingest`, `harvest`, `bind`,
`gaps`, `review`, `answer`, `draft`, `pack --draft-missing`, `probe
add/run/baseline` and `boundary --scope` did not. The guard now lives at the one
door all of them already come through, and the table below is the module
docstring of `adopt_cli.replica` in executable form.

**Every case is run twice**, once against a marked store and once against a
byte-identical unmarked one. The control is what makes the allowed rows mean
anything: a verb that failed for its own reasons on both stores would satisfy
"not refused" perfectly, and a refused verb has to be shown refusing *because of
the marker* rather than because the fixture was wrong.
"""

import datetime as _dt
import hashlib
import io
import json
import re
import subprocess
import sys
import textwrap
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pytest

from adopt_cli.main import main
from adopt_obs import ExitCode

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINT = REPO_ROOT / "packages" / "adopt-cli" / "src" / "adopt_cli" / "main.py"
CLI_SOURCE = REPO_ROOT / "packages" / "adopt-cli" / "src"

SCOPE = "northwind/acme-erp/orders-api/prod"
URI = f"onboard-v1://{SCOPE}/metadata_component/file/Dockerfile"

PROBE = textwrap.dedent(
    """\
    probe_id: guard-probe
    safe_path: sandbox
    network: { deny_by_default: true, allow: ["127.0.0.1:9"] }
    http_methods: { allow: [GET] }
    side_effect_policy: prohibited
    runtime: { max_seconds: 5, max_memory_mb: 64, max_requests: 1 }
    cost: { max_model_calls: 0, max_tokens: 100 }
    output: { retain_raw: false, redaction_policy: pii-default }
    cleanup: { required: true }
    diff_method: exact
    steps:
      - kind: http
        method: GET
        url: "http://127.0.0.1:9/health"
    """
)

#: `(id, argv)` for every verb the policy table refuses. Each argv is chosen to
#: reach the store open: an invocation the parser rejected first would pass a
#: refusal test while proving nothing. What proves it reached the guard is the
#: refusal itself -- only the guard produces a `*_REPLICA` code.
REFUSED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("init", ("init", "{root}", "--scope", SCOPE, "--answers", "{answers}", "--archetype", "web")),
    ("map", ("map", "{root}")),
    ("ingest", ("ingest", "{doc}")),
    ("harvest", ("harvest", "{root}", "--since", "HEAD")),
    ("bind", ("bind", "kn_01GUARD", URI)),
    ("gaps", ("gaps", "--ack", "some-gap-key")),
    ("review", ("review", "--confirm", "ri_01GUARD")),
    ("answer", ("answer", "esc_01GUARD", "--text", "an answer")),
    ("draft", ("draft", URI)),
    ("pack--draft-missing", ("pack", "--draft-missing", "--out", "{out}")),
    ("probe-add", ("probe", "add", "{probe}")),
    ("probe-run-all", ("probe", "run", "--all")),
    ("probe-baseline", ("probe", "baseline", "--set")),
    ("boundary--scope", ("boundary", "--scope", SCOPE, "--answers", "{answers}")),
    ("refresh", ("refresh", "--no-probes")),
    ("handover-start", ("handover", "start", "--receiving-owner", "someone")),
)

#: The other half of the table: verbs a replica must keep answering. Each is
#: asserted to exit **exactly as it does on an unmarked store**, whatever that
#: is -- several of these fail for their own unrelated reasons on a store this
#: bare, and requiring exit 0 would have meant contorting the fixture until the
#: assertion stopped being about the guard.
ALLOWED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ask", ("ask", "what is the refund policy")),
    ("store-info", ("store", "info")),
    ("store-migrate", ("store", "migrate")),
    ("probe-diff", ("probe", "diff")),
    ("coverage-rebuild", ("coverage", "recompute", "--rebuild")),
    ("export", ("export", "{export_out}")),
    ("freshness", ("freshness", "resolve", "--item", "kn_01GUARD")),
)


@dataclass(frozen=True, slots=True)
class Result:
    """What one CLI invocation returned, whichever way it was run."""

    returncode: int
    stdout: str
    stderr: str


def _run(*argv: str) -> Result:
    """One CLI invocation, **in process**, through the real entry point.

    `main(argv)` is what the installed `adopt` console script calls, so this is
    the same code path a subprocess would take minus the interpreter start. That
    matters here and nowhere else in this file's design: the table has 46 rows
    and a subprocess each costs about a second of import, which is 55 s of
    runtime buying nothing -- every assertion below is about an exit code, a
    code name on stderr, and a digest, and `main` returns all three.

    `tests/unit/test_cli_usage_errors.py` keeps its subprocesses deliberately:
    its claim is that stderr contains no `Traceback`, and only a real process
    can be said to have printed one.
    """
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    with redirect_stdout(captured_out), redirect_stderr(captured_err):
        try:
            code = main(list(argv))
        except SystemExit as exit_called:  # pragma: no cover -- no command does this
            code = int(exit_called.code or 0)
    return Result(code, captured_out.getvalue(), captured_err.getvalue())


def _run_out_of_process(*argv: str) -> subprocess.CompletedProcess[str]:
    """The fixture's own store build, which must not share this process's state."""
    return subprocess.run(
        [sys.executable, str(ENTRY_POINT), *argv],
        check=False,
        capture_output=True,
        text=True,
    )


def _build_store(root: Path) -> Path:
    """One initialised store and the files the verbs above name."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "answers.json").write_text(
        json.dumps({"artifact_access": True, "deploy_signal": True, "safe_interaction": True}),
        encoding="utf-8",
    )
    (root / "doc.md").write_text("# A document\n\nSomething true.\n", encoding="utf-8")
    (root / "probe.yaml").write_text(PROBE, encoding="utf-8")
    # `adopt harvest` reads git **before** it opens the store, so on a directory
    # that is not a checkout it refuses with `HARVEST_NOT_A_GIT_REPO` and never
    # reaches the guard. Making the fixture a real checkout is what puts harvest
    # in this table honestly rather than passing it for the wrong reason.
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=guard@example.invalid",
            "-c",
            "user.name=guard",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        cwd=str(root),
        check=True,
        capture_output=True,
    )

    store = root / "adopt.db"
    done = _run_out_of_process(
        "init",
        str(root),
        "--scope",
        SCOPE,
        "--answers",
        str(root / "answers.json"),
        "--store",
        str(store),
        "--archetype",
        "web",
        "--json",
    )
    assert done.returncode == ExitCode.SUCCESS, done.stderr
    return store


def _argv_for(argv: tuple[str, ...], root: Path, store: Path) -> list[str]:
    return [
        item.format(
            root=root,
            answers=root / "answers.json",
            doc=root / "doc.md",
            probe=root / "probe.yaml",
            out=root / "out",
            # Its own directory: `pack --draft-missing` writes into `out` on the
            # control store, and `adopt export` refuses a non-empty target -- so
            # sharing one would make the parity row fail for a reason that has
            # nothing to do with the guard.
            export_out=root / "export",
        )
        for item in argv
    ] + ["--store", str(store), "--json"]


@pytest.fixture(scope="module")
def stores(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path, Path]:
    """`(replica_root, replica_store, control_root, control_store)`.

    Two stores built the same way; only one carries the marker. Building the
    control through the same path is what makes "exits as it does on a normal
    store" an assertion rather than a hope.
    """
    from adopt_cli.replica import ReplicaMarker, write_marker

    base = tmp_path_factory.mktemp("replica-guard")
    replica_root = base / "replica"
    control_root = base / "control"
    replica_store = _build_store(replica_root)
    control_store = _build_store(control_root)
    write_marker(
        replica_store,
        ReplicaMarker(
            plane_url="https://plane.example",
            system_id="sys_01GUARD",
            pulled_at=_dt.datetime(2026, 9, 1, tzinfo=_dt.UTC),
            bundle_sha256="0" * 64,
        ),
    )
    return replica_root, replica_store, control_root, control_store


def _digests(store: Path) -> tuple[str, str]:
    from adopt_cli.replica import marker_path

    return (
        hashlib.sha256(store.read_bytes()).hexdigest(),
        hashlib.sha256(marker_path(store).read_bytes()).hexdigest(),
    )


@pytest.mark.unit
@pytest.mark.parametrize(("case", "argv"), REFUSED, ids=[case for case, _ in REFUSED])
def test_a_canon_writing_verb_is_refused_and_changes_nothing(
    stores: tuple[Path, Path, Path, Path], case: str, argv: tuple[str, ...]
) -> None:
    """Exit 3, one of the three replica codes, and not a byte moved.

    The digest assertion is the one that matters. An exit code says the process
    stopped; only the digest says it stopped *before writing*, and a guard placed
    one line too late would satisfy the exit-code assertion perfectly.
    """
    replica_root, replica_store, _, _ = stores
    before = _digests(replica_store)

    done = _run(*_argv_for(argv, replica_root, replica_store))

    assert done.returncode == ExitCode.POLICY_REFUSAL, f"{case}: {done.stdout}{done.stderr}"
    assert re.search(
        r"\"code\": \"(STORE|REFRESH|HANDOVER)_TARGET_(IS|NOT)_REPLICA\"", done.stderr
    ), f"{case}: {done.stderr}"
    assert _digests(replica_store) == before, f"{case} wrote to the replica"


@pytest.mark.unit
@pytest.mark.parametrize(("case", "argv"), REFUSED, ids=[case for case, _ in REFUSED])
def test_the_same_verb_is_not_refused_on_an_ordinary_store(
    stores: tuple[Path, Path, Path, Path], case: str, argv: tuple[str, ...]
) -> None:
    """The control for the row above, and the reason the fixture can be trusted.

    Several of these then fail for their own reasons -- `bind` names no real
    item, `answer` no real escalation -- and that is fine and deliberate: the
    row's claim is only that the marker is what refused, and the proof that each
    argv *reaches* the guard is the row above returning a replica code, which
    nothing else in the tree can produce.
    """
    _, _, control_root, control_store = stores

    done = _run(*_argv_for(argv, control_root, control_store))

    assert "_REPLICA" not in done.stderr, f"{case}: {done.stderr}"


@pytest.mark.unit
@pytest.mark.parametrize(("case", "argv"), ALLOWED, ids=[case for case, _ in ALLOWED])
def test_a_verb_that_writes_no_canon_still_works_on_a_replica(
    stores: tuple[Path, Path, Path, Path], case: str, argv: tuple[str, ...]
) -> None:
    """A replica exists to be read. `adopt ask` on one must still answer.

    Compared against the control rather than against `0`, because several of
    these fail for their own reasons on a store this bare -- and a guard that
    started refusing them would show up as a *difference*, which is the thing
    being measured.
    """
    replica_root, replica_store, control_root, control_store = stores

    on_replica = _run(*_argv_for(argv, replica_root, replica_store))
    on_control = _run(*_argv_for(argv, control_root, control_store))

    assert "_REPLICA" not in on_replica.stderr, f"{case}: {on_replica.stderr}"
    assert on_replica.returncode == on_control.returncode, (
        f"{case}: replica exit {on_replica.returncode} != control {on_control.returncode}"
    )


@pytest.mark.unit
def test_ask_still_answers_on_a_replica(stores: tuple[Path, Path, Path, Path]) -> None:
    """The acceptance line, asserted on its own rather than only as a parity row.

    `adopt ask` is the whole reason a replica is on a laptop. A parity assertion
    would be satisfied if the verb broke identically on both stores, so this one
    asserts the outcome: exit 0 and an answer payload on stdout.
    """
    replica_root, replica_store, _, _ = stores

    done = _run(*_argv_for(("ask", "what is the refund policy"), replica_root, replica_store))

    assert done.returncode == ExitCode.SUCCESS, done.stderr
    assert json.loads(done.stdout)["branch"] in {"known", "stale", "unknown"}


@pytest.mark.unit
def test_only_the_three_non_canon_writers_opt_out_of_the_guard() -> None:
    """The opt-out cannot spread without somebody noticing.

    *Fails when* a fourth call site passes `non_canon_reason`. *Matters because*
    the opt-out is the hole the guard exists to close: applied to a capture path
    -- `ask --escalate`, `serve`'s `escalate` field -- it would let canon reach a
    replica again, silently and with the reason field making it look considered.
    *No other instrument catches it because* the resulting write succeeds, which
    is exactly the state before this task.
    """
    sites = [
        f"{module.relative_to(REPO_ROOT)}:{number}"
        for module in sorted(CLI_SOURCE.rglob("*.py"))
        for number, line in enumerate(module.read_text(encoding="utf-8").splitlines(), 1)
        if "non_canon_reason=" in line and "non_canon_reason: str" not in line
    ]

    assert len(sites) == 3, (
        "exactly three verbs may open a store writable without the replica guard "
        f"-- ask, serve and coverage --rebuild. Found: {sites}"
    )
