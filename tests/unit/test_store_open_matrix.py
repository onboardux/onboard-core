"""The open matrix: what `open_store` does with every store it can be handed.

*Fails when* a store opens in a mode the version does not permit — a newer store
opened writable, an unmigrated store opened as if it were current, or a store
refusing to open at all when it should open read-only. *Matters because* §7.4
names exactly one recovery for a bad schema deploy, *older code against the newer
store*, and a binary that refuses that store has deleted the only rollback the
schema has. *No other instrument catches it because* every one of these paths
ends with a working connection object, and only the mode and the restriction
distinguish them.

Twelve rows: four store versions (absent, older, current, newer) against the
three ways a caller can ask for one (plain, `migrate`, `read_only`).
"""

import datetime as _dt
import sqlite3
from pathlib import Path

import pytest

from adopt_const import EXPORT_VERSION, SCHEMA_VERSION
from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_store import open_store
from adopt_store.sqlite.connection import read_user_version

_CLOCK_START = _dt.datetime(2026, 8, 3, tzinfo=_dt.UTC)


def _clock() -> ManualClock:
    return ManualClock(_CLOCK_START)


def _store_at(path: Path, version: int) -> Path:
    """A real schema-3 store whose recorded version is forced to `version`.

    Forced with a pragma rather than by generating a different schema: there is
    no version 1 or 2 in this line and there never will be (CR-15), so the only
    honest way to produce an "older" store is to take a current one and claim it
    is older — which is exactly the shape of the store a rolled-back binary meets.
    """
    with open_store(path, migrate=True, clock=_clock()):
        pass
    if version != SCHEMA_VERSION:
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.execute(f"PRAGMA user_version = {version};")
        finally:
            connection.close()
    return path


# -- absent store ---------------------------------------------------------


@pytest.mark.unit
def test_absent_store_with_migrate_is_created_at_the_current_version(tmp_path: Path) -> None:
    with open_store(tmp_path / "store.db", migrate=True, clock=_clock()) as handle:
        assert handle.schema_version == SCHEMA_VERSION
        assert handle.read_only is False
        assert handle.restriction is None
        assert handle.backend.current_version() == SCHEMA_VERSION


@pytest.mark.unit
def test_absent_store_without_migrate_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AdoptError) as raised:
        open_store(tmp_path / "store.db", clock=_clock())
    assert raised.value.code is ErrorCode.SCHEMA_MIGRATION_PENDING


@pytest.mark.unit
def test_absent_store_read_only_is_refused_and_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    with pytest.raises(AdoptError) as raised:
        open_store(path, read_only=True, clock=_clock())
    assert raised.value.code is ErrorCode.SCHEMA_MIGRATION_PENDING
    assert not path.exists(), "a read-only open must never bring a store into being"


# -- current store --------------------------------------------------------


@pytest.mark.unit
def test_current_store_opens_writable(tmp_path: Path) -> None:
    path = _store_at(tmp_path / "store.db", SCHEMA_VERSION)
    with open_store(path, clock=_clock()) as handle:
        assert handle.read_only is False
        assert handle.restriction is None
        assert handle.schema_version == SCHEMA_VERSION


@pytest.mark.unit
def test_current_store_with_migrate_is_a_no_op(tmp_path: Path) -> None:
    path = _store_at(tmp_path / "store.db", SCHEMA_VERSION)
    with open_store(path, migrate=True, clock=_clock()) as handle:
        assert handle.read_only is False
        assert handle.schema_version == SCHEMA_VERSION


@pytest.mark.unit
def test_current_store_read_only_opens_and_refuses_writes(tmp_path: Path) -> None:
    path = _store_at(tmp_path / "store.db", SCHEMA_VERSION)
    with open_store(path, read_only=True, clock=_clock()) as handle:
        assert handle.read_only is True
        assert handle.restriction is None
        with pytest.raises(AdoptError) as raised:
            handle.scope().create_firm(slug="northwind", name="Northwind LLP")
    assert raised.value.code is ErrorCode.STORE_READ_ONLY


# -- older store ----------------------------------------------------------


@pytest.mark.unit
def test_older_store_without_migrate_opens_read_only_and_reports_why(tmp_path: Path) -> None:
    path = _store_at(tmp_path / "store.db", SCHEMA_VERSION - 1)
    with open_store(path, clock=_clock()) as handle:
        assert handle.read_only is True
        assert handle.restriction is not None
        assert handle.restriction.code is ErrorCode.SCHEMA_MIGRATION_PENDING
        assert handle.schema_version == SCHEMA_VERSION - 1


@pytest.mark.unit
def test_older_store_read_only_reports_the_same_restriction(tmp_path: Path) -> None:
    path = _store_at(tmp_path / "store.db", SCHEMA_VERSION - 1)
    with open_store(path, read_only=True, clock=_clock()) as handle:
        assert handle.read_only is True
        assert handle.restriction is not None
        assert handle.restriction.code is ErrorCode.SCHEMA_MIGRATION_PENDING


@pytest.mark.unit
def test_an_empty_file_with_migrate_becomes_a_store(tmp_path: Path) -> None:
    """The only legitimately "older" store in this line is one at version 0 --
    an empty file. CR-15 burned versions 1 and 2, so there is nothing between
    nothing and three."""
    path = tmp_path / "store.db"
    path.touch()

    with open_store(path, migrate=True, clock=_clock()) as handle:
        assert handle.read_only is False
        assert handle.schema_version == SCHEMA_VERSION


@pytest.mark.unit
def test_a_store_claiming_a_version_this_line_never_had_fails_loudly(tmp_path: Path) -> None:
    """There is no version 1 or 2 in this schema line (CR-15), so no migration
    from one exists and none will be written. Reporting that is the correct
    outcome: quietly rewriting `user_version` to make the store openable is the
    action §8's incident card calls data corruption with extra steps."""
    path = _store_at(tmp_path / "store.db", SCHEMA_VERSION - 1)

    with pytest.raises(AdoptError) as raised:
        open_store(path, migrate=True, clock=_clock())

    assert raised.value.code is ErrorCode.SCHEMA_MIGRATION_FAILED
    assert read_user_version(sqlite3.connect(path)) == SCHEMA_VERSION - 1


# -- newer store ----------------------------------------------------------


@pytest.mark.unit
def test_newer_store_opens_read_only_and_reports_why(tmp_path: Path) -> None:
    path = _store_at(tmp_path / "store.db", SCHEMA_VERSION + 1)
    with open_store(path, clock=_clock()) as handle:
        assert handle.read_only is True
        assert handle.restriction is not None
        assert handle.restriction.code is ErrorCode.SCHEMA_VERSION_TOO_NEW
        assert handle.schema_version == SCHEMA_VERSION + 1


@pytest.mark.unit
def test_newer_store_refuses_writes_naming_the_version(tmp_path: Path) -> None:
    path = _store_at(tmp_path / "store.db", SCHEMA_VERSION + 1)
    with open_store(path, clock=_clock()) as handle, pytest.raises(AdoptError) as raised:
        handle.scope().create_firm(slug="northwind", name="Northwind LLP")
    assert raised.value.code is ErrorCode.STORE_READ_ONLY
    assert str(SCHEMA_VERSION + 1) in str(raised.value)


@pytest.mark.unit
def test_read_only_and_migrate_together_are_refused(tmp_path: Path) -> None:
    """Migrating is a write. Accepting both would make one of them a lie."""
    path = _store_at(tmp_path / "store.db", SCHEMA_VERSION)
    with pytest.raises(AdoptError) as raised:
        open_store(path, migrate=True, read_only=True, clock=_clock())
    assert raised.value.code is ErrorCode.STORE_READ_ONLY


# -- schema_meta ----------------------------------------------------------


@pytest.mark.unit
def test_schema_meta_is_appended_on_every_writable_open(tmp_path: Path) -> None:
    """CR-04: rows are appended, never updated — that is what makes the table
    the migration log the source spec's "written and read on every open" implies."""
    path = tmp_path / "store.db"
    with open_store(path, migrate=True, clock=_clock()) as handle:
        first = handle.backend.query("SELECT schema_version, export_version FROM schema_meta;")
    with open_store(path, clock=_clock()) as handle:
        second = handle.backend.query("SELECT schema_version, export_version FROM schema_meta;")

    assert len(first) == 1
    assert len(second) == len(first) + 1
    assert [tuple(row) for row in second] == [(SCHEMA_VERSION, EXPORT_VERSION)] * len(second)
