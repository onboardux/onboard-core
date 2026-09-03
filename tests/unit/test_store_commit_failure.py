"""A `COMMIT` that fails rolls back and leaves the store usable.

*Fails when* `SqliteStore.transaction()` lets a failing `COMMIT` escape without
rolling back or without resetting its nesting depth. *Matters because* the
connection then believes it is outside a transaction while SQLite believes it is
inside one: every later `transaction()` joins a transaction nobody opened, and
its writes commit whenever some unrelated caller happens to finish. *No other
instrument catches it because* `COMMIT` is the one statement in the class that
is not an ordinary write -- every happy-path test drives it successfully, and the
CLI's one-run-per-process shape hides the consequence behind a closing
connection.

BACKLOG **B-07 item 1**, owner-decided 2026-08-14: *"Do this before any plane
work resumes."* The exposure it names is a long-lived process holding one
connection across units of work -- `adopt-plane`'s shape, and Build 7 is the
build that makes it the norm.

The injection replaces `execute` on the connection rather than patching the
store, because the defect is about what the store does when the **driver**
refuses, and a fake store would be asserting the behaviour of a copy.
"""

import sqlite3
from pathlib import Path

import pytest

from adopt_obs import AdoptError, ErrorCode
from adopt_store.sqlite.store import SqliteStore


class _DenyCommit:
    """Wraps a real connection and refuses exactly one statement.

    `ROLLBACK` is passed through so the test can assert the store issued it;
    counting is what turns "the code has a rollback line" into "the rollback
    ran".
    """

    def __init__(self, connection: sqlite3.Connection, deny: str) -> None:
        self._connection = connection
        self._deny = deny
        self.rollbacks = 0

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> object:
        statement = sql.strip().rstrip(";").upper()
        if statement == "ROLLBACK":
            self.rollbacks += 1
        if statement == self._deny:
            raise sqlite3.OperationalError(f"injected failure: {self._deny}")
        return self._connection.execute(sql, parameters)

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)


def _store(tmp_path: Path) -> SqliteStore:
    store = SqliteStore(tmp_path / "store.db")
    store.execute("CREATE TABLE probe_rows (id INTEGER PRIMARY KEY)")
    return store


@pytest.mark.unit
def test_a_failed_commit_rolls_back_and_leaves_the_store_usable(tmp_path: Path) -> None:
    """The defect sentence, asserted in four places.

    Depth is the one an operator never sees and the one that corrupts every
    later unit of work, so it is asserted directly rather than inferred from the
    behaviour it produces.
    """
    store = _store(tmp_path)
    real = store._connection
    denied = _DenyCommit(real, "COMMIT")
    store._connection = denied  # type: ignore[assignment]

    with (
        pytest.raises(sqlite3.OperationalError, match="injected failure: COMMIT"),
        store.transaction(),
    ):
        store.execute("INSERT INTO probe_rows (id) VALUES (1)")

    # 1. The rollback ran.
    assert denied.rollbacks == 1
    # 2. The store no longer believes it is inside a transaction.
    assert store._depth == 0
    # 3. The write did not survive.
    store._connection = real  # type: ignore[assignment]
    assert store.query("SELECT id FROM probe_rows") == []
    # 4. The connection still works -- the next unit of work is not poisoned.
    with store.transaction():
        store.execute("INSERT INTO probe_rows (id) VALUES (2)")
    assert [row["id"] for row in store.query("SELECT id FROM probe_rows")] == [2]
    store.close()


@pytest.mark.unit
def test_a_failed_rollback_does_not_replace_the_failure_that_caused_it(tmp_path: Path) -> None:
    """The caller needs the commit error, not a complaint about rollback.

    SQLite rolls back for us on some `COMMIT` failures, and then `ROLLBACK`
    raises "cannot rollback - no transaction is active". Surfacing that instead
    would send an operator looking at the wrong statement.
    """
    store = _store(tmp_path)
    real = store._connection

    class _DenyBoth(_DenyCommit):
        def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> object:
            statement = sql.strip().rstrip(";").upper()
            if statement in {"COMMIT", "ROLLBACK"}:
                if statement == "ROLLBACK":
                    self.rollbacks += 1
                raise sqlite3.OperationalError(f"injected failure: {statement}")
            return self._connection.execute(sql, parameters)

    denied = _DenyBoth(real, "COMMIT")
    store._connection = denied  # type: ignore[assignment]

    with (
        pytest.raises(sqlite3.OperationalError, match="injected failure: COMMIT"),
        store.transaction(),
    ):
        store.execute("INSERT INTO probe_rows (id) VALUES (1)")

    assert denied.rollbacks == 1
    assert store._depth == 0
    store._connection = real  # type: ignore[assignment]
    store.close()


@pytest.mark.unit
def test_a_failed_migration_commit_leaves_nothing_behind(tmp_path: Path) -> None:
    """`apply_migration` carries the same shape and the same promise.

    Its docstring says a failed migration leaves nothing behind; that is only
    true if the `COMMIT` is inside the `try` here too. Asserted separately
    because this method keeps its own transaction and does not go through
    `transaction()`.

    The error is typed here and raw in `transaction()` because this whole method
    runs inside `translate_sqlite_error` -- a difference worth seeing in the
    tests rather than discovering from a caller.
    """
    store = SqliteStore(tmp_path / "migrated.db")
    real = store._connection
    denied = _DenyCommit(real, "COMMIT")
    store._connection = denied  # type: ignore[assignment]

    with pytest.raises(AdoptError) as raised:
        store.apply_migration(
            "CREATE TABLE schema_meta (schema_version INTEGER, export_version INTEGER, "
            "written_by TEXT, written_at TEXT);",
            schema_version=1,
            export_version=1,
            written_by="test",
        )

    assert raised.value.code is ErrorCode.SCHEMA_MIGRATION_FAILED
    assert denied.rollbacks == 1
    store._connection = real  # type: ignore[assignment]
    assert store.is_empty()
    store.close()
