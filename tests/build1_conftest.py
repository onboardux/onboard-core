"""Build 1 fixtures, shared by the unit, property and integration suites.

Imported by `tests/conftest.py` rather than declared as a second `conftest.py`,
so there is one fixture namespace and `pytest --fixtures` shows everything in one
place.

**`store_fingerprint` is the instrument behind "zero writes".** `02` §10 C8 says
every scope abort must write nothing, and the honest way to assert that is to
photograph the store before and after and require the photographs to match. A
row count would miss an in-place edit; the file's bytes would miss nothing but
would also fail on WAL churn that changes no row. So the fingerprint is every
row of every table the manifest declares, ordered, digested -- which fails on an
insert, an update and a delete alike, and on nothing else.
"""

import hashlib
import json
from collections.abc import Callable

import pytest
from adopt_map.ports import ScopeLookupRecords, SurfaceAuxRecords

from adopt_model import MODEL_FOR_TABLE
from adopt_store.api import SqliteStoreHandle


@pytest.fixture
def scope_records(s4_store: SqliteStoreHandle) -> ScopeLookupRecords:
    """The read port, satisfied structurally by Build 0's export records (OD-2)."""
    return s4_store.export_records()


@pytest.fixture
def aux_records(s4_store: SqliteStoreHandle) -> SurfaceAuxRecords:
    """The write port for the four tables with no facade (OD-1)."""
    return s4_store.import_records()


@pytest.fixture
def store_fingerprint(s4_store: SqliteStoreHandle) -> Callable[[], str]:
    """A digest over every row of every canonical table.

    Sensitive to an insert, an update and a delete; insensitive to WAL churn,
    page reordering and connection state -- which is the difference between an
    instrument and a file hash.
    """

    def _fingerprint() -> str:
        digest = hashlib.blake2b(digest_size=16)
        records = s4_store.export_records()
        for table in sorted(MODEL_FOR_TABLE):
            rows = records.table_rows(table, MODEL_FOR_TABLE[table])
            rendered = sorted(
                json.dumps(row.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
                for row in rows
            )
            digest.update(f"{table}:{len(rendered)}\n".encode())
            for line in rendered:
                digest.update(line.encode() + b"\n")
        return digest.hexdigest()

    return _fingerprint
