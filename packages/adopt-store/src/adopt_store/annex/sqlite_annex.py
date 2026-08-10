"""SQLite realization of the runtime annex. Contracts §12.

The DDL is **read from `schema/annex/0001__agent_run.sql`**, not embedded as a
string literal. Two reasons, and the second is the one that matters: a literal
would put a `CREATE TABLE` inside `packages/`, which `no-foreign-tables` forbids
and which CR-45 declined to route around; and a reader looking for "what is in
the annex" should find one answer, in a file that says so, rather than a Python
string that happens to be the truth today.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final

from adopt_agent.annex import AgentRunRecord
from adopt_const import STORE_BUSY_TIMEOUT_MS
from adopt_schema.assets import assets_root

__all__ = ["SqliteAnnexRecords", "annex_path", "open_annex"]

#: Contracts §12: `.adopt/runtime.db` locally. Resolved beside the canonical
#: store so the two travel together -- an annex beside a *different* store is an
#: idempotency table answering for runs that were never made against it.
_ANNEX_FILENAME: Final[str] = "runtime.db"

#: This module used to carry its own `parents[5]` walk to the checkout, and its
#: comment cited `adopt_schema.manifest` and `adopt_store.api` doing the same --
#: three copies of one assumption that held only in a checkout (CR-53). All three
#: now resolve through `adopt_schema.assets`, which is also why this is looked up
#: per call rather than at import: the answer can now fail, and a failure at
#: import time would take down every command instead of the one that needs a
#: store.

_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "scope_ref",
    "idempotency_key",
    "skill_ref",
    "skill_sha256",
    "inputs_sha256",
    "adapter",
    "model",
    "params_hash",
    "status",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "wall_ms",
    "trace_json",
    "output_ref",
    "created_at",
)


def annex_path(store_path: Path) -> Path:
    """Where the annex lives for a given canonical store."""
    return store_path.parent / _ANNEX_FILENAME


@contextmanager
def open_annex(path: Path, *, repo_root: Path | None = None) -> Iterator["SqliteAnnexRecords"]:
    """Open (creating if absent) the annex at `path` and yield its records.

    A context manager rather than a bare handle, because the annex is opened for
    the duration of one seam call; holding it open across a process makes a
    second writer wait on a lock for no reason, and `busy_timeout` covers the
    case where one genuinely does.

    **No `user_version` is read or written.** The annex is outside
    `schema_version` (CR-08), and `IF NOT EXISTS` in the DDL is what makes
    opening idempotent. A version stamp here would be the first step towards
    someone migrating the annex alongside the canonical store, which is the
    coupling the ratification exists to prevent.
    """
    root = repo_root if repo_root is not None else assets_root()
    ddl = (root / "schema" / "annex" / "0001__agent_run.sql").read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(STORE_BUSY_TIMEOUT_MS)};")
        connection.executescript(ddl)
        yield SqliteAnnexRecords(connection)
    finally:
        connection.close()


class SqliteAnnexRecords:
    """Realizes `adopt_agent.annex.AnnexRecords` structurally.

    Two methods and no setter: contracts §12 records that a run happened, and
    there is no operation for recording that it happened differently.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def find_run(self, *, scope_ref: str, idempotency_key: str) -> AgentRunRecord | None:
        """Look up by the §12 unique index, never by key alone.

        Keying on the pair matters: two engagements may legitimately choose the
        same idempotency key, and a global key space would hand one client's
        recorded run to another client's replay.
        """
        columns = ", ".join(_COLUMNS)
        with closing(
            self._connection.execute(
                f"SELECT {columns} FROM agent_run "  # noqa: S608 -- names are this module's
                "WHERE scope_ref = ? AND idempotency_key = ?;",
                (scope_ref, idempotency_key),
            )
        ) as cursor:
            row = cursor.fetchone()
        if row is None:
            return None
        return AgentRunRecord.model_validate({name: row[name] for name in _COLUMNS})

    def record_run(self, record: AgentRunRecord) -> AgentRunRecord:
        """Persist a completed run and return **what is stored afterwards**.

        On a race the loser gets the winner's record back rather than an error,
        and that is the correct answer rather than a lenient one: the caller
        asked "what is the result for this key", two callers raced to record it,
        and idempotency means they must both be told the same thing. Raising
        would make the second caller retry a provider call that has already been
        paid for -- the exact double-spend the annex exists to prevent.

        `ON CONFLICT DO NOTHING` followed by a read is one statement and one
        read, so the loser observes the winner's committed row. The same shape
        the DBOS backend's `dedupe` uses (`02` §10.2, CR-43), for the same
        reason: the marker must be claimed atomically or it claims nothing.
        """
        placeholders = ", ".join("?" for _ in _COLUMNS)
        columns = ", ".join(_COLUMNS)
        values = tuple(getattr(record, name) for name in _COLUMNS)
        self._connection.execute(
            f"INSERT INTO agent_run ({columns}) VALUES ({placeholders}) "  # noqa: S608
            "ON CONFLICT(scope_ref, idempotency_key) DO NOTHING;",
            values,
        )
        stored = self.find_run(scope_ref=record.scope_ref, idempotency_key=record.idempotency_key)
        if stored is None:  # pragma: no cover -- the row was just inserted or already there
            raise RuntimeError("agent_run vanished between insert and read")
        return stored
