"""Backend resolution for the one durability suite.

**Why the DBOS backend is reached by name rather than by import.** `core-independent`
forbids any `adopt_*` package importing a `plane_*` one, and the licence boundary
behind that rule is irreversible: `adopt-core` is Apache-2.0 and publishes at
`0.3.0`, while `plane_workflow` is closed forever. A static
`from plane_workflow...` anywhere in this repository would make the open
repository's suite depend on a private package.

So this module holds a **name**, not an import. `adopt-core`'s CI runs
`--backend=inproc`; `adopt-plane`'s CI checks this repository out as a sibling --
which five of its jobs already do -- and runs the same suite with
`--backend=dbos`, where `plane_workflow` is importable. One suite, two
backends, and the closed half never leaks into the open tree. The module name
below is already public in this repository's `importlinter.ini`, which names
`plane_workflow` as a forbidden module, so nothing new is disclosed.
"""

import importlib
from pathlib import Path
from typing import Final

from adopt_workflow import InProcessWorkflowClient, WorkflowClient

__all__ = ["BACKENDS", "build_client", "close_built_clients"]

#: Every client this process has built and not yet closed.
#:
#: **A durable backend's client is a worker, not a handle.** A launched DBOS
#: instance polls the shared queue for the life of the process, so a client left
#: open by test *N* is still competing for work in test *N+1* -- and it wins
#: often enough to execute the child's run in the parent, where the drill's
#: environment is not set. Tracking what was built is what lets the conftest
#: guarantee no worker outlives the test that made it.
_BUILT: list[WorkflowClient] = []

#: `name -> "module:factory"`. `inproc` resolves in this repository; anything
#: else is resolved at call time and is only importable where it belongs.
BACKENDS: Final[dict[str, str]] = {
    "inproc": "",  # built directly below; no indirection to misread
    "dbos": "plane_workflow.dbos_backend:build_durability_client",
}


def build_client(backend: str, journal_dir: Path) -> WorkflowClient:
    """A client for `backend`, rooted at `journal_dir`.

    An unknown backend raises rather than falling back to `inproc`: a drill that
    silently ran the in-process backend when asked for DBOS would report the
    durable engine green without ever starting it.
    """
    if backend == "inproc":
        inproc = InProcessWorkflowClient(journal_dir)
        _BUILT.append(inproc)
        return inproc
    try:
        target = BACKENDS[backend]
    except KeyError:
        raise ValueError(
            f"unknown workflow backend {backend!r}; known: {sorted(BACKENDS)}"
        ) from None
    module_name, _, factory_name = target.partition(":")
    module = importlib.import_module(module_name)
    factory = getattr(module, factory_name)
    client: WorkflowClient = factory(journal_dir)
    _BUILT.append(client)
    return client


def close_built_clients() -> None:
    """Close every client built since the last call, newest first.

    Errors are **not** swallowed: a backend that cannot be shut down is exactly
    the defect this exists to prevent, and a teardown that hides it would let
    the next test inherit a live worker while reporting nothing.
    """
    while _BUILT:
        _BUILT.pop().close()
