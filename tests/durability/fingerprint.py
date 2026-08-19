"""A digest over every row of every canonical table.

Extracted so the `01` N10 drill's child and parent compute it the same way. A
child that fingerprinted differently from its parent would report a difference
on every run and the drill would be noise; a child that fingerprinted the *same
way but separately* would drift the first time one copy was edited.

Sensitive to an insert, an update and a delete; insensitive to WAL churn, page
reordering and connection state -- which is the difference between an instrument
and a file hash. `tests/build1_conftest.py`'s `store_fingerprint` fixture is the
same function bound to the S4 store fixture.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from adopt_model import MODEL_FOR_TABLE

if TYPE_CHECKING:  # pragma: no cover - typing only
    from adopt_store.api import SqliteStoreHandle

#: `schema_meta` gains a row **on every `open_store`**, recording when the store
#: was opened and by which version. So it is a property of who has looked at the
#: file, not of what any run wrote -- and a digest including it changes when the
#: observer opens the store to take the measurement.
#:
#: That is exactly what it did: the N10 drill's first run failed at *every* kill
#: point, including statement 1 where nothing had been written, because the child
#: fingerprinted through its own handle and the parent added a row by opening the
#: file to check. The store was unchanged and the instrument said otherwise.
#: Excluded here rather than in `MODEL_FOR_TABLE`, which is generated.
_OPEN_AUDIT_TABLES = frozenset({"schema_meta"})


def fingerprint_store(handle: SqliteStoreHandle) -> str:
    """Digest every row of every canonical content table in a stable order."""
    digest = hashlib.blake2b(digest_size=16)
    records = handle.export_records()
    for table in sorted(set(MODEL_FOR_TABLE) - _OPEN_AUDIT_TABLES):
        rows = records.table_rows(table, MODEL_FOR_TABLE[table])
        rendered = sorted(
            json.dumps(row.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            for row in rows
        )
        digest.update(f"{table}:{len(rendered)}\n".encode())
        for line in rendered:
            digest.update(line.encode() + b"\n")
    return digest.hexdigest()
