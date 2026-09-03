"""Where the refresh file-state snapshot is kept -- derived, annex-resident.

The **pure half** is `adopt_map.filestate`: hashing a file and comparing two
snapshots need no database, and `no-raw-sqlite` follows indirect chains into
`adopt_cli`, so a CLI module reaching through this package for a pure function
would break a contract to call something that touches no store. This module is
only the persistence.

**Scoped, because one store can hold several environments.** The key is
`(scope_ref, path)`: two environments of one system are separate working trees
in the general case, and a shared path key would make each refresh look like a
wholesale rewrite of the other's.

**Absence is reported, never assumed.** `load` returns `None` when the scope has
no snapshot -- the first refresh of any store, and any run after the annex was
lost. The caller then reports RENDER-ONLY as *unavailable* rather than as zero,
because "no cosmetic changes" and "we could not tell" are different statements
and printing the first for the second is a measurement nobody took.
"""

import datetime as _dt
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, runtime_checkable

from adopt_obs import format_timestamp
from adopt_store.annex.sqlite_annex import annex_path, connect_annex

__all__ = ["FileStateRow", "SqliteFileStateRecords", "open_file_state"]


@runtime_checkable
class FileStateRow(Protocol):
    """One hashed path, as this module reads it.

    **Structural, not `adopt_map.filestate.FileState` imported.** The pure half
    of this pair lives in `adopt_map`, and annotating against it would make
    `adopt-store` depend on `adopt-map` -- eleven extractors pulled into any
    consumer of the store, to name two strings. `first-party-deps` caught
    exactly that: the import was real, undeclared, and invisible to every test,
    because `uv sync --all-packages` supplies every distribution whatever one of
    them declares. The gap only appears to somebody installing a subset.

    So the two fields are read structurally, which is the reason
    `ChangeFacade.ClassifiedChange` is a protocol too: the value crosses a
    package boundary, and re-declaring it on this side would be one more place
    for a field name to be transcribed wrong.
    """

    @property
    def path(self) -> str: ...

    @property
    def sha256(self) -> str: ...


class SqliteFileStateRecords:
    """Load and replace one scope's snapshot. There is no partial update.

    Replacement is `DELETE` + `INSERT` within one transaction, because a
    snapshot is a statement about a whole tree at a moment: merging a new run's
    rows into an old run's would leave deleted files present forever, comparing
    equal on every future run -- so a path later recreated with different content
    would be measured against a tree that no longer exists.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def load(self, scope_ref: str) -> Mapping[str, str] | None:
        """`path -> sha256` for the scope, or `None` if it has no snapshot."""
        rows = self._connection.execute(
            "SELECT path, sha256 FROM refresh_file_state WHERE scope_ref = ?", (scope_ref,)
        ).fetchall()
        if not rows:
            return None
        return {str(row["path"]): str(row["sha256"]) for row in rows}

    def replace(
        self, scope_ref: str, states: Iterable[FileStateRow], *, observed_at: _dt.datetime
    ) -> int:
        """Replace the scope's snapshot wholesale. Returns the row count written."""
        stamp = format_timestamp(observed_at)
        payload = [(scope_ref, state.path, state.sha256, stamp) for state in states]
        with self._connection:
            self._connection.execute(
                "DELETE FROM refresh_file_state WHERE scope_ref = ?", (scope_ref,)
            )
            self._connection.executemany(
                "INSERT INTO refresh_file_state (scope_ref, path, sha256, observed_at) "
                "VALUES (?, ?, ?, ?)",
                payload,
            )
        return len(payload)


@contextmanager
def open_file_state(
    store_path: Path, *, repo_root: Path | None = None
) -> Iterator[SqliteFileStateRecords]:
    """Open the annex beside `store_path` and yield the file-state port.

    A context manager per port over one annex file, which is the pattern
    `open_annex` and `open_search` already establish: one function knows how to
    open the annex, and each port takes the connection it needs.
    """
    connection = connect_annex(annex_path(store_path), repo_root=repo_root)
    try:
        yield SqliteFileStateRecords(connection)
    finally:
        connection.close()
