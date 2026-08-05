"""Fixtures for the G0 suite.

Separate from `tests/conftest.py` because G0's store is a different thing from
S4's: S4 wants the smallest world that makes coverage and freshness decidable,
G0 wants **every** exportable table populated. Sharing one fixture would mean
every coverage test paid for thirty-six tables it never reads.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from adopt_obs import ManualClock
from adopt_schema.manifest import Manifest, load_manifest
from adopt_scope import Scope
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle
from tests.golden.fixture import FIXTURE_START, build_fixture_store


@pytest.fixture(scope="session")
def manifest() -> Manifest:
    """The canonical manifest, loaded once: it is read-only and parsing it is not free."""
    return load_manifest()


@pytest.fixture
def golden_clock() -> ManualClock:
    return ManualClock(FIXTURE_START)


@pytest.fixture
def golden_store(
    tmp_path: Path, golden_clock: ManualClock
) -> Iterator[tuple[SqliteStoreHandle, Scope]]:
    """A store populated across every exportable table, and its resolved scope."""
    handle = open_store(tmp_path / "golden.db", migrate=True, clock=golden_clock)
    scope = build_fixture_store(handle, golden_clock)
    yield handle, scope
    handle.close()
