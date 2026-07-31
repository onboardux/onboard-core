"""The SQLite realization: contracts §3, generated.

The file this emits **is** `schema/migrations/sqlite/0001__init_v3.sql`. There is
no second copy of the DDL that a migration is derived from, because two copies of
a schema are two schemas as soon as someone edits one of them.

Tables are emitted in foreign-key topological order (CR-18). SQLite would accept
a forward `REFERENCES` at creation and then fail at first insert, which is the
same defect discovered much later and much further from its cause.
"""

from typing import Final

from adopt_const import SCHEMA_VERSION
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


def _version_marker() -> str:
    """What schema version this file produces -- the file number is not a version."""
    return f"-- schema-version: {SCHEMA_VERSION}"


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


def emit(manifest: Manifest) -> str:
    """The complete SQLite DDL for the manifest, as one migration file."""
    header = "\n".join(f"-- {line}" for line in GENERATED_NOTICE.splitlines())
    pragmas = [
        f"PRAGMA user_version = {SCHEMA_VERSION};",
        "PRAGMA journal_mode = WAL;",
        "PRAGMA foreign_keys = ON;",
    ]
    tables = [_table_sql(manifest, name, table) for name, table in manifest.ordered_tables()]
    return "\n\n".join([header, _version_marker(), BACK_OUT, "\n".join(pragmas), *tables]) + "\n"
