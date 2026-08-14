"""`web.migrations` -- tables, columns and indexes from Alembic and Django migrations.

A migration is the most reliable static statement a repository makes about its
schema: it is the thing that actually ran. **It agrees with
`web.sqlalchemy.schema` by construction** (`_dbfield`), because the two describe
one referent from two files and a namespace disagreement would mint every column
twice.

**Both migration dialects, because a repository has one or the other and the
extractor cannot know which in advance.** Alembic writes `op.create_table(...)`;
Django writes `migrations.CreateModel(name=..., fields=[...])`. The shapes differ
enough that one pattern would match neither well.

**A dropped column is not un-emitted.** This reads what migrations *declare*, not
the net schema after replaying them: replaying is executing, and a migration that
adds then removes a column leaves a `db_field` this extractor still reports. That
is a stated limit rather than a defect -- `web.sqlalchemy.schema` reads the
current declaration, and the two together are what a reader compares.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from tree_sitter import Node

from adopt_extractors_web._dbfield import (
    DEFAULT_DIALECT,
    DEFAULT_SCHEMA,
    TABLE_KEY,
    detect_dialect,
    field_namespace,
)
from adopt_extractors_web._grammar import matches, node_text, parse, string_value

__all__ = ["MANIFEST", "MigrationsExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="web.migrations",
    version="1.0.0",
    pack="web",
    archetypes=["web"],
    kinds=["db_field"],
    method="grammar",
)

#: Any call with a leading string argument -- `op.create_table("orders", ...)`,
#: `sa.Column("status", ...)`, `op.create_index("ix_orders_status", ...)`.
_CALL_PATTERN: Final[str] = """
(call
  function: [(identifier) @fn (attribute attribute: (identifier) @fn)]
  arguments: (argument_list . (string) @first)) @call
"""

#: Django's `migrations.CreateModel(name="Order", fields=[("id", ...), ...])`.
_KEYWORD_PATTERN: Final[str] = """
(call
  function: [(identifier) @fn (attribute attribute: (identifier) @fn)]
  arguments: (argument_list
    (keyword_argument name: (identifier) @kw value: (string) @value))) @call
"""

#: `("status", models.CharField(max_length=16))` -- a Django field tuple.
_TUPLE_PATTERN: Final[str] = """
(tuple . (string) @name . (call) @definition) @tuple
"""

_CREATE_TABLE: Final[frozenset[str]] = frozenset({"create_table"})
_ADD_COLUMN: Final[frozenset[str]] = frozenset({"add_column"})
_CREATE_INDEX: Final[frozenset[str]] = frozenset({"create_index"})


class MigrationsExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        base = Path(root)
        return any(base.rglob("migrations/*.py")) or any(base.rglob("versions/*.py"))

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `db_field` per declared table, column and index."""
        dialect = _dialect(ctx)
        for entry in ctx.files(language="python"):
            ctx.budget.check()
            if "/migrations/" not in entry.path and "/versions/" not in entry.path:
                continue
            text = ctx.text(entry)
            root, data = parse("python", text)
            source = SourceRef(path=entry.path, blob_sha=entry.blob_sha)
            yield from _alembic(root, data, dialect, source)
            yield from _django(root, data, dialect, source)


def _dialect(ctx: ExtractorContext) -> str:
    for entry in ctx.files():
        name = entry.path.rsplit("/", 1)[-1].lower()
        if name not in {"settings.py", "alembic.ini", "database.py", "db.py"}:
            continue
        found = detect_dialect(ctx.text(entry))
        if found is not None:
            return found
    return DEFAULT_DIALECT


def _alembic(root: Node, data: bytes, dialect: str, source: SourceRef) -> Iterator[SurfaceFact]:
    for capture in matches("python", _CALL_PATTERN, root):
        names = capture.get("fn") or []
        firsts = capture.get("first") or []
        calls = capture.get("call") or []
        if not (names and firsts and calls):
            continue
        operation = node_text(names[0], data)
        first = string_value(firsts[0], data)
        if operation in _CREATE_TABLE:
            namespace = field_namespace(dialect, DEFAULT_SCHEMA, first)
            yield _table_fact(namespace, first, source)
            for column, arguments in _sa_columns(calls[0], data):
                yield _column_fact(namespace, column, arguments, source)
        elif operation in _ADD_COLUMN:
            namespace = field_namespace(dialect, DEFAULT_SCHEMA, first)
            for column, arguments in _sa_columns(calls[0], data):
                yield _column_fact(namespace, column, arguments, source)
        elif operation in _CREATE_INDEX:
            table = _index_table(calls[0], data)
            if table is not None:
                yield _index_fact(field_namespace(dialect, DEFAULT_SCHEMA, table), first, source)


def _sa_columns(call: Node, data: bytes) -> Iterator[tuple[str, str]]:
    for capture in matches("python", _CALL_PATTERN, call):
        names = capture.get("fn") or []
        firsts = capture.get("first") or []
        calls = capture.get("call") or []
        if not (names and firsts and calls):
            continue
        if node_text(names[0], data) != "Column":
            continue
        yield string_value(firsts[0], data), node_text(calls[0], data)


def _index_table(call: Node, data: bytes) -> str | None:
    """The table an `op.create_index("ix", "orders", [...])` names -- its *second*
    string argument, the first being the index name."""
    strings = [
        node_text(child, data)
        for child in (call.child_by_field_name("arguments") or call).children
        if child.type == "string"
    ]
    if len(strings) < 2:
        return None
    stripped = strings[1].strip()
    for quote in ('"', "'"):
        if stripped.startswith(quote) and stripped.endswith(quote):
            return stripped[1:-1]
    return None


def _django(root: Node, data: bytes, dialect: str, source: SourceRef) -> Iterator[SurfaceFact]:
    for capture in matches("python", _KEYWORD_PATTERN, root):
        names = capture.get("fn") or []
        keywords = capture.get("kw") or []
        values = capture.get("value") or []
        calls = capture.get("call") or []
        if not (names and keywords and values and calls):
            continue
        if node_text(names[0], data) != "CreateModel":
            continue
        if node_text(keywords[0], data) != "name":
            continue
        # Django derives a table name from the model name by lower-casing it and
        # prefixing the app label. The app label is not in the migration, so the
        # model name lower-cased is what this tree declares -- recorded as such
        # rather than dressed up as the real table name.
        table = string_value(values[0], data).lower()
        namespace = field_namespace(dialect, DEFAULT_SCHEMA, table)
        yield _table_fact(namespace, table, source)
        for column, arguments in _django_fields(calls[0], data):
            yield _column_fact(namespace, column, arguments, source)


def _django_fields(call: Node, data: bytes) -> Iterator[tuple[str, str]]:
    for capture in matches("python", _TUPLE_PATTERN, call):
        names = capture.get("name") or []
        definitions = capture.get("definition") or []
        if names and definitions:
            yield string_value(names[0], data), node_text(definitions[0], data)


def _table_fact(namespace: str, table: str, source: SourceRef) -> SurfaceFact:
    return SurfaceFact(
        identity_kind="db_field",
        namespace=namespace,
        local_key=TABLE_KEY,
        title=table,
        attributes={"column": TABLE_KEY, "data_type": "table"},
        source_refs=[source],
    )


def _column_fact(namespace: str, column: str, arguments: str, source: SourceRef) -> SurfaceFact:
    return SurfaceFact(
        identity_kind="db_field",
        namespace=namespace,
        local_key=column,
        title=column,
        attributes={
            "column": column,
            "data_type": _data_type(arguments),
            "nullable": _nullable(arguments),
            "is_key": "primary_key=True" in arguments,
            "is_unique": "unique=True" in arguments,
            "indexes": ["index"]
            if "db_index=True" in arguments or "index=True" in arguments
            else [],
        },
        source_refs=[source],
    )


def _index_fact(namespace: str, name: str, source: SourceRef) -> SurfaceFact:
    return SurfaceFact(
        identity_kind="db_field",
        namespace=namespace,
        local_key=TABLE_KEY,
        title=name,
        attributes={"column": TABLE_KEY, "data_type": "table", "indexes": [name]},
        source_refs=[source],
    )


def _data_type(arguments: str) -> str | None:
    """The declared field type -- `sa.String(16)`, `models.CharField(...)`."""
    head = arguments.split("(", 1)[0].strip()
    return head.rsplit(".", 1)[-1] or None


def _nullable(arguments: str) -> bool | None:
    if "nullable=False" in arguments or "null=False" in arguments:
        return False
    if "nullable=True" in arguments or "null=True" in arguments:
        return True
    return None
