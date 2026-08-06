"""The workflow the drill kills, declared at module level on purpose.

A resumed run resolves its body **by name**, because after a process death there
is no closure left -- only a name in a journal. So the drill's workflow cannot be
declared inside a test function: the child process and the recovering parent must
both be able to import it and arrive at the same definition.

**The halt is environmental, not an argument.** A crash is something that happens
*to* a run, not something the run was asked for -- and a `halt` in the recorded
arguments would be replayed on resume, so the recovering process would stop dead
in exactly the same place. The child sets `ADOPT_DRILL_HALT`; the parent does
not, so the resumed step runs to completion.

Reading an environment variable inside a **step** is legitimate and inside a
**body** is not: that asymmetry is the whole point of the split, and this file
sits on both sides of it deliberately.
"""

import os
import sys
from pathlib import Path
from typing import Any

from adopt_workflow import RetryPolicy, step, workflow

__all__ = [
    "EFFECTS_FILE",
    "HALT_ENV",
    "MARKER",
    "RUN_DIR_ENV",
    "charge_once",
    "committed_effects",
    "effect_key_for",
    "effects_path",
    "paying_flow",
]

#: What the child prints once the effect is durable. The parent blocks on
#: reading this line, which is a rendezvous rather than a sleep -- implementation
#: spec §5 bans sleeps, and a timing-based handshake would make the drill flaky
#: on a loaded runner rather than wrong.
MARKER: str = "EFFECT_COMMITTED"

#: Set by the child only.
HALT_ENV: str = "ADOPT_DRILL_HALT"


#: The dedupe key is **per run**, and it travels in the run's arguments so the
#: resumed execution replays the same one. A module constant worked against the
#: in-process backend, where every test gets its own journal directory -- and
#: collides immediately against DBOS, where one Postgres table is shared by every
#: test in the job and `effect_key` is its primary key. The second test would
#: have found the first test's claim already there, concluded the effect was a
#: replay, and never committed anything.
def effect_key_for(run_key: str) -> str:
    return f"charge:{run_key}"


#: Where the run directory is, so the step can find the effect log after a
#: process death. Passed as an environment variable rather than as a workflow
#: argument because it is a property of the machine, not of the run.
RUN_DIR_ENV: str = "ADOPT_DRILL_DIR"

#: **The effect the drill counts, and it is a real one.** A line appended to this
#: file, never through the in-process journal -- so the same assertion holds for
#: a backend that keeps its dedupe state in Postgres. Counting journal records
#: instead would have made the drill measure one backend's bookkeeping and call
#: it a property of both.
EFFECTS_FILE: str = "effects.log"


def effects_path(run_dir: Path) -> Path:
    return run_dir / EFFECTS_FILE


def committed_effects(run_dir: Path) -> list[str]:
    path = effects_path(run_dir)
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


@step(name="charge-once", retries=RetryPolicy(max_attempts=1))
def charge_once(ctx: Any, effect_key: str) -> str:
    """Commit one effect, then -- in the child only -- stop dead.

    The halt opens the window `05` S8 names: the effect has committed and the
    step record has not been written, which is the one interleaving where a naive
    backend either loses the effect or repeats it.
    """
    first = ctx.dedupe(effect_key)
    if first:
        run_dir = Path(os.environ[RUN_DIR_ENV])
        with effects_path(run_dir).open("a", encoding="utf-8") as handle:
            handle.write(f"{effect_key}\n")
            handle.flush()
            os.fsync(handle.fileno())
    if os.environ.get(HALT_ENV):
        # The rendezvous with the parent. `print` is the point here, not a
        # stray debug line: stdout is the channel the drill blocks on.
        print(MARKER, flush=True)  # noqa: T201
        # Block until killed. A read that never returns is a rendezvous the
        # parent controls; a sleep would be a race the parent hopes to win.
        sys.stdin.readline()
    return "charged" if first else "already-charged"


@workflow(name="paying-flow")
def paying_flow(ctx: Any, args: dict[str, Any]) -> str:
    return str(ctx.step(charge_once, args["effect_key"]))
