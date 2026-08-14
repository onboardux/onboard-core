"""`web.sqlalchemy.schema` -- tables and columns from **declared** metadata.

**No SQLAlchemy import, and B1-CR-65 explains why the obvious route is closed.**
`MetaData` populates from exactly two places: `reflect(bind=Engine)`, which needs
the live connection `05` S1.4 gates behind `--db-url` and a tier and whose own
validation line asserts a default run never opens; and importing the client's
model modules so `Base.metadata` fills in, which is `02` §7 obligation 1. So
*"declared metadata"* means the declarations as they appear in source, read
through the `python` grammar like every other declaration in this build.

**It agrees with `web.migrations` by construction** (`_dbfield`). The two describe
the same tables from different files, and a disagreement about the namespace would
mint every column twice.

Two declaration styles, because SQLAlchemy has two and real projects mix them:
the imperative `Table("orders", metadata, Column(...))` and the declarative
`class Order(Base)` with `__tablename__` and either `Column` or `mapped_column`
attributes.
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

__all__ = ["MANIFEST", "SqlalchemySchemaExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="web.sqlalchemy.schema",
    version="1.0.0",
    pack="web",
    archetypes=["web"],
    kinds=["db_field"],
    method="grammar",
)

#: The declarative style: a class whose body assigns `__tablename__`.
_CLASS_PATTERN: Final[str] = """
(class_definition name: (identifier) @name body: (block) @body) @class
"""

#: `__tablename__ = "orders"` inside a class body.
_TABLENAME_PATTERN: Final[str] = """
(assignment left: (identifier) @attr right: (string) @value) @assign
"""

#: `status = Column(String(16), nullable=False)` and its `mapped_column` sibling.
_COLUMN_PATTERN: Final[str] = """
(assignment
  left: (identifier) @column
  right: (call function: [(identifier) @ctor (attribute attribute: (identifier) @ctor)]
               arguments: (argument_list) @args)) @assign
"""

#: The imperative style: `Table("orders", metadata, Column("id", Integer), ...)`.
_TABLE_PATTERN: Final[str] = """
(call
  function: [(identifier) @ctor (attribute attribute: (identifier) @ctor)]
  arguments: (argument_list . (string) @table)) @call
"""

_COLUMN_CTORS: Final[frozenset[str]] = frozenset({"Column", "mapped_column"})
_KEY_MARKERS: Final[tuple[str, ...]] = ("primary_key=True",)
_UNIQUE_MARKERS: Final[tuple[str, ...]] = ("unique=True",)
_INDEX_MARKERS: Final[tuple[str, ...]] = ("index=True",)


class SqlalchemySchemaExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return any(Path(root).rglob("*.py"))

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `db_field` per table (keyed `*`) and one per column."""
        dialect = _dialect(ctx)
        for entry in ctx.files(language="python"):
            ctx.budget.check()
            text = ctx.text(entry)
            if "__tablename__" not in text and "Table(" not in text:
                continue
            root, data = parse("python", text)
            source = SourceRef(path=entry.path, blob_sha=entry.blob_sha)
            yield from _declarative(root, data, dialect, source)
            yield from _imperative(root, data, dialect, source)


def _dialect(ctx: ExtractorContext) -> str:
    """The dialect the tree declares, or `sql`.

    Read from the files most likely to declare one rather than from every file:
    a `settings.py` `ENGINE`, an `alembic.ini` URL. See `_dbfield` for why the
    fallback is a statement rather than a guess.
    """
    for entry in ctx.files():
        name = entry.path.rsplit("/", 1)[-1].lower()
        if name not in {"settings.py", "alembic.ini", "database.py", "db.py"}:
            continue
        found = detect_dialect(ctx.text(entry))
        if found is not None:
            return found
    return DEFAULT_DIALECT


def _declarative(root: Node, data: bytes, dialect: str, source: SourceRef) -> Iterator[SurfaceFact]:
    for capture in matches("python", _CLASS_PATTERN, root):
        bodies = capture.get("body") or []
        if not bodies:
            continue
        body = bodies[0]
        table = _tablename(body, data)
        if table is None:
            continue
        namespace = field_namespace(dialect, DEFAULT_SCHEMA, table)
        yield _table_fact(namespace, table, source)
        for column, attributes in _columns(body, data):
            yield _column_fact(namespace, column, attributes, source)


def _tablename(body: Node, data: bytes) -> str | None:
    for capture in matches("python", _TABLENAME_PATTERN, body):
        names = capture.get("attr") or []
        values = capture.get("value") or []
        if names and values and node_text(names[0], data) == "__tablename__":
            return string_value(values[0], data)
    return None


def _columns(body: Node, data: bytes) -> Iterator[tuple[str, str]]:
    """`(column name, the constructor call's argument text)` for one class body."""
    for capture in matches("python", _COLUMN_PATTERN, body):
        columns = capture.get("column") or []
        ctors = capture.get("ctor") or []
        arguments = capture.get("args") or []
        if not (columns and ctors):
            continue
        if node_text(ctors[0], data) not in _COLUMN_CTORS:
            continue
        yield (
            node_text(columns[0], data),
            node_text(arguments[0], data) if arguments else "",
        )


def _imperative(root: Node, data: bytes, dialect: str, source: SourceRef) -> Iterator[SurfaceFact]:
    for capture in matches("python", _TABLE_PATTERN, root):
        ctors = capture.get("ctor") or []
        tables = capture.get("table") or []
        calls = capture.get("call") or []
        if not (ctors and tables and calls):
            continue
        if node_text(ctors[0], data) != "Table":
            continue
        table = string_value(tables[0], data)
        namespace = field_namespace(dialect, DEFAULT_SCHEMA, table)
        yield _table_fact(namespace, table, source)
        for column, attributes in _positional_columns(calls[0], data):
            yield _column_fact(namespace, column, attributes, source)


def _positional_columns(call: Node, data: bytes) -> Iterator[tuple[str, str]]:
    """`Column("id", Integer, primary_key=True)` inside a `Table(...)` call."""
    for capture in matches("python", _TABLE_PATTERN, call):
        ctors = capture.get("ctor") or []
        names = capture.get("table") or []
        calls = capture.get("call") or []
        if not (ctors and names and calls):
            continue
        if node_text(ctors[0], data) != "Column":
            continue
        yield string_value(names[0], data), node_text(calls[0], data)


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
            "is_key": any(marker in arguments for marker in _KEY_MARKERS),
            "is_unique": any(marker in arguments for marker in _UNIQUE_MARKERS),
            "indexes": ["index"] if any(m in arguments for m in _INDEX_MARKERS) else [],
        },
        source_refs=[source],
    )


def _data_type(arguments: str) -> str | None:
    """The first positional type in a `Column(...)` call.

    Recorded as it was written -- `String(16)` rather than `VARCHAR(16)` --
    because normalizing to SQL types would require knowing the dialect, which is
    the thing this extractor cannot read.
    """
    inner = arguments.strip().lstrip("(").rstrip(")").strip()
    if not inner:
        return None
    for part in inner.split(","):
        candidate = part.strip()
        if not candidate or "=" in candidate:
            continue
        if candidate.startswith(('"', "'")):
            continue
        return candidate
    return None


def _nullable(arguments: str) -> bool | None:
    if "nullable=False" in arguments:
        return False
    if "nullable=True" in arguments:
        return True
    return None
