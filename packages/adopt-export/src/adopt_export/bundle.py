"""The bundle layout, its manifest shape, and the canonical rendering rules.

Contracts §11. Everything here exists to make one sentence true --
*export → import → export produces byte-identical table files* -- and that
sentence is only true if every rendering decision is made in exactly one place.

**One JSON rule, at every nesting level:** no ASCII escaping, no spaces, keys
sorted. Sorting applies to the row object and to anything nested inside a `json`
column alike, because a rule with an exception is a rule someone applies to the
outer object and forgets on the inner one -- and the inner one is
`permitted_outbound_categories`, the only `json` column schema version 3 has.

**Timestamps are rendered here, not by pydantic.** `model_dump(mode="json")`
would emit whatever the library's default happens to be; contracts §1.2 requires
millisecond precision with a `Z` suffix, and a bundle written before a pydantic
upgrade must equal one written after it.

**There is no `blobs/` directory at schema version 3.** No exportable table
declares a blob reference -- the only one in the pack is `agent_run.output_ref`,
which lives in the runtime annex (§12) and is never exported. The manifest still
carries the `blobs` block, reporting zero, which is what §11's *"present only
when referenced"* means for this version. Inventing a collection mechanism for a
source that does not exist would be inventing its bugs too.
"""

import datetime as _dt
import hashlib
import json as _json
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from adopt_obs import format_timestamp

__all__ = [
    "BLOBS_DIRNAME",
    "MANIFEST_FILENAME",
    "SCHEMA_FILENAME",
    "TABLES_DIRNAME",
    "TABLE_SUFFIX",
    "BlobSummary",
    "BundleManifest",
    "BundleScope",
    "TableEntry",
    "canonical_json",
    "read_text",
    "row_object",
    "sha256_of_bytes",
    "table_relative_path",
]

MANIFEST_FILENAME: Final[str] = "manifest.json"
SCHEMA_FILENAME: Final[str] = "export.schema.json"
TABLES_DIRNAME: Final[str] = "tables"
BLOBS_DIRNAME: Final[str] = "blobs"
TABLE_SUFFIX: Final[str] = ".ndjson"

#: NDJSON is line-delimited by definition, and the delimiter is pinned to `\n`
#: so a Windows checkout and a Linux runner produce the same bytes -- the same
#: reason `adopt_schema.generate` opens its targets with `newline="\n"`.
LINE_SEPARATOR: Final[str] = "\n"
ENCODING: Final[str] = "utf-8"


def canonical_json(value: object) -> str:
    """The one rendering. Sorted keys, no spaces, no ASCII escaping."""
    return _json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_of_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def table_relative_path(table: str) -> str:
    """`tables/<table>.ndjson`, as it appears in the manifest and on disk."""
    return f"{TABLES_DIRNAME}/{table}{TABLE_SUFFIX}"


def row_object(model: BaseModel) -> dict[str, Any]:
    """One validated row as the JSON object a bundle line carries.

    Dumped **by alias**, because the alias is the canonical column name: one
    column is called `class`, a Python keyword, whose field is therefore
    `class_`. A bundle carrying `class_` would not validate against
    `export.schema.json`, which is generated from the manifest and names columns.
    """
    return {
        name: format_timestamp(value) if isinstance(value, _dt.datetime) else value
        for name, value in model.model_dump(by_alias=True).items()
    }


class _Strict(BaseModel):
    """Closed by construction: an unknown key in a bundle manifest is a defect."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class BundleScope(_Strict):
    """The scope a bundle belongs to, **as slugs** so it resolves without ULIDs."""

    firm: str
    engagement: str


class TableEntry(_Strict):
    name: str
    rows: int
    sha256: str
    #: Columns the manifest has retired at this schema version. Present and empty
    #: rather than omitted, because §11 requires the field to distinguish
    #: "absent" from "null for every row" -- and an absent list says neither.
    omitted_columns: list[str]


class BlobSummary(_Strict):
    count: int
    total_bytes: int


class BundleManifest(_Strict):
    """`manifest.json`, exactly the §11 keys and no others."""

    export_version: int
    schema_version: int
    scope: BundleScope
    written_by: str
    #: A string, not a datetime: it is rendered once by the writer and compared
    #: as text thereafter. Re-parsing it into a datetime only to render it again
    #: would put a second timestamp format in the round trip.
    written_at: str
    tables: list[TableEntry]
    blobs: BlobSummary

    def entry_for(self, table: str) -> TableEntry | None:
        return next((entry for entry in self.tables if entry.name == table), None)

    def to_bytes(self) -> bytes:
        return (canonical_json(self.model_dump()) + LINE_SEPARATOR).encode(ENCODING)


def read_text(path: Path) -> str:
    """Read a bundle file as text without letting the platform rewrite newlines.

    Decoded from bytes rather than read as text: `Path.read_text` opens in text
    mode, and text mode on Windows turns a `\\r\\n` in a bundle into a `\\n` on
    the way in. A bundle's bytes are the contract, so they are never translated.
    """
    return path.read_bytes().decode(ENCODING)
