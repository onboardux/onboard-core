"""The child half of the `01` N10 drill: die at a chosen statement boundary.

Run as a module, never imported by the suite. It builds a store, announces the
fingerprint of that store **before** the surface write, and then dies partway
through the write with no unwinding at all.

**Why `os._exit` and not an exception.** N10 is *"kill at any statement
boundary"*, and an exception is the one kind of death this write path already
handles: `SqliteStore.transaction()` catches `BaseException` and issues a
`ROLLBACK`. A test that raised would be asserting that the rollback handler
works, which is a different and much weaker claim. `os._exit` skips `finally`
blocks, context managers and interpreter shutdown, so nothing in this process
gets the chance to tidy up -- the store is left exactly as the operating system
found it, which is what a `SIGKILL`, a power cut and an OOM kill all look like.

**Why a trace callback rather than a timer.** Implementation spec §5 bans sleeps
in tests, and a wall-clock kill would land somewhere different on every machine
and every run -- so a green suite would mean "we happened not to hit a bad
point" rather than "there is no bad point". `set_trace_callback` fires once per
statement, so counting them makes the kill point *addressable*: the parent
sweeps the range and every boundary is visited deterministically.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from adopt_extractors_common import MANIFEST  # noqa: E402
from adopt_map.schemas import SurfaceFact  # noqa: E402

from tests.build1_conftest import build_scoped_store, surface_writer_for  # noqa: E402

#: Enough facts that the write spans many statements, and all of one kind the
#: stub manifest declares -- a kind outside it is refused before any SQL runs
#: (`02` §7 obligation 4), which would make this drill exercise validation
#: rather than the transaction.
_FACT_COUNT = 12


def _facts() -> list[SurfaceFact]:
    return [
        SurfaceFact(
            identity_kind="endpoint",
            namespace="http",
            local_key=f"GET /v1/items/{index}",
            title=f"GET /v1/items/{index}",
        )
        for index in range(_FACT_COUNT)
    ]


def main(argv: list[str]) -> int:
    root = Path(argv[1])
    kill_at = int(argv[2])

    handle, scopes = build_scoped_store(root)
    resolved = scopes["prod"]

    # Announced before the write so the parent has the exact pre-write state to
    # compare against. Scope creation mints ULIDs, so this value differs every
    # run and cannot be a constant in the parent.
    from tests.durability.fingerprint import fingerprint_store

    print(f"PREWRITE {fingerprint_store(handle)}", flush=True)  # noqa: T201

    connection: sqlite3.Connection = handle.backend._connection
    seen = 0

    def _trace(statement: str) -> None:
        nonlocal seen
        seen += 1
        if seen >= kill_at:
            # No flush, no close, no rollback. That is the point.
            os._exit(137)

    connection.set_trace_callback(_trace)

    writer = surface_writer_for(handle)
    writer.write_run(resolved=resolved, manifest=MANIFEST, facts=_facts(), vcs_revision="abc123")

    connection.set_trace_callback(None)
    print("COMPLETED", flush=True)  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
