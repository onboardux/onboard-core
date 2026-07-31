"""Rendering helpers shared by the four emitters.

Everything here is a pure function of the manifest. Nothing reads a database,
nothing reads the clock, and nothing reads the filesystem -- which is what makes
`generate --check` a meaningful drift check and what the determinism property
test asserts. A timestamp in a generated header would make every run differ from
the last, so generated files carry provenance without carrying a date.
"""

import keyword
from typing import Final

from adopt_schema.manifest import Column, EnumDecl, Manifest, Table

__all__ = [
    "GENERATED_NOTICE",
    "class_name",
    "enum_check",
    "field_name",
    "quote_sql_literal",
    "resolve_enum",
]

GENERATED_NOTICE: Final[str] = (
    "GENERATED FROM schema/canonical.yaml -- DO NOT EDIT.\n"
    "Regenerate with `adopt-schema generate`. A hand edit is SCHEMA_GENERATED_DRIFT\n"
    "and CI fails on it, because a hand-edited realization means the manifest has\n"
    "silently stopped being the single source of truth."
)


def resolve_enum(manifest: Manifest, column: Column) -> EnumDecl | None:
    name = column.enum_name
    return manifest.enums[name] if name is not None else None


def quote_sql_literal(value: str | int | float | bool) -> str:
    """Render a declared default as SQL, in the dialect-neutral cases."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def enum_check(column_name: str, enum: EnumDecl) -> str:
    """The CHECK constraint for an enum column, identical in both dialects.

    A contiguous integer enum renders as `BETWEEN`, which is what source spec §4
    declares for `locator_rung` and is exactly equivalent to the membership test.
    Emitting `IN (1,2,3,4,5)` there would be a gratuitous difference from the
    contract at the one place a reviewer compares them line by line.
    """
    if enum.is_integer:
        values = sorted(int(v) for v in enum.values)
        if values == list(range(values[0], values[0] + len(values))):
            return f"CHECK ({column_name} BETWEEN {values[0]} AND {values[-1]})"
        rendered = ",".join(str(v) for v in values)
        return f"CHECK ({column_name} IN ({rendered}))"
    rendered = ",".join(f"'{v}'" for v in enum.values)
    return f"CHECK ({column_name} IN ({rendered}))"


def class_name(table_name: str) -> str:
    """`knowledge_item` -> `KnowledgeItem`."""
    return "".join(part.title() for part in table_name.split("_"))


def field_name(column_name: str) -> str:
    """A Python-safe field name; `class` is a keyword and one column uses it."""
    return f"{column_name}_" if keyword.iskeyword(column_name) else column_name


def primary_key_columns(table: Table) -> set[str]:
    return set(table.primary_key)
