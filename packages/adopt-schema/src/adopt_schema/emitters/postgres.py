"""The Postgres realization: the mechanical delta from SQLite, and nothing else.

Two things differ and both are derived, never authored: the type mapping in
contracts §2.3, and row-level security expressed from each table's declared
scope. **Any divergence beyond those two is an emitter defect, never a local
decision** -- if the two realizations can drift, the promise that one manifest
describes both is already broken.

Every policy is derived from `scope_ref`, so isolation is a property of the
declaration rather than of whoever wrote the policy by hand. A table whose scope
is not derivable does not quietly receive no policy: it must declare
`scope_level: unscoped` with a reason, and this emitter prints that reason where
the policy would have been.
"""

from typing import Final

from adopt_const import SCHEMA_VERSION
from adopt_schema.emitters._shared import (
    GENERATED_NOTICE,
    enum_check,
    resolve_enum,
)
from adopt_schema.manifest import Column, Index, Manifest, Table

__all__ = ["emit"]

#: contracts §2.3, manifest type -> Postgres type.
TYPE_MAP: Final[dict[str, str]] = {
    "id": "text",
    "slug": "text",
    "uri": "text",
    "text": "text",
    "md": "text",
    "json": "jsonb",
    "int": "bigint",
    "real": "double precision",
    "bool": "boolean",
    "ts": "timestamptz",
}

FIRM_SETTING: Final[str] = "current_setting('adopt.firm_id', true)"
ENGAGEMENT_SETTING: Final[str] = "current_setting('adopt.engagement_id', true)"
POLICY_NAME: Final[str] = "scope_isolation"

BACK_OUT: Final[str] = (
    "-- back-out: none. This is the initial creation of schema version 3, so the\n"
    "-- back-out is to drop the database and create a new one. There is no in-place\n"
    "-- reversal, and none will be written."
)


def _pg_type(manifest: Manifest, column: Column) -> str:
    enum = resolve_enum(manifest, column)
    if enum is not None:
        return "bigint" if enum.is_integer else "text"
    return TYPE_MAP[column.type]


def _default_sql(manifest: Manifest, column: Column) -> str:
    value = column.default
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    literal = f"'{escaped}'"
    return f"{literal}::jsonb" if column.type == "json" else literal


def _column_sql(manifest: Manifest, table: Table, column: Column) -> str:
    parts = [column.name, _pg_type(manifest, column)]

    if table.primary_key == [column.name]:
        parts.append("PRIMARY KEY")
    elif not column.nullable:
        parts.append("NOT NULL")

    if column.default is not None:
        parts.append(f"DEFAULT {_default_sql(manifest, column)}")
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


def _predicate(manifest: Manifest, name: str, alias: str, depth: int) -> str:
    """The scope predicate for one table, resolved through its declared derivation.

    A `via` walk becomes an `EXISTS` over the parent, recursively. A NULL foreign
    key contributes no branch and the row becomes invisible to every tenant --
    which is the correct direction for a row whose scope cannot be established.
    """
    table = manifest.tables[name]
    ref = table.scope_ref
    if ref is None:  # pragma: no cover -- the loader rejects this before emit
        raise ValueError(f"{name} has no scope_ref")

    if ref.is_direct:
        clauses = []
        if ref.firm is not None:
            clauses.append(f"{alias}.{ref.firm} = {FIRM_SETTING}")
        if ref.engagement is not None:
            clauses.append(f"{alias}.{ref.engagement} = {ENGAGEMENT_SETTING}")
        return " AND ".join(clauses)

    branches: list[str] = []
    for offset, foreign_key in enumerate(ref.via or []):
        column = next(c for c in table.columns if c.name == foreign_key)
        parent_table, parent_column = column.reference_parts or ("", "")
        parent_alias = f"p{depth}_{offset}"
        inner = _predicate(manifest, parent_table, parent_alias, depth + 1)
        # S608: every identifier interpolated here is a manifest name, and the
        # loader rejects any name that is not a plain lowercase identifier before
        # an emitter ever sees it. There is no caller-supplied value in this
        # string, and this DDL is generated at build time, not at request time.
        branches.append(
            f"EXISTS (SELECT 1 FROM {parent_table} {parent_alias} "  # noqa: S608
            f"WHERE {parent_alias}.{parent_column} = {alias}.{foreign_key} AND {inner})"
        )
    return branches[0] if len(branches) == 1 else "(" + " OR ".join(branches) + ")"


def _rls_sql(manifest: Manifest, name: str, table: Table) -> str:
    if table.scope_level == "global":
        return f"-- {name}: scope_level 'global' -- holds no client-scoped data, so no policy."
    if table.scope_level == "unscoped":
        reason = " ".join((table.unscoped_reason or "").split())
        return (
            f"-- !! {name}: NO ROW-LEVEL SECURITY POLICY IS EMITTED.\n"
            f"-- !! {reason}\n"
            f"-- !! This is a recorded gap, not a design. Until it is resolved, {name} is\n"
            "-- !! readable across every scope by anything that reaches the table."
        )

    predicate = _predicate(manifest, name, name, 0)
    return (
        f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {name} FORCE  ROW LEVEL SECURITY;\n"
        f"CREATE POLICY {POLICY_NAME} ON {name}\n"
        f"  USING      ({predicate})\n"
        f"  WITH CHECK ({predicate});"
    )


def emit(manifest: Manifest) -> str:
    """The complete Postgres DDL plus every derived RLS policy."""
    header = "\n".join(f"-- {line}" for line in GENERATED_NOTICE.splitlines())
    version_marker = f"-- schema-version: {SCHEMA_VERSION}"
    ordered = manifest.ordered_tables()
    tables = [_table_sql(manifest, name, table) for name, table in ordered]
    policies = [
        "-- ═══════════════ ROW-LEVEL SECURITY, DERIVED FROM ROW SCOPE ═══════════════",
        "-- Every policy below is generated from the table's declared `scope_ref`.",
        "-- Editing one by hand makes isolation a property of this file rather than of",
        "-- the manifest, and the next regeneration silently reverts it.",
        *[_rls_sql(manifest, name, table) for name, table in ordered],
    ]
    return "\n\n".join([header, version_marker, BACK_OUT, *tables, *policies]) + "\n"
