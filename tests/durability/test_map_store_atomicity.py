"""`01` N10 — a kill at any statement boundary leaves the store openable and unchanged.

*Fails when* the surface write can leave a store half-written: rows from an
interrupted `write_run` visible after reopening, a corrupt database, or a store
that will not open at all. *Matters because* `adopt map` runs on a consultant's
laptop inside a client environment, where the process is killed by a closed lid,
an OOM killer or an impatient operator -- and a store carrying half a run is
worse than no store, since `01` F5.3 never marks an identity dead, so the next
run reconciles against corrupted prior state and reports that nothing happened.
*No other instrument catches it because* every other test in this build runs in
one process that exits cleanly; `tests/durability/test_kill_and_resume.py` kills
a real process but drives Build 0's workflow backend and touches neither
`adopt_map` nor `SurfaceWriter`.

**This NFR had no instrument until 2026-08-19.** `01` N10 has been in the PRD
since v3.0 and `01` F3's acceptance signal ends *"a kill at any statement
boundary leaves the store clean"*, so the gap sat inside a checked feature box as
well as an unmeasured NFR row.

**On "any".** The write spans roughly 150-250 SQLite statements. Visiting every
one costs more suite time than `03` §7's ratchet allows, so this sweeps a
deterministic stride and says so rather than claiming exhaustiveness it does not
have. The stride is fixed, not random: a flaky sample would make a real
regression look like noise.

**Proven by planting.** Committing a canonical-table row before the kill turns
13 of 13 sweep cases red. Worth recording what the *first* plant showed: a
`CREATE TABLE` + `INSERT` into a table of its own was **invisible**, because the
fingerprint walks `MODEL_FOR_TABLE` and a non-canonical table is not in it. So
the stated scope of this drill is the canonical store, and a write path that
invented its own table would escape it -- which `no-foreign-tables` is the gate
for, not this one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from adopt_store.api import open_store
from tests.durability.fingerprint import fingerprint_store

pytestmark = pytest.mark.durability

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Kill points, in statements after the write path is entered. The upper bound
#: sits below the shortest observed completion (~250) so every point in the
#: sweep lands **inside** the write rather than after it -- a kill after COMMIT
#: proves nothing and would pass for the wrong reason.
_KILL_POINTS = tuple(range(1, 150, 12))

_CHILD = "tests.durability._map_kill_child"


def _run_child(root: Path, kill_at: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", _CHILD, str(root), str(kill_at)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
        timeout=120,
    )


def _prewrite_digest(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("PREWRITE "):
            return line.split(" ", 1)[1].strip()
    raise AssertionError(f"child never announced its pre-write fingerprint: {stdout!r}")


@pytest.mark.parametrize("kill_at", _KILL_POINTS)
def test_a_kill_mid_write_leaves_the_store_openable_and_unchanged(
    tmp_path: Path, kill_at: int
) -> None:
    """The drill itself, at one statement boundary."""
    root = tmp_path / f"kill-{kill_at}"
    result = _run_child(root, kill_at)

    assert "COMPLETED" not in result.stdout, (
        f"the write finished before statement {kill_at}, so this case killed nothing and "
        "asserts nothing; lower the sweep's upper bound"
    )
    assert result.returncode == 137, (
        f"child exited {result.returncode}, not the 137 `os._exit` was asked for. "
        f"An ordinary exception would be **handled** by `SqliteStore.transaction()`'s "
        f"rollback, which is not what N10 is about. stderr: {result.stderr[-500:]}"
    )

    before = _prewrite_digest(result.stdout)

    # 1. It opens. A store that will not open has lost the run *and* everything
    #    that preceded it.
    handle = open_store(root / "store.db", migrate=False)
    try:
        # 2. SQLite itself is satisfied -- no torn pages, no broken indexes.
        integrity = handle.backend._connection.execute("PRAGMA integrity_check;").fetchone()[0]
        assert integrity == "ok", f"integrity_check said {integrity!r}"

        # 3. And it is *unchanged*: the interrupted transaction contributed
        #    nothing. This is the half that distinguishes "the file survived"
        #    from "the write was atomic".
        assert fingerprint_store(handle) == before, (
            f"a kill at statement {kill_at} left rows behind: the store differs from its "
            "pre-write state, so `write_run` is not atomic at this boundary"
        )
    finally:
        handle.close()


def test_the_uninterrupted_write_completes_and_changes_the_store(tmp_path: Path) -> None:
    """**The positive control**, without which the sweep above is vacuous.

    Every assertion in the drill is satisfied by a `write_run` that does nothing
    at all: a store that was never written to is trivially openable, intact and
    identical to itself. This proves the write under test actually writes, so
    "unchanged after a kill" is a statement about atomicity rather than about an
    inert code path.
    """
    root = tmp_path / "clean"
    result = _run_child(root, 10**9)

    assert result.returncode == 0, result.stderr[-500:]
    assert "COMPLETED" in result.stdout

    before = _prewrite_digest(result.stdout)
    handle = open_store(root / "store.db", migrate=False)
    try:
        assert fingerprint_store(handle) != before, (
            "the uninterrupted write left the store byte-identical, so the drill's "
            "'unchanged' assertion would hold no matter how broken the transaction was"
        )
    finally:
        handle.close()


def test_the_child_dies_without_unwinding(tmp_path: Path) -> None:
    """**The second control**: the kill is a kill, not a caught exception.

    `SqliteStore.transaction()` catches `BaseException` and issues a `ROLLBACK`,
    so a drill that killed by raising would be testing that handler -- which
    already works -- and would say nothing about a process that never gets to
    run it. `os._exit` skips `finally`, context managers and interpreter
    shutdown; this asserts the child really left that way.
    """
    root = tmp_path / "abrupt"
    result = _run_child(root, 1)

    assert result.returncode == 137
    assert result.stderr == "", (
        "a traceback means the process unwound, and an unwinding process runs the "
        f"rollback this drill exists to do without: {result.stderr[-500:]}"
    )
