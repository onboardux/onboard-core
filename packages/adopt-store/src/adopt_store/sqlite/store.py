"""The SQLite realization: connection ownership, transactions, migrations.

`SqliteStore` is the `MigrationTarget` `adopt_schema.migrate` drives, and the
transaction boundary every facade shares. Both live here because both are
dialect facts, and the dialect is what `adopt_schema` is forbidden to know:

* **`executescript` implicitly commits** any open transaction before it runs, so
  a migration script cannot simply be wrapped in a `BEGIN` the caller opened.
  The statements are therefore executed individually inside one explicit
  transaction, which is what makes a failed migration leave nothing behind.
* **`PRAGMA journal_mode` cannot run inside a transaction at all**, so pragmas
  are applied first, in autocommit.

There is no down-migration and there never will be. Recovery is older code
opening a newer store, and that only works if a store is never left in a state
no version of the binary produced — which is the whole reason atomicity is
implemented here rather than assumed of the runner.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, format_timestamp
from adopt_store.sqlite.connection import (
    connect,
    read_user_version,
    table_names,
    translate_sqlite_error,
)

__all__ = ["SqliteStore", "split_statements"]

_PRAGMA: Final[str] = "PRAGMA"
#: `user_version` is stored in the database header and is journaled, so it
#: belongs **inside** the migration transaction with the DDL it describes.
#: Leaving it outside is how a failed migration ends with a store claiming a
#: version whose tables were rolled back -- a state no version of the binary
#: ever produced, which is the one state the no-down-migration design cannot
#: survive. Every other pragma here is connection or file state and must run in
#: autocommit, because `journal_mode` cannot be set inside a transaction at all.
_TRANSACTIONAL_PRAGMAS: Final[tuple[str, ...]] = ("PRAGMA USER_VERSION",)
_SCHEMA_META_INSERT: Final[str] = (
    "INSERT INTO schema_meta (schema_version, export_version, written_by, written_at) "
    "VALUES (?, ?, ?, ?)"
)


def _is_connection_pragma(statement: str) -> bool:
    upper = " ".join(statement.upper().split())
    if not upper.startswith(_PRAGMA):
        return False
    return not any(upper.startswith(name) for name in _TRANSACTIONAL_PRAGMAS)


def split_statements(sql: str) -> tuple[list[str], list[str]]:
    """``(connection pragmas, everything else)``, comments stripped.

    Split by `sqlite3.complete_statement` rather than on `;`, because a table
    purpose in a comment legitimately contains a semicolon and splitting on the
    character silently truncates the statement that follows it.
    """
    pragmas: list[str] = []
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        if line.lstrip().startswith("--"):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                target = pragmas if _is_connection_pragma(statement) else statements
                target.append(statement)
            buffer = ""
    return pragmas, statements


class SqliteStore:
    """Owns one connection, its transaction, and the migration path.

    One writer per store (implementation spec §5). Readers use WAL, so a reader
    never blocks the writer; revision-chain safety comes from `expected_head_id`
    in the revision writer, never from locking.
    """

    def __init__(
        self,
        path: Path,
        *,
        read_only: bool = False,
        read_only_reason: str = "the caller opened it read-only",
        clock: Clock | None = None,
    ) -> None:
        self.path = path
        self.read_only = read_only
        self.read_only_reason = read_only_reason
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._connection = connect(path, read_only=read_only)
        self._depth = 0

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- transactions -----------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """One unit of work. Nested use joins the outermost transaction.

        Nesting joins rather than nests because SQLite has no nested
        transactions worth the name, and a savepoint that silently commits half
        a facade call is worse than one boundary a caller can reason about.
        """
        if self._depth > 0:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return

        self.require_writable()
        with translate_sqlite_error(read_only_reason=self.read_only_reason):
            self._connection.execute("BEGIN;")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK;")
            self._depth = 0
            raise
        self._connection.execute("COMMIT;")
        self._depth = 0

    def require_writable(self) -> None:
        """Raise ``STORE_READ_ONLY`` before a write reaches a read-only store.

        Checked here as well as enforced by the `mode=ro` URI: the URI is the
        guarantee, and this is the message. A driver error saying
        "attempt to write a readonly database" does not tell an operator that
        their binary is older than their store.
        """
        if not self.read_only:
            return
        raise AdoptError(
            ErrorCode.STORE_READ_ONLY,
            message=f"the store at {self.path} is open read-only: {self.read_only_reason}",
            hint="Re-open it for writing. A store found read-only is never upgraded in "
            "place by the code that found it so.",
        )

    # -- reads ------------------------------------------------------------

    def query(self, sql: str, parameters: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        """Run one read. Rows leave as mappings; cursors never leave this class."""
        with translate_sqlite_error(read_only_reason=self.read_only_reason):
            return list(self._connection.execute(sql, parameters))

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> None:
        """Run one write inside the caller's transaction."""
        self.require_writable()
        with translate_sqlite_error(read_only_reason=self.read_only_reason):
            self._connection.execute(sql, parameters)

    def is_empty(self) -> bool:
        """Whether the file holds no user tables — a store not yet created."""
        return not table_names(self._connection)

    # -- MigrationTarget --------------------------------------------------

    def current_version(self) -> int:
        """`PRAGMA user_version`; `0` for a store that has never been migrated."""
        return read_user_version(self._connection)

    def apply_migration(
        self, sql: str, schema_version: int, export_version: int, written_by: str
    ) -> None:
        """Apply one migration file and append its `schema_meta` row atomically.

        Raises:
            AdoptError: ``STORE_READ_ONLY`` when the store is not writable.
        """
        self.require_writable()
        pragmas, statements = split_statements(sql)
        with translate_sqlite_error(read_only_reason=self.read_only_reason):
            for statement in pragmas:
                self._connection.execute(statement)

            self._connection.execute("BEGIN;")
            try:
                for statement in statements:
                    self._connection.execute(statement)
                self._connection.execute(
                    _SCHEMA_META_INSERT,
                    (
                        schema_version,
                        export_version,
                        written_by,
                        format_timestamp(self._clock.now()),
                    ),
                )
            except Exception:
                self._connection.execute("ROLLBACK;")
                raise
            self._connection.execute("COMMIT;")

    def append_schema_meta(self, schema_version: int, export_version: int, written_by: str) -> None:
        """Append one `schema_meta` row — never update one (CR-04).

        Contracts §14 requires the row to be written on **every** open, which is
        what turns the table into the migration log CR-04 describes. The table
        has no primary key by design and no `UPDATE` path exists anywhere.
        """
        self.require_writable()
        with self.transaction():
            self.execute(
                _SCHEMA_META_INSERT,
                (
                    schema_version,
                    export_version,
                    written_by,
                    format_timestamp(self._clock.now()),
                ),
            )
