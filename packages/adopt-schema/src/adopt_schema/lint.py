"""The additive-only linter: version 3 is the last schema anyone creates.

Compares a manifest at HEAD against a manifest at a git ref and rejects every
change that would break a store already in the field. The rule set is PRD F2.2's,
and the widening lattice is contracts §2.2's.

**Every violation names both remedies**, because a linter that only says "no" is
a linter people route around. There are exactly two ways to make a breaking
change additive -- add a new column, or retire the old one with
`retired_in_version` and leave the physical object in place -- and a message that
does not say so leaves the reader to guess which one applies.

The constraint binds from the `0.3.0` tag. Until then the manifest is free, so
this ships and gates now against a baseline that is set at release.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml
from pydantic import ValidationError

from adopt_obs import AdoptError, ErrorCode
from adopt_schema.manifest import Column, EnumDecl, Manifest, Table, canonical_path

__all__ = ["Violation", "lint", "manifest_at_ref"]

#: contracts §2.2 rule 3. Every other type change is a violation, including the
#: one that looks harmless: `text -> json` re-reads every stored value.
WIDENING_EDGES: Final[frozenset[tuple[str, str]]] = frozenset({("int", "real"), ("text", "md")})

_ADD_COLUMN: Final[str] = "add a new column instead and leave this one as it is"
_RETIRE: Final[str] = "or mark it `retired_in_version` and keep the physical object, written NULL"


@dataclass(frozen=True)
class Violation:
    rule: str
    table: str
    column: str | None
    detail: str
    remedies: tuple[str, str]

    def render(self) -> str:
        where = f"{self.table}.{self.column}" if self.column else self.table
        return (
            f"{self.rule}: {where} -- {self.detail}. Either {self.remedies[0]}, {self.remedies[1]}."
        )


def _violation(rule: str, table: str, column: str | None, detail: str) -> Violation:
    return Violation(rule, table, column, detail, (_ADD_COLUMN, _RETIRE))


def _column_map(table: Table) -> dict[str, Column]:
    return {column.name: column for column in table.columns}


def _enum_values(manifest: Manifest, column: Column) -> list[str | int] | None:
    name = column.enum_name
    return list(manifest.enums[name].values) if name is not None else None


def _check_type(
    base: Manifest, head: Manifest, table_name: str, before: Column, after: Column
) -> list[Violation]:
    if before.type == after.type:
        base_values = _enum_values(base, before)
        head_values = _enum_values(head, after)
        if base_values is not None and head_values is not None:
            missing = [v for v in base_values if v not in head_values]
            if missing:
                return [
                    _violation(
                        "enum-value-removed",
                        table_name,
                        after.name,
                        f"enum values {missing!r} were removed or renamed; a stored row still "
                        "carries them",
                    )
                ]
        return []

    if (before.type, after.type) in WIDENING_EDGES:
        return []

    base_values = _enum_values(base, before)
    head_values = _enum_values(head, after)
    if base_values is not None and head_values is not None:
        missing = [v for v in base_values if v not in head_values]
        if not missing:
            return []
        return [
            _violation(
                "type-narrowed",
                table_name,
                after.name,
                f"enum changed from {before.type} to {after.type}, which does not include "
                f"{missing!r}",
            )
        ]

    return [
        _violation(
            "type-narrowed",
            table_name,
            after.name,
            f"type changed from {before.type!r} to {after.type!r}, which is not a widening "
            "edge (int->real, text->md, non-null->nullable, enum->superset)",
        )
    ]


def _check_column(
    base: Manifest, head: Manifest, table_name: str, before: Column, after: Column
) -> list[Violation]:
    found = _check_type(base, head, table_name, before, after)
    if before.nullable and not after.nullable:
        found.append(
            _violation(
                "nullability-tightened",
                table_name,
                after.name,
                "the column became NOT NULL; rows already written may hold NULL",
            )
        )
    return found


def _check_table(base: Manifest, head: Manifest, name: str) -> list[Violation]:
    before, after = base.tables[name], head.tables[name]
    found: list[Violation] = []

    if before.since != after.since:
        found.append(
            _violation(
                "since-edited",
                name,
                None,
                f"`since` changed from {before.since} to {after.since}; it is set once and "
                "never edited",
            )
        )

    before_columns, after_columns = _column_map(before), _column_map(after)
    for column_name, column in before_columns.items():
        if column_name not in after_columns:
            found.append(
                _violation(
                    "column-removed",
                    name,
                    column_name,
                    "the column is gone from the manifest; a store in the field still has it",
                )
            )
            continue
        found.extend(_check_column(base, head, name, column, after_columns[column_name]))

    if before.primary_key != after.primary_key:
        found.append(
            _violation(
                "primary-key-changed",
                name,
                None,
                f"the primary key changed from {before.primary_key} to {after.primary_key}",
            )
        )

    before_indexes = {index.name for index in before.indexes}
    after_indexes = {index.name for index in after.indexes}
    found.extend(
        _violation(
            "index-removed",
            name,
            None,
            f"index {index_name} is gone from the manifest",
        )
        for index_name in sorted(before_indexes - after_indexes)
    )
    return found


def _check_enum(name: str, before: EnumDecl, after: EnumDecl) -> list[Violation]:
    missing = [value for value in before.values if value not in after.values]
    if not missing:
        return []
    return [
        Violation(
            "enum-value-removed",
            name,
            None,
            f"values {missing!r} were removed or renamed from the enum vocabulary",
            (
                "add the new value and leave the old one declared",
                "or list it under `retired_values`, which keeps stored rows readable",
            ),
        )
    ]


def lint(base: Manifest | None, head: Manifest) -> list[Violation]:
    """Every non-additive change between two manifests.

    A `None` base means the manifest did not exist at that ref, which is the
    legal state for the commit that introduces it: everything is new, and
    nothing that was ever in the field has been taken away.
    """
    if base is None:
        return []

    found: list[Violation] = [
        _violation(
            "table-removed",
            name,
            None,
            "the table is gone from the manifest; a store in the field still has it",
        )
        for name in sorted(set(base.tables) - set(head.tables))
    ]
    for name in sorted(set(base.tables) & set(head.tables)):
        found.extend(_check_table(base, head, name))
    for name in sorted(set(base.enums) & set(head.enums)):
        found.extend(_check_enum(name, base.enums[name], head.enums[name]))
    return found


def manifest_at_ref(
    ref: str, *, repo: Path | None = None, relative: str | None = None
) -> Manifest | None:
    """Read the manifest from a git ref without checking anything out.

    Returns `None` when the ref has no manifest, which is not an error: it is
    what the very first commit to introduce the file looks like from behind.
    """
    git = shutil.which("git")
    if git is None:
        raise AdoptError(
            ErrorCode.SCHEMA_NON_ADDITIVE,
            message="git is not on PATH, so the base manifest cannot be read",
            hint="`lint --base` compares against a git ref. Run it inside a checkout.",
        )

    path = relative or "schema/canonical.yaml"
    completed = subprocess.run(  # noqa: S603 -- fixed argv, git resolved from PATH by shutil.which
        [git, "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
        cwd=repo or canonical_path().parent.parent,
        check=False,
    )
    if completed.returncode != 0:
        return None

    try:
        return Manifest.model_validate(yaml.safe_load(completed.stdout))
    except (ValidationError, yaml.YAMLError) as error:
        raise AdoptError(
            ErrorCode.SCHEMA_NON_ADDITIVE,
            message=f"the manifest at {ref} does not parse: {error}",
            hint="The base ref predates the current grammar. Compare against a newer ref.",
        ) from error
