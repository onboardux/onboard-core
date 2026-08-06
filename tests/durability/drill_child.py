"""The process the drill kills.

Run as `python drill_child.py <backend> <journal-dir> <key>`, with this
directory on `PYTHONPATH`. It starts the
workflow with `halt=True`, so it commits the effect, announces it and then waits
to be killed. It never exits cleanly by design: a child that could finish on its
own would let the drill pass without ever testing a crash.
"""

import os
import sys
import threading
from pathlib import Path

from drill_backends import build_client
from drill_workflows import HALT_ENV, RUN_DIR_ENV, effect_key_for, paying_flow


def main(argv: list[str]) -> int:
    backend, journal_dir, idempotency_key = argv[1], Path(argv[2]), argv[3]
    os.environ[HALT_ENV] = "1"
    os.environ[RUN_DIR_ENV] = str(journal_dir)
    client = build_client(backend, journal_dir)
    client.start(
        paying_flow,
        {"effect_key": effect_key_for(idempotency_key)},
        idempotency_key=idempotency_key,
    )
    # **Outlive `start()`.** The in-process backend runs the body inline, so
    # it never returns from the call above -- the step is already blocked at its
    # halt. DBOS enqueues instead, so `start()` returns as soon as the run is
    # scheduled and this process would exit before a worker ever reached the
    # step. Blocking here is what makes one child serve both backends; the
    # parent kills us either way.
    threading.Event().wait()
    return 0  # pragma: no cover -- unreachable: the parent kills us first


if __name__ == "__main__":
    sys.exit(main(sys.argv))
