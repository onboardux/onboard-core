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
`MigrationTarget`. Until S2 that target was a copy in this file; it is now
`adopt_store.sqlite.SqliteStore`, the one the product actually ships. Testing a
copy would have meant asserting rollback behaviour of code no user runs.
"""

import sqlite3
from pathlib import Path

import pytest

from adopt_const import EXPORT_VERSION, INITIAL_SCHEMA_VERSION, SCHEMA_VERSION
from adopt_obs import AdoptError, ErrorCode
from adopt_schema.emitters import sqlite as sqlite_emitter
from adopt_schema.generate import MIGRATIONS
from adopt_schema.manifest import load_manifest
from adopt_schema.migrate import (
    BACK_OUT_MARKER,
    SCHEMA_VERSION_MARKER,
    apply,
    new_migration,
    pending,
)
from adopt_store.sqlite.store import SqliteStore as SqliteTarget


#: A filename that sorts after every shipped migration, so a test's own broken
#: file is applied last and cannot collide with a real one. Derived rather than
#: written down: the moment a build adds a migration, a hard-coded `0002` here
#: would either collide or silently stop being the last file applied.
def _next_number(root: Path) -> int:
    directory = root / "schema" / "migrations" / "sqlite"
    return 1 + max(int(path.name[:4]) for path in directory.glob("*.sql"))


def _repo(tmp_path: Path) -> Path:
    """A checkout carrying **every** shipped migration, not just the initial one.

    Writing only the initial file would test a store the product never creates
    once a second migration exists, and would leave the version ladder -- the
    thing tranche emission introduced -- unexercised.
    """
    directory = tmp_path / "schema" / "migrations" / "sqlite"
    directory.mkdir(parents=True)
    manifest = load_manifest()
    for version, name in MIGRATIONS:
        (directory / name).write_text(
            sqlite_emitter.emit(manifest, version=version), encoding="utf-8", newline="\n"
        )
    return tmp_path


@pytest.mark.unit
def test_the_shipped_migrations_bring_a_fresh_store_to_the_current_version(
    tmp_path: Path,
) -> None:
    """Every migration applies, in order, and each records the version *it* produced.

    The `schema_meta` assertion is the one that would have caught stamping the
    binary's `SCHEMA_VERSION` on every file: a fresh store would then claim to
    have been at version 4 while the initial migration was running, which is a
    moment that never existed.
    """
    root = _repo(tmp_path)
    target = SqliteTarget(tmp_path / "store.db")

    applied = apply(target, root, "sqlite", "test")

    assert [path.name for path in applied] == [name for _, name in MIGRATIONS]
    assert target.current_version() == SCHEMA_VERSION
    rows = target.query("SELECT schema_version, export_version FROM schema_meta;")
    assert [tuple(row) for row in rows] == [(version, EXPORT_VERSION) for version, _ in MIGRATIONS]
    target.close()


@pytest.mark.unit
def test_a_store_at_the_initial_version_is_migrated_forward_not_refused(
    tmp_path: Path,
) -> None:
    """The upgrade path an existing v3 store actually takes.

    *Fails when* a later tranche is emitted in a way an already-migrated store
    never receives -- the failure the whole tranche design exists to prevent.
    *Matters because* every store written before this build is at the initial
    version, and a table they never get is a `no such table` at first use, four
    layers from its cause (CR-53). *No other instrument catches it because* a
    fresh store gets every migration at once and looks perfect.
    """
    root = _repo(tmp_path)
    directory = root / "schema" / "migrations" / "sqlite"
    later = [(v, n) for v, n in MIGRATIONS if v > INITIAL_SCHEMA_VERSION]
    for _, name in later:
        (directory / name).unlink()

    target = SqliteTarget(tmp_path / "store.db")
    apply(target, root, "sqlite", "test")
    assert target.current_version() == INITIAL_SCHEMA_VERSION
    assert not target.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='coverage_gap';"
    )

    manifest = load_manifest()
    for version, name in later:
        (directory / name).write_text(
            sqlite_emitter.emit(manifest, version=version), encoding="utf-8", newline="\n"
        )

    applied = apply(target, root, "sqlite", "test")

    assert [path.name for path in applied] == [name for _, name in later]
    assert target.current_version() == SCHEMA_VERSION
    assert target.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='coverage_gap';"
    )
    target.close()


@pytest.mark.unit
def test_a_failing_migration_rolls_back_and_the_store_still_opens(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    store = tmp_path / "store.db"
    target = SqliteTarget(store)
    apply(target, root, "sqlite", "test")

    broken = root / "schema" / "migrations" / "sqlite" / f"{_next_number(root):04d}__broken.sql"
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
    name = f"{_next_number(root):04d}__no_note.sql"
    (root / "schema" / "migrations" / "sqlite" / name).write_text(
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

    assert path.name == f"{_next_number(root) - 1:04d}__add_probe_retry_budget.sql"
    body = path.read_text(encoding="utf-8")
    assert BACK_OUT_MARKER in body
    assert f"{SCHEMA_VERSION_MARKER} {SCHEMA_VERSION + 1}" in body
    assert [p.name for p in pending(root, "sqlite", SCHEMA_VERSION)] == [path.name]


@pytest.mark.unit
def test_a_migration_without_a_declared_version_is_refused(tmp_path: Path) -> None:
    """The file number orders migrations; it never says which version they produce."""
    root = _repo(tmp_path)
    name = f"{_next_number(root):04d}__unversioned.sql"
    (root / "schema" / "migrations" / "sqlite" / name).write_text(
        f"{BACK_OUT_MARKER} nothing.\nALTER TABLE firm ADD COLUMN whatever TEXT;\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(AdoptError) as raised:
        pending(root, "sqlite", SCHEMA_VERSION)

    assert SCHEMA_VERSION_MARKER in raised.value.message
