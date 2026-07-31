"""A failed migration rolls back whole, and the store still opens afterwards.

*Fails when* a migration leaves a store halfway through a schema change -- some
statements applied, a `schema_meta` row written for work that did not happen, or
a transaction left open. *Matters because* there is no down-migration and there
never will be: recovery is older code opening a newer store, which only works if
a store is never in a state no version produced. *No other instrument catches it
because* the happy path leaves exactly the same store whether or not the failure
path is transactional.

`sqlite3` is imported here and not in `adopt_schema`: the `no-raw-sqlite`
contract names that package as a source module, so migrations drive an injected
`MigrationTarget` and the test supplies the SQLite one.
"""

import sqlite3
from pathlib import Path

import pytest

from adopt_const import EXPORT_VERSION, SCHEMA_VERSION
from adopt_obs import AdoptError, ErrorCode
from adopt_schema.emitters import sqlite as sqlite_emitter
from adopt_schema.manifest import load_manifest
from adopt_schema.migrate import (
    BACK_OUT_MARKER,
    SCHEMA_VERSION_MARKER,
    apply,
    new_migration,
    pending,
)


class SqliteTarget:
    """The `MigrationTarget` the store package will provide in the store sprint.

    Atomicity lives here because the dialect's rules do. Two SQLite facts shape
    it, and neither is optional:

    * `executescript` **implicitly commits** any open transaction before running,
      so the script cannot simply be run inside a `BEGIN` the caller opened.
    * `PRAGMA journal_mode = WAL` cannot run inside a transaction at all.

    So PRAGMAs are applied first, in autocommit, and the remaining statements plus
    the `schema_meta` row go in one explicit transaction.
    """

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path, isolation_level=None)

    def current_version(self) -> int:
        row = self._connection.execute("PRAGMA user_version;").fetchone()
        return int(row[0])

    def apply_migration(
        self, sql: str, schema_version: int, export_version: int, written_by: str
    ) -> None:
        pragmas, statements = _split_statements(sql)
        for statement in pragmas:
            self._connection.execute(statement)

        self._connection.execute("BEGIN;")
        try:
            for statement in statements:
                self._connection.execute(statement)
            self._connection.execute(
                "INSERT INTO schema_meta (schema_version, export_version, written_by, written_at) "
                "VALUES (?, ?, ?, ?)",
                (schema_version, export_version, written_by, "2026-07-31T00:00:00.000Z"),
            )
        except Exception:
            self._connection.execute("ROLLBACK;")
            raise
        self._connection.execute("COMMIT;")

    def query(self, sql: str) -> list[tuple[object, ...]]:
        return list(self._connection.execute(sql))

    def close(self) -> None:
        self._connection.close()


def _split_statements(sql: str) -> tuple[list[str], list[str]]:
    """`(pragmas, everything else)`, comments stripped.

    Split by `sqlite3.complete_statement` rather than on `;`, because a table
    purpose in a comment legitimately contains a semicolon.
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
                (pragmas if statement.upper().startswith("PRAGMA") else statements).append(
                    statement
                )
            buffer = ""
    return pragmas, statements


def _repo(tmp_path: Path) -> Path:
    directory = tmp_path / "schema" / "migrations" / "sqlite"
    directory.mkdir(parents=True)
    (directory / "0001__init_v3.sql").write_text(
        sqlite_emitter.emit(load_manifest()), encoding="utf-8", newline="\n"
    )
    return tmp_path


@pytest.mark.unit
def test_the_initial_migration_creates_version_3(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    target = SqliteTarget(tmp_path / "store.db")

    applied = apply(target, root, "sqlite", "test")

    assert [path.name for path in applied] == ["0001__init_v3.sql"]
    assert target.current_version() == SCHEMA_VERSION
    assert target.query("SELECT schema_version, export_version FROM schema_meta;") == [
        (SCHEMA_VERSION, EXPORT_VERSION)
    ]
    target.close()


@pytest.mark.unit
def test_a_failing_migration_rolls_back_and_the_store_still_opens(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    store = tmp_path / "store.db"
    target = SqliteTarget(store)
    apply(target, root, "sqlite", "test")

    broken = root / "schema" / "migrations" / "sqlite" / "0002__broken.sql"
    broken.write_text(
        f"{SCHEMA_VERSION_MARKER} {SCHEMA_VERSION + 1}\n"
        f"{BACK_OUT_MARKER} drop the column this adds.\n"
        "ALTER TABLE firm ADD COLUMN added_ok TEXT;\n"
        "ALTER TABLE nonexistent_table ADD COLUMN boom TEXT;\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(AdoptError) as raised:
        apply(target, root, "sqlite", "test")

    assert raised.value.code is ErrorCode.SCHEMA_MIGRATION_FAILED
    target.close()

    reopened = sqlite3.connect(store)
    try:
        columns = {row[1] for row in reopened.execute("PRAGMA table_info(firm);")}
        # The whole file rolled back: not one statement from it survives.
        assert "added_ok" not in columns
        assert reopened.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    finally:
        reopened.close()


@pytest.mark.unit
def test_a_migration_without_a_back_out_note_is_refused(tmp_path: Path) -> None:
    """The moment the note is needed is the moment nobody has time to write it."""
    root = _repo(tmp_path)
    target = SqliteTarget(tmp_path / "store.db")
    apply(target, root, "sqlite", "test")
    (root / "schema" / "migrations" / "sqlite" / "0002__no_note.sql").write_text(
        f"{SCHEMA_VERSION_MARKER} {SCHEMA_VERSION + 1}\n"
        "ALTER TABLE firm ADD COLUMN whatever TEXT;\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(AdoptError) as raised:
        apply(target, root, "sqlite", "test")

    assert raised.value.code is ErrorCode.SCHEMA_MIGRATION_FAILED
    assert BACK_OUT_MARKER in raised.value.message
    target.close()


@pytest.mark.unit
def test_new_migration_scaffolds_the_next_number_with_an_unfilled_note(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    path = new_migration(root, "sqlite", "add_probe_retry_budget")

    assert path.name == "0002__add_probe_retry_budget.sql"
    body = path.read_text(encoding="utf-8")
    assert BACK_OUT_MARKER in body
    assert f"{SCHEMA_VERSION_MARKER} {SCHEMA_VERSION + 1}" in body
    assert [p.name for p in pending(root, "sqlite", SCHEMA_VERSION)] == [path.name]


@pytest.mark.unit
def test_a_migration_without_a_declared_version_is_refused(tmp_path: Path) -> None:
    """The file number orders migrations; it never says which version they produce."""
    root = _repo(tmp_path)
    (root / "schema" / "migrations" / "sqlite" / "0002__unversioned.sql").write_text(
        f"{BACK_OUT_MARKER} nothing.\nALTER TABLE firm ADD COLUMN whatever TEXT;\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(AdoptError) as raised:
        pending(root, "sqlite", SCHEMA_VERSION)

    assert SCHEMA_VERSION_MARKER in raised.value.message
