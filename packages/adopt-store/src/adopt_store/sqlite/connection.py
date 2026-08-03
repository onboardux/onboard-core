"""The one module in the programme permitted to import `sqlite3`.

The `no-raw-sqlite` import contract points here. Everything above it — facades,
the scope hierarchy, the CLI — sees rows and typed errors, never a connection, a
cursor or a string of SQL (contracts §10.3).

Three connection settings are not optional and are applied on every open:

* **WAL**, so a reader never blocks the single writer. It is set outside a
  transaction because SQLite refuses `journal_mode` inside one, and it is
  persistent in the database file rather than per-connection.
* **`foreign_keys = ON`**, because SQLite defaults it *off* and the canonical
  schema is full of foreign keys that would otherwise be documentation.
* **`busy_timeout`**, from `STORE_BUSY_TIMEOUT_MS`, so a concurrent writer waits
  rather than failing instantly.

A read-only store is opened through a `mode=ro` URI, so the guarantee is the
database's rather than a flag this package remembers to check.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from adopt_const import STORE_BUSY_TIMEOUT_MS
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "connect",
    "read_user_version",
    "set_user_version",
    "table_names",
    "translate_sqlite_error",
]

_READONLY_MARKERS: Final[tuple[str, ...]] = ("readonly database", "read-only database")


def _uri_for(path: Path, *, read_only: bool) -> str:
    """A SQLite URI. `mode=ro` refuses to create a missing file, which is what
    makes "open the store the operator already has" different from "make one"."""
    as_uri = path.absolute().as_uri()
    return f"{as_uri}?mode=ro" if read_only else as_uri


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a connection with the pragmas the canonical schema assumes.

    Raises:
        AdoptError: ``SCHEMA_MIGRATION_PENDING`` when a read-only open names a
            file that does not exist. There is nothing to read and creating it
            would silently turn a reader into a writer.
    """
    if read_only and not path.exists():
        raise AdoptError(
            ErrorCode.SCHEMA_MIGRATION_PENDING,
            message=f"no store exists at {path}",
            hint="A read-only open never creates a store. Open it for writing with "
            "`migrate=True` to create schema version 3.",
        )

    connection = sqlite3.connect(
        _uri_for(path, read_only=read_only),
        uri=True,
        isolation_level=None,  # explicit transactions; see `transaction()`
        check_same_thread=True,
    )
    connection.row_factory = sqlite3.Row
    # `journal_mode` cannot be set inside a transaction, and a read-only
    # connection cannot change it at all -- the mode is a property of the file,
    # already set by whoever created it.
    if not read_only:
        connection.execute("PRAGMA journal_mode = WAL;")
    connection.execute("PRAGMA foreign_keys = ON;")
    connection.execute(f"PRAGMA busy_timeout = {int(STORE_BUSY_TIMEOUT_MS)};")
    return connection


def read_user_version(connection: sqlite3.Connection) -> int:
    """`PRAGMA user_version`; `0` for a store that has never been migrated."""
    row = connection.execute("PRAGMA user_version;").fetchone()
    return int(row[0])


def set_user_version(connection: sqlite3.Connection, version: int) -> None:
    """Set `PRAGMA user_version`.

    The value is interpolated because SQLite does not accept a bound parameter
    in a pragma. `int()` above is the whole sanitisation surface, and the caller
    is always the migration runner with a version from the manifest.
    """
    connection.execute(f"PRAGMA user_version = {int(version)};")


def table_names(connection: sqlite3.Connection) -> frozenset[str]:
    """Every user table present, so an empty file is distinguishable from a store."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%';"
    ).fetchall()
    return frozenset(str(row[0]) for row in rows)


@contextmanager
def translate_sqlite_error(*, read_only_reason: str) -> Iterator[None]:
    """Turn a driver exception into a typed one.

    Implementation spec §5: never raise a bare exception across a package
    boundary. A `sqlite3.OperationalError` reaching a caller would also leak the
    fact that this store is SQLite, which is precisely what the facade contract
    exists to hide.
    """
    try:
        yield
    except sqlite3.OperationalError as error:
        text = str(error).lower()
        if any(marker in text for marker in _READONLY_MARKERS):
            raise AdoptError(
                ErrorCode.STORE_READ_ONLY,
                message=f"the store is open read-only: {read_only_reason}",
                hint="Re-open it for writing. A read-only store is never upgraded in "
                "place by the code that found it read-only.",
            ) from error
        raise AdoptError(
            ErrorCode.SCHEMA_MIGRATION_FAILED,
            message=f"the store rejected the operation: {error}",
            hint="Run `adopt store doctor` against this store.",
        ) from error
    except sqlite3.IntegrityError as error:
        raise AdoptError(
            ErrorCode.SCHEMA_MIGRATION_FAILED,
            message=f"the store rejected the write as invalid: {error}",
            hint="A foreign key, unique constraint or CHECK from the canonical schema "
            "refused this row. The manifest is the authority; fix the caller.",
        ) from error


def execute(
    connection: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()
) -> sqlite3.Cursor:
    """Run one statement. Kept here so `sqlite3` types never escape this module."""
    return connection.execute(sql, parameters)
