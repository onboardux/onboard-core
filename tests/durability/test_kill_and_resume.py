"""The kill-and-resume drill: `SIGKILL` after the effect, before the step record.

*Fails when* a resumed run repeats a committed effect or fails to reach a terminal
status. *Matters because* the whole reason durable execution exists is that a
process dies mid-sequence and the work is neither lost nor duplicated -- and a
duplicated effect at the payment or notification boundary is the failure a client
notices. *No other instrument catches it because* every other test in the suite
runs in one process that never dies, which is precisely the condition under which
a broken resume looks correct.

**The kill point is the one `05` S8 names**, and it is the only interesting one:
the effect has committed and the step record has not. A backend that writes its
dedupe marker before the effect loses the effect here; one that writes it after
repeats it. Only a backend that commits them together survives.

The child announces the committed effect on stdout and the parent blocks on that
line -- a rendezvous, not a sleep. Implementation spec §5 bans sleeps in tests,
and a timing-based handshake would make this drill flaky rather than wrong.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from drill_backends import build_client
from drill_workflows import MARKER, RUN_DIR_ENV, committed_effects

from adopt_workflow import TERMINAL_STATUSES

pytestmark = pytest.mark.durability

#: This directory. The suite is **self-contained on purpose** -- it is run from
#: `adopt-core` against the in-process backend and from `adopt-plane` against
#: DBOS, and the second only works if nothing here reaches for a package, a
#: conftest or a `tests.` prefix that exists in one repository and not the
#: other. That was untested when CR-40 first claimed it, and CI said so.
HERE = Path(__file__).resolve().parent
CHILD_TIMEOUT_S = 60


def _effect_records(journal_dir: Path) -> list[str]:
    """Every committed effect, read from the side effect itself.

    Deliberately not from a backend's bookkeeping: the claim is about the effect
    the client would be billed for, and reading one backend's journal would make
    the drill unable to say anything about the other.
    """
    return committed_effects(journal_dir)


def _crash_after_effect(backend: str, journal_dir: Path, key: str) -> None:
    """Run the workflow in a child process and kill it once its effect is durable.

    The `with` block matters beyond tidiness: `filterwarnings = ["error"]` turns
    a leaked pipe into a test failure, and a drill that leaked three file handles
    per run would fail for a reason that has nothing to do with durability.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HERE)
    env[RUN_DIR_ENV] = str(journal_dir)
    with subprocess.Popen(
        [sys.executable, str(HERE / "drill_child.py"), backend, str(journal_dir), key],
        cwd=HERE,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as child:
        assert child.stdout is not None
        # **A watchdog, because the alternative to a failing drill is a hanging
        # one.** The read below blocks until the marker arrives; if the child
        # never gets there, killing it closes the pipe, the loop ends at EOF and
        # the test fails with the child's stderr attached. Without this a broken
        # backend burns the job's entire time limit and reports nothing.
        watchdog = threading.Timer(CHILD_TIMEOUT_S, child.kill)
        watchdog.daemon = True
        watchdog.start()
        # **Read until the marker, not just the first line.** DBOS announces
        # itself on stdout before any of our code runs, so a rendezvous that
        # trusted line one read "DBOS system database URL:" and declared the
        # effect uncommitted. A backend is entitled to be chatty; the drill is
        # not entitled to assume otherwise.
        seen: list[str] = []
        while True:
            line = child.stdout.readline()
            if not line:  # EOF: the child died, or the watchdog killed it
                watchdog.cancel()
                child.kill()
                stderr = child.stderr.read() if child.stderr else ""
                pytest.fail(
                    f"child never committed its effect; stdout={''.join(seen)!r} stderr={stderr}"
                )
            seen.append(line)
            if MARKER in line:
                break
        # `kill` is `SIGKILL` on POSIX and `TerminateProcess` on Windows: in both
        # cases unconditional, with no chance to flush, finalize or clean up.
        child.kill()
        child.wait(timeout=CHILD_TIMEOUT_S)
        watchdog.cancel()


def _resume_and_settle(backend: str, journal_dir: Path, key: str) -> tuple[Any, Any]:
    """Bring the killed run to a terminal state, and return `(client, handle)`.

    **Asserts the outcome, not the mechanism.** The in-process backend is told to
    recover; DBOS has already done it inside `launch()`, so its `recover()` finds
    nothing left to resume. An earlier version of this drill asserted
    `list(status="running")` was non-empty *before* recovering, and that is a
    statement about one backend's timing rather than about durability -- it
    passed on `inproc` and failed on DBOS for a reason that is not a defect.

    What both backends must agree on is what this returns: the run reaches a
    terminal state and its result is readable. `result(..., timeout_s=...)` is
    the contract's own blocking primitive (§10.2), which is why waiting here
    needs no sleep.
    """
    client = build_client(backend, journal_dir)
    client.recover()
    handle = next(h for h in client.list() if h.idempotency_key == key)
    client.result(handle.run_id, timeout_s=CHILD_TIMEOUT_S)
    return client, next(h for h in client.list() if h.idempotency_key == key)


def test_a_killed_run_resumes_with_its_effect_committed_exactly_once(
    backend: str, tmp_path: Path
) -> None:
    journal_dir = tmp_path / "runtime"
    journal_dir.mkdir()
    key = "drill-1"

    _crash_after_effect(backend, journal_dir, key)

    assert len(_effect_records(journal_dir)) == 1, "the effect did not commit before the kill"

    client, handle = _resume_and_settle(backend, journal_dir, key)
    assert handle.status == "completed"
    assert client.status(handle.run_id) in TERMINAL_STATUSES

    # The claim the whole sprint is for.
    assert len(_effect_records(journal_dir)) == 1, "the resume repeated a committed effect"


def test_the_resumed_run_replays_the_step_rather_than_repeating_the_effect(
    backend: str, tmp_path: Path
) -> None:
    """The resumed step *runs* -- it is the effect that does not repeat.

    Asserted because the cheap way to pass the test above is to skip the step
    entirely on resume, which would also skip every step after the crash point.
    """
    journal_dir = tmp_path / "runtime"
    journal_dir.mkdir()

    _crash_after_effect(backend, journal_dir, "drill-2")

    client, handle = _resume_and_settle(backend, journal_dir, "drill-2")

    # `already-charged` is what the step returns when `dedupe` says the effect
    # was already committed -- so the body ran, the step ran, and only the
    # effect was suppressed.
    assert client.result(handle.run_id, timeout_s=CHILD_TIMEOUT_S) == "already-charged"


def test_a_completed_run_is_not_resumed_again(backend: str, tmp_path: Path) -> None:
    """Recovery is for non-terminal runs. Re-driving a completed one would repeat
    every effect it already committed, which is the failure this suite exists to
    prevent, arriving through the recovery path instead of the crash path."""
    journal_dir = tmp_path / "runtime"
    journal_dir.mkdir()

    _crash_after_effect(backend, journal_dir, "drill-3")

    client, handle = _resume_and_settle(backend, journal_dir, "drill-3")
    assert handle.status in TERMINAL_STATUSES

    # Recovery over a settled run must be a no-op. Re-driving it would repeat
    # every effect it already committed -- the failure this suite exists to
    # prevent, arriving through the recovery path instead of the crash path.
    assert client.recover() == []
    assert len(_effect_records(journal_dir)) == 1
