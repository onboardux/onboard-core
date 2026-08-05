"""Export bundle writer and reader — contracts §11, implementation spec §4.10.

The portability promise in executable form: *export → import → export produces
byte-identical table files*, and every identity in the second export resolves by
URI alone with no ULID lookup. That property is gate **G0** and Build 0's
definition of done, condition 2.

Invariants this package holds:

* **No network.** Nothing here opens a socket.
* **Writes confined to the target directory.** The writer creates the bundle and
  nothing else; the reader writes only through the `ImportRecords` port.
* **Runtime-annex tables are excluded by construction**, not by a filter: they
  are not in the canonical manifest, and the manifest is what the writer
  iterates (contracts §12).
* **No dialect.** This package declares its own storage ports and imports no
  store, so `no-raw-sqlite` holds and a Postgres realization changes nothing
  here.
"""

from adopt_export.bundle import (
    BLOBS_DIRNAME,
    MANIFEST_FILENAME,
    SCHEMA_FILENAME,
    TABLE_SUFFIX,
    TABLES_DIRNAME,
    BlobSummary,
    BundleManifest,
    BundleScope,
    TableEntry,
    canonical_json,
    row_object,
    sha256_of_bytes,
    table_relative_path,
)
from adopt_export.ports import ExportRecords, ImportRecords
from adopt_export.reader import apply_bundle, read_bundle
from adopt_export.roundtrip import table_files, verify_roundtrip
from adopt_export.writer import write_bundle

__all__ = [
    "BLOBS_DIRNAME",
    "MANIFEST_FILENAME",
    "SCHEMA_FILENAME",
    "TABLES_DIRNAME",
    "TABLE_SUFFIX",
    "BlobSummary",
    "BundleManifest",
    "BundleScope",
    "ExportRecords",
    "ImportRecords",
    "TableEntry",
    "apply_bundle",
    "canonical_json",
    "read_bundle",
    "row_object",
    "sha256_of_bytes",
    "table_files",
    "table_relative_path",
    "verify_roundtrip",
    "write_bundle",
]
