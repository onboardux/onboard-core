"""`write_bundle` — the export half of contracts §11.

Three decisions live here rather than in whichever store answered, and each is
the reason the byte-identical round trip is testable at all:

* **Row order.** The writer sorts by the manifest's primary key over the
  *rendered* values, so the order is byte-wise ascending as §11 requires and is a
  pure function of (manifest, rows). Pushing `ORDER BY` into the realization
  would make the emitted bytes depend on a collation, and the Postgres
  realization would have to reproduce SQLite's exactly, unwritten and untested.
* **The scope refusal.** `manifest.json` names one firm and one engagement. A
  store holding two is refused before anything is created, because a bundle
  labelled with a scope half its rows do not belong to is not a wrong answer, it
  is a *different referent* -- and nothing downstream can detect it (CR-37).
* **What counts as exportable.** The writer iterates
  `Manifest.exportable_tables()`. Runtime-annex tables are excluded **by
  construction**: they are not in the canonical manifest at all, so there is no
  filter to forget (contracts §12).

The writer opens no socket and writes nothing outside `target`.
"""

import datetime as _dt
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from adopt_const import EXPORT_NDJSON_MAX_LINE_BYTES, EXPORT_VERSION, SCHEMA_VERSION
from adopt_export.bundle import (
    ENCODING,
    LINE_SEPARATOR,
    MANIFEST_FILENAME,
    SCHEMA_FILENAME,
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
from adopt_export.ports import ExportRecords
from adopt_model import MODEL_FOR_TABLE
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, format_timestamp, get_logger
from adopt_schema.manifest import Manifest, Table, canonical_path, load_manifest

__all__ = ["default_schema_source", "write_bundle"]

_LOGGER: Final = get_logger("adopt_export")

#: No exportable table at schema version 3 references a blob, so the block is
#: reported as empty rather than assembled. See `bundle` for the full argument.
_NO_BLOBS: Final[BlobSummary] = BlobSummary(count=0, total_bytes=0)


def default_schema_source() -> Path:
    """Where `export.schema.json` is read from, beside the manifest it came from.

    Resolved through `canonical_path()` so the `ADOPT_SCHEMA_MANIFEST` override
    moves both together. A bundle carrying a schema from one checkout and rows
    from another is a bundle that validates against the wrong contract.
    """
    return canonical_path().parent / SCHEMA_FILENAME


def _sole_slug(kind: str, slugs: Sequence[str]) -> str:
    distinct = sorted(set(slugs))
    if len(distinct) == 1:
        return distinct[0]
    if not distinct:
        raise AdoptError(
            ErrorCode.EXPORT_SCOPE_AMBIGUOUS,
            message=f"the store holds no {kind}, and a bundle names one",
            hint="Create the scope before exporting. An empty store has no scope to "
            "record, and a bundle whose scope is guessed is a bundle nothing can trust.",
        )
    raise AdoptError(
        ErrorCode.EXPORT_SCOPE_AMBIGUOUS,
        message=f"the store holds {len(distinct)} {kind}s ({', '.join(distinct)}) "
        f"and contracts §11 names one",
        hint="Export one scope per bundle. Labelling a bundle with one of several "
        "scopes is not a wrong answer but a different referent, and no consumer of "
        "the bundle can detect it.",
    )


def _omitted_columns(table: Table) -> list[str]:
    """Columns the manifest has retired at or below the current schema version.

    Empty at version 3, and computed rather than hard-coded so it stops being
    empty by itself the first time a column is retired -- which is the only way
    §11's "absent versus null for every row" distinction stays true.
    """
    return sorted(
        column.name
        for column in table.columns
        if column.retired_in_version is not None and column.retired_in_version <= SCHEMA_VERSION
    )


def _sort_key(row: dict[str, Any], primary_key: Sequence[str]) -> tuple[str, ...]:
    """Byte-wise ascending over the rendered primary key (§11).

    Rendered, not raw: the values compared are the ones about to be written, so
    the file's order and the file's bytes cannot disagree. Every canonical
    primary-key column is a `id`, `slug`, `text` or `ts`, all of which render as
    strings, so this is a total order rather than a best effort.
    """
    return tuple(str(row[column]) for column in primary_key)


def _render_table(table_name: str, table: Table, records: ExportRecords) -> tuple[bytes, int]:
    """One table's file content and its row count. Empty tables render empty."""
    models = records.table_rows(table_name, MODEL_FOR_TABLE[table_name])
    rows = sorted(
        (row_object(model) for model in models),
        key=lambda row: _sort_key(row, table.primary_key),
    )

    lines: list[str] = []
    for index, row in enumerate(rows):
        line = canonical_json(row)
        encoded = line.encode(ENCODING)
        if len(encoded) > EXPORT_NDJSON_MAX_LINE_BYTES:
            raise AdoptError(
                ErrorCode.EXPORT_BUNDLE_MALFORMED,
                message=f"{table_name} row {index} renders to {len(encoded)} bytes, over the "
                f"{EXPORT_NDJSON_MAX_LINE_BYTES}-byte line limit",
                hint="The reader refuses a line this long, so writing one would produce a "
                "bundle this version cannot import. The row is too large for the format.",
            )
        lines.append(line)

    content = "".join(line + LINE_SEPARATOR for line in lines)
    return content.encode(ENCODING), len(rows)


def write_bundle(
    records: ExportRecords,
    target: Path,
    *,
    written_by: str,
    manifest: Manifest | None = None,
    clock: Clock | None = None,
    schema_source: Path | None = None,
) -> BundleManifest:
    """Write a bundle to ``target`` and return the manifest it recorded.

    Args:
        records: The read port. Never written through.
        target: The bundle directory. Created if absent; refused if non-empty.
        written_by: Provenance for `manifest.json`, e.g. ``adopt-core/0.3.0``.
        manifest: The canonical manifest. Loaded if not supplied.
        clock: Injected clock; tests pass `ManualClock`.
        schema_source: Where to copy `export.schema.json` from.

    Raises:
        AdoptError: ``EXPORT_SCOPE_AMBIGUOUS`` when the store does not hold
            exactly one firm and one engagement. ``EXPORT_TARGET_NOT_EMPTY``
            when ``target`` already holds anything. ``EXPORT_BUNDLE_MALFORMED``
            when a row would render past the line limit.
    """
    loaded = manifest if manifest is not None else load_manifest()
    ticking = clock if clock is not None else SystemClock()

    # Scope first, before anything exists on disk: a refusal that has already
    # created half a bundle leaves a directory an operator has to reason about.
    scope = BundleScope(
        firm=_sole_slug("firm", records.firm_slugs()),
        engagement=_sole_slug("engagement", records.engagement_slugs()),
    )

    if target.exists() and any(target.iterdir()):
        raise AdoptError(
            ErrorCode.EXPORT_TARGET_NOT_EMPTY,
            message=f"{target} is not empty",
            hint="Export writes a whole bundle or none of one. Point it at a new "
            "directory rather than merging into an existing bundle, whose manifest "
            "would then describe files it did not write.",
        )

    tables_dir = target / TABLES_DIRNAME
    tables_dir.mkdir(parents=True, exist_ok=True)

    entries: list[TableEntry] = []
    for table_name, table in loaded.exportable_tables():
        content, count = _render_table(table_name, table, records)
        (target / table_relative_path(table_name)).write_bytes(content)
        entries.append(
            TableEntry(
                name=table_name,
                rows=count,
                sha256=sha256_of_bytes(content),
                omitted_columns=_omitted_columns(table),
            )
        )

    source = schema_source if schema_source is not None else default_schema_source()
    (target / SCHEMA_FILENAME).write_bytes(source.read_bytes())

    bundle_manifest = BundleManifest(
        export_version=EXPORT_VERSION,
        schema_version=loaded.schema_version,
        scope=scope,
        written_by=written_by,
        written_at=format_timestamp(_as_utc(ticking.now())),
        tables=entries,
        blobs=_NO_BLOBS,
    )
    # Written last: a manifest present means every file it names is present too,
    # so a reader never has to distinguish a finished bundle from an abandoned one.
    (target / MANIFEST_FILENAME).write_bytes(bundle_manifest.to_bytes())

    _LOGGER.info(
        "export.bundle_written",
        tables=len(entries),
        rows=sum(entry.rows for entry in entries),
        export_version=EXPORT_VERSION,
        schema_version=loaded.schema_version,
    )
    return bundle_manifest


def _as_utc(moment: _dt.datetime) -> _dt.datetime:
    return moment.astimezone(_dt.UTC)
