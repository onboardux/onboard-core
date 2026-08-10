"""Load `schema/canonical.yaml` and refuse to return a manifest that is wrong.

The manifest is the only schema authority. Every realization is generated from
what this module returns, so a defect that survives loading is a defect emitted
four times -- into two dialects, a JSON schema and the typed models -- and
discovered at whichever one someone happens to run first.

The self-checks therefore run at load, not at emit. **A foreign-key cycle in
particular must be rejected here** (CR-18): Postgres rejects a forward
`REFERENCES` outright, while SQLite defers the same error to first insert, where
it costs far more to diagnose than it does at the moment someone edits the YAML.

This module does not import `sqlite3`, and never will -- the `no-raw-sqlite`
contract names this package as a source module, and the manifest describes a
schema rather than talking to a database.
"""

import os
import re
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from adopt_obs import AdoptError, ErrorCode
from adopt_schema.assets import schema_dir

__all__ = [
    "CANONICAL_PATH_ENV",
    "Column",
    "EnumDecl",
    "Index",
    "Manifest",
    "ScopeRef",
    "Table",
    "canonical_path",
    "load_manifest",
]

#: Overrides the manifest location, so a test can load a fixture and CI can load
#: a checkout at a path this file cannot infer.
CANONICAL_PATH_ENV: Final[str] = "ADOPT_SCHEMA_MANIFEST"

#: The §2.3 type vocabulary. `enum(<name>)` is handled separately.
SCALAR_TYPES: Final[frozenset[str]] = frozenset(
    {"id", "slug", "uri", "text", "md", "json", "int", "real", "bool", "ts"}
)

_ENUM_TYPE_RE: Final[re.Pattern[str]] = re.compile(r"^enum\((?P<name>[a-z_][a-z0-9_]*)\)$")
_REFERENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<table>[a-z_][a-z0-9_]*)\.(?P<column>[a-z_][a-z0-9_]*)$"
)

#: Every table, column, index and enum name is interpolated straight into DDL,
#: so the safety of that interpolation is this pattern and nothing else. It is
#: checked at load rather than at emit: an identifier that cannot be rendered
#: safely should never reach an emitter in the first place.
_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")

ScopeLevel = Literal["global", "firm", "engagement", "system", "environment", "unscoped"]

#: Scope levels that describe client-scoped rows and therefore require a
#: declared derivation for their row-level-security policy.
SCOPED_LEVELS: Final[frozenset[str]] = frozenset({"firm", "engagement", "system", "environment"})


def _invalid(message: str, hint: str) -> AdoptError:
    return AdoptError(ErrorCode.MANIFEST_INVALID, message=message, hint=hint)


class _Strict(BaseModel):
    """Closed by construction: an unknown key is a typo, not an extension."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Column(_Strict):
    name: str
    type: str
    nullable: bool = True
    default: str | int | float | bool | None = None
    references: str | None = None
    unique: bool = False
    retired_in_version: int | None = None

    @property
    def enum_name(self) -> str | None:
        """The declared enum this column constrains against, if any."""
        match = _ENUM_TYPE_RE.match(self.type)
        return match.group("name") if match else None

    @property
    def reference_parts(self) -> tuple[str, str] | None:
        if self.references is None:
            return None
        match = _REFERENCE_RE.match(self.references)
        return (match.group("table"), match.group("column")) if match else None


class Index(_Strict):
    name: str
    columns: list[str]
    unique: bool = False


class ScopeRef(_Strict):
    """How a table's rows reach the firm and engagement RLS is expressed against.

    Exactly one of the two shapes in contracts §2.1: direct scope columns, or a
    list of foreign keys to walk. Both are never set at once, because a table
    that could be scoped two ways would get two policies and the narrower one
    would be silently redundant.
    """

    firm: str | None = None
    engagement: str | None = None
    via: list[str] | None = None

    @property
    def is_direct(self) -> bool:
        return self.firm is not None or self.engagement is not None


class Table(_Strict):
    since: int
    purpose: str
    scope_level: ScopeLevel
    scope_ref: ScopeRef | None = None
    unscoped_reason: str | None = None
    exportable: bool
    # Required, and permitted to be empty. The rule is not "every table has a
    # primary key" -- `schema_meta` deliberately has none (CR-04) -- it is that
    # no table lacks one by accident, which only an explicit list can prove.
    primary_key: list[str]
    columns: list[Column]
    unique: list[list[str]] = []
    indexes: list[Index] = []
    retired_in_version: int | None = None

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


class EnumDecl(_Strict):
    since: int
    values: list[str | int]
    retired_values: list[str | int] = []

    @property
    def is_integer(self) -> bool:
        return all(isinstance(v, int) and not isinstance(v, bool) for v in self.values)


class Manifest(_Strict):
    schema_version: int
    export_version: int
    enums: dict[str, EnumDecl]
    tables: dict[str, Table]

    def ordered_tables(self) -> list[tuple[str, Table]]:
        """Tables in a foreign-key topological order, deterministic across runs.

        Declaration order breaks every tie, so the emitted file is a pure
        function of the manifest rather than of dictionary iteration luck.
        """
        return [(name, self.tables[name]) for name in _topological_order(self)]

    def exportable_tables(self) -> list[tuple[str, Table]]:
        return [(n, t) for n, t in self.ordered_tables() if t.exportable]


def _dependencies(manifest: Manifest) -> dict[str, set[str]]:
    """Table -> the tables it references, ignoring self-references.

    A self-reference (`supersedes_revision_id`) is acyclic for ordering
    purposes: the table is emitted once, and the constraint resolves against a
    row in the same table (CR-07).
    """
    edges: dict[str, set[str]] = defaultdict(set)
    for name, table in manifest.tables.items():
        for column in table.columns:
            parts = column.reference_parts
            if parts is not None and parts[0] != name:
                edges[name].add(parts[0])
    return edges


def _topological_order(manifest: Manifest) -> list[str]:
    edges = _dependencies(manifest)
    ordered: list[str] = []
    placed: set[str] = set()
    remaining = list(manifest.tables)

    while remaining:
        ready = [name for name in remaining if edges.get(name, set()) <= placed]
        if not ready:
            raise _invalid(
                "the foreign-key graph contains a cycle among: " + ", ".join(sorted(remaining)),
                "Break the cycle by making one side a pointer without a foreign key, as "
                "`current_revision_id` is (CR-07). Postgres rejects a forward REFERENCES and "
                "SQLite defers the same error to first insert.",
            )
        ordered.extend(ready)
        placed.update(ready)
        remaining = [name for name in remaining if name not in placed]
    return ordered


def _check_columns(name: str, table: Table, manifest: Manifest) -> Iterator[str]:
    seen: set[str] = set()
    for column in table.columns:
        if column.name in seen:
            yield f"{name}.{column.name} is declared twice."
        seen.add(column.name)

        enum_name = column.enum_name
        if enum_name is not None:
            if enum_name not in manifest.enums:
                yield (
                    f"{name}.{column.name} constrains against enum {enum_name!r}, "
                    "which no `enums:` entry declares."
                )
        elif column.type not in SCALAR_TYPES:
            yield (
                f"{name}.{column.name} declares type {column.type!r}, which is not in the "
                f"contracts §2.3 vocabulary ({', '.join(sorted(SCALAR_TYPES))}, enum(<name>))."
            )

        if column.references is not None:
            parts = column.reference_parts
            if parts is None:
                yield (
                    f"{name}.{column.name} references {column.references!r}, which is not "
                    "in `<table>.<column>` form."
                )
            else:
                target_table, target_column = parts
                target = manifest.tables.get(target_table)
                if target is None:
                    yield (
                        f"{name}.{column.name} references table {target_table!r}, "
                        "which no `tables:` entry declares."
                    )
                elif target_column not in target.column_names:
                    yield (
                        f"{name}.{column.name} references {target_table}.{target_column}, "
                        f"and {target_table} has no such column."
                    )


def _check_column_lists(name: str, table: Table) -> Iterator[str]:
    known = set(table.column_names)
    for column in table.primary_key:
        if column not in known:
            yield f"{name} names {column!r} in its primary key, but has no such column."
    for group in table.unique:
        for column in group:
            if column not in known:
                yield f"{name} names {column!r} in a unique constraint, but has no such column."
    for index in table.indexes:
        for column in index.columns:
            if column not in known:
                yield f"{name}: index {index.name} names {column!r}, but the table has no such column."


def _check_scope(name: str, table: Table) -> Iterator[str]:
    known = set(table.column_names)
    ref = table.scope_ref

    if table.scope_level == "unscoped" and not table.unscoped_reason:
        yield (
            f"{name} declares scope_level 'unscoped' without an `unscoped_reason`. "
            "An unscoped table is a visible gap or it is an accident; the reason is what "
            "makes it the first."
        )
    if table.scope_level in {"global", "unscoped"} and ref is not None:
        yield f"{name} declares scope_level {table.scope_level!r} and a scope_ref; it may not have both."
    if table.scope_level in SCOPED_LEVELS:
        if ref is None:
            yield (
                f"{name} declares scope_level {table.scope_level!r} but no scope_ref, so no "
                "row-level-security policy can be derived for it."
            )
            return
        if ref.is_direct and ref.via is not None:
            yield f"{name} declares both a direct scope_ref and a `via` walk; exactly one is permitted."
        if not ref.is_direct and not ref.via:
            yield f"{name} declares an empty scope_ref."
        for column in filter(None, (ref.firm, ref.engagement)):
            if column not in known:
                yield f"{name}: scope_ref names column {column!r}, which the table does not declare."
        for column in ref.via or []:
            if column not in known:
                yield f"{name}: scope_ref via names column {column!r}, which the table does not declare."


def _check_identifiers(name: str, table: Table) -> Iterator[str]:
    """Every name that reaches generated SQL is a plain lowercase identifier.

    This is the check the emitters' string interpolation rests on. Without it a
    quote or a space in the manifest would produce DDL that is at best broken and
    at worst something else entirely, and the emitters would have to defend
    themselves four times over instead of once here.
    """
    if not _IDENTIFIER_RE.match(name):
        yield f"table name {name!r} is not a plain lowercase identifier."
    for column in table.columns:
        if not _IDENTIFIER_RE.match(column.name):
            yield f"{name}: column name {column.name!r} is not a plain lowercase identifier."
    for index in table.indexes:
        if not _IDENTIFIER_RE.match(index.name):
            yield f"{name}: index name {index.name!r} is not a plain lowercase identifier."


def _check_index_name_uniqueness(manifest: Manifest) -> Iterator[str]:
    owners: dict[str, str] = {}
    for name, table in manifest.tables.items():
        for index in table.indexes:
            previous = owners.get(index.name)
            if previous is not None:
                yield (
                    f"index name {index.name!r} is declared on both {previous} and {name}. "
                    "Index names share one namespace in both dialects."
                )
            owners[index.name] = name


def _check_enum_identifiers(manifest: Manifest) -> Iterator[str]:
    for name in manifest.enums:
        if not _IDENTIFIER_RE.match(name):
            yield f"enum name {name!r} is not a plain lowercase identifier."


def _validate(manifest: Manifest) -> None:
    problems: list[str] = []
    for name, table in manifest.tables.items():
        problems.extend(_check_identifiers(name, table))
        problems.extend(_check_columns(name, table, manifest))
        problems.extend(_check_column_lists(name, table))
        problems.extend(_check_scope(name, table))
    problems.extend(_check_index_name_uniqueness(manifest))
    problems.extend(_check_enum_identifiers(manifest))

    if problems:
        raise _invalid(
            f"{len(problems)} manifest problem(s): " + " | ".join(problems),
            "The manifest is the only schema authority, so a defect here is emitted into "
            "both dialects, the export schema and the models. Fix schema/canonical.yaml.",
        )

    # Ordering last: it raises on its own, and a cycle report is more useful
    # once the references it is built from are known to resolve.
    _topological_order(manifest)


def canonical_path() -> Path:
    """The manifest, wherever this artefact carries it.

    Resolved through `assets.assets_root` rather than by walking to a checkout,
    because a wheel has no checkout above it and the walk landed on an unrelated
    directory instead of failing (CR-53).
    """
    override = os.environ.get(CANONICAL_PATH_ENV)
    return Path(override) if override else schema_dir() / "canonical.yaml"


def load_manifest(path: Path | None = None) -> Manifest:
    """Parse, validate and return the canonical manifest.

    Raises `MANIFEST_INVALID` rather than returning a partly-wrong manifest: a
    caller that has to check the result is a caller that will forget to.
    """
    source = path or canonical_path()
    if not source.exists():
        raise _invalid(
            f"no manifest at {source}",
            f"Point {CANONICAL_PATH_ENV} at schema/canonical.yaml, or run from a checkout.",
        )

    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise _invalid(
            f"{source} does not parse to a mapping",
            "The manifest is a YAML mapping with `schema_version`, `export_version`, "
            "`enums` and `tables` at its root.",
        )

    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as error:
        raise _invalid(
            f"{source} does not match the contracts §2.1 grammar: {error}",
            "Every key is declared in contracts §2.1. An unexpected key is a typo, and a "
            "missing `primary_key` is a table whose key was forgotten rather than declined.",
        ) from error

    _validate(manifest)
    return manifest
