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

__all__ = ["BACKENDS", "build_client"]

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
        return InProcessWorkflowClient(journal_dir)
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
    return client
