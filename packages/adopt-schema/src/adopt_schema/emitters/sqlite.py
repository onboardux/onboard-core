"""The SQLite realization: contracts §3, generated.

What this emits **is** a migration file -- `0001__init_v3.sql` for the initial
version, one file per version after it. There is no second copy of the DDL that a
migration is derived from, because two copies of a schema are two schemas as soon
as someone edits one of them.

**One version per call, and that is what keeps history stable.** `emit` renders
the tranche of tables a single version introduced (`Manifest.tranche`), so adding
a table at version 4 leaves the version-3 file byte-identical. Regenerating one
whole-schema file instead would rewrite a migration that has already run on real
stores -- and the stores that ran it would have no pending migration and a
missing table.

Tables are emitted in foreign-key topological order (CR-18). SQLite would accept
a forward `REFERENCES` at creation and then fail at first insert, which is the
same defect discovered much later and much further from its cause. A later
tranche may reference a table an earlier one created; ordering holds across
files because the tranches apply in version order.
"""

from typing import Final

from adopt_const import INITIAL_SCHEMA_VERSION
from adopt_schema.emitters._shared import (
    GENERATED_NOTICE,
    enum_check,
    quote_sql_literal,
    resolve_enum,
)
from adopt_schema.manifest import Column, Index, Manifest, Table

__all__ = ["emit"]

#: contracts §2.3, manifest type -> SQLite storage class.
TYPE_MAP: Final[dict[str, str]] = {
    "id": "TEXT",
    "slug": "TEXT",
    "uri": "TEXT",
    "text": "TEXT",
    "md": "TEXT",
    "json": "TEXT",
    "int": "INTEGER",
    "real": "REAL",
    "bool": "INTEGER",
    "ts": "TEXT",
}

BACK_OUT: Final[str] = (
    "-- back-out: none. This is the initial creation of schema version 3, so the\n"
    "-- back-out is to discard the store file and create a new one. There is no\n"
    "-- in-place reversal, and none will be written: recovery from a newer store\n"
    "-- is older code opening it read-only, which is why additive-only is enforced\n"
    "-- mechanically rather than trusted."
)


def _back_out(version: int) -> str:
    """The mandatory back-out note. `migrate.apply` refuses a file without one."""
    if version <= INITIAL_SCHEMA_VERSION:
        return BACK_OUT
    return (
        f"-- back-out: none is needed, and none is possible in place. This migration\n"
        f"-- only CREATEs tables introduced at schema version {version}; it alters nothing\n"
        f"-- that existed before, so every earlier query reads exactly what it read\n"
        f"-- before it ran. A binary too old for version {version} opens the store read-only\n"
        f"-- with SCHEMA_VERSION_TOO_NEW rather than misreading it -- that read-only open\n"
        f"-- IS the recovery path. Removal is `retired_in_version` in\n"
        f"-- schema/canonical.yaml, which keeps the physical object and writes it NULL."
    )


def _version_marker(version: int) -> str:
    """What schema version this file produces -- the file number is not a version."""
    return f"-- schema-version: {version}"


def _sql_type(manifest: Manifest, column: Column) -> str:
    enum = resolve_enum(manifest, column)
    if enum is not None:
        return "INTEGER" if enum.is_integer else "TEXT"
    return TYPE_MAP[column.type]


def _column_sql(manifest: Manifest, table: Table, column: Column) -> str:
    parts = [column.name, _sql_type(manifest, column)]

    single_column_key = table.primary_key == [column.name]
    if single_column_key:
        parts.append("PRIMARY KEY")
    elif not column.nullable:
        parts.append("NOT NULL")

    if column.default is not None:
        parts.append(f"DEFAULT {quote_sql_literal(column.default)}")
    if column.unique:
        parts.append("UNIQUE")
    if column.references is not None:
        target_table, target_column = column.reference_parts or ("", "")
        parts.append(f"REFERENCES {target_table}({target_column})")

    enum = resolve_enum(manifest, column)
    if enum is not None:
        parts.append(enum_check(column.name, enum))
    return " ".join(parts)


def _index_sql(table_name: str, index: Index) -> str:
    kind = "CREATE UNIQUE INDEX" if index.unique else "CREATE INDEX"
    return f"{kind} {index.name} ON {table_name}({', '.join(index.columns)});"


def _table_sql(manifest: Manifest, name: str, table: Table) -> str:
    lines = [f"-- {table.purpose}", f"CREATE TABLE {name} ("]
    body = [f"  {_column_sql(manifest, table, column)}" for column in table.columns]

    if len(table.primary_key) > 1:
        body.append(f"  PRIMARY KEY ({', '.join(table.primary_key)})")
    body.extend(f"  UNIQUE ({', '.join(group)})" for group in table.unique)

    lines.append(",\n".join(body))
    lines.append(");")
    lines.extend(_index_sql(name, index) for index in table.indexes)
    return "\n".join(lines)


def emit(manifest: Manifest, *, version: int) -> str:
    """The SQLite DDL for one schema version, as one migration file.

    Args:
        version: The schema version this file produces. Selects the tranche of
            tables (see `Manifest.tranche`), the `user_version` it sets, and the
            `-- schema-version:` marker `migrate.pending` reads.
    """
    header = "\n".join(f"-- {line}" for line in GENERATED_NOTICE.splitlines())
    # `user_version` is journaled and `apply_migration` runs it inside the
    # migration transaction, so a failed migration leaves the version untouched.
    # `journal_mode` and `foreign_keys` are connection/file state that cannot be
    # set inside a transaction at all, and are the initial file's business only:
    # re-asserting them in a later tranche would put a statement that must run in
    # autocommit into every future migration for no effect.
    pragmas = [f"PRAGMA user_version = {version};"]
    if version <= INITIAL_SCHEMA_VERSION:
        pragmas += ["PRAGMA journal_mode = WAL;", "PRAGMA foreign_keys = ON;"]

    tables = [_table_sql(manifest, name, table) for name, table in manifest.tranche(version)]
    return (
        "\n\n".join(
            [header, _version_marker(version), _back_out(version), "\n".join(pragmas), *tables]
        )
        + "\n"
    )
