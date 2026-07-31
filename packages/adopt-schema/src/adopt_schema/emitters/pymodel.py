"""The generated `adopt_model` package: one validating model per table and enum.

The generated models are the **only** validators in the product (contracts §1.4).
No package re-declares a shape, every boundary is closed, and that closure is
what replaces whole categories of hand-written validation tests.

This emitter produces three modules and the package they live in. It may emit
nothing that imports outside `pydantic` and `adopt_const` -- the
`generated-purity` contract enforces that, and it is why `uri` renders as a
constrained `str` rather than as `adopt_identity`'s value object (CR-25).
"""

import json
from typing import Final

from adopt_schema.emitters._shared import (
    GENERATED_NOTICE,
    class_name,
    field_name,
    resolve_enum,
)
from adopt_schema.manifest import Column, Manifest

__all__ = ["emit_enums", "emit_package", "emit_tables"]

#: contracts §2.3, manifest type -> Python annotation.
TYPE_MAP: Final[dict[str, str]] = {
    "id": "str",
    "slug": "str",
    "uri": "str",
    "text": "str",
    "md": "str",
    "json": "dict[str, Any] | list[Any]",
    "int": "int",
    "real": "float",
    "bool": "bool",
    # Aware, not naive: contracts §1.2 stores UTC with a `Z` suffix, and a naive
    # datetime is how a local time silently becomes a stored value.
    "ts": "AwareDatetime",
}

_HEADER: Final[str] = "\n".join(f"# {line}" for line in GENERATED_NOTICE.splitlines())


def _enum_alias(enum_name: str) -> str:
    return class_name(enum_name)


def emit_enums(manifest: Manifest) -> str:
    lines = [
        '"""Enum vocabularies, generated from the canonical manifest."""',
        "",
        _HEADER,
        "",
        "from typing import Literal",
        "",
        "__all__ = [",
    ]
    lines.extend(f'    "{_enum_alias(name)}",' for name in sorted(manifest.enums))
    lines.append("]")
    lines.append("")

    for name in sorted(manifest.enums):
        enum = manifest.enums[name]
        if enum.is_integer:
            # A generated enum value is not a tunable, and `constants-sync`
            # cannot know that. The waiver goes on each member's own line rather
            # than above the block, because the gate reads a two-line window and
            # a block-level comment stops covering the fourth member onward.
            # Every waiver prints on every run, so this stays a visible decision.
            members = [
                f"{v},  # const-sync: ok -- manifest enum value, not a tunable."
                for v in enum.values
            ]
        else:
            members = [f'"{v}",' for v in enum.values]
        # One member per line with a trailing comma: the formatter's magic
        # trailing comma then keeps this shape whatever the line length, so the
        # emitter does not have to reimplement a line-wrapping algorithm to stay
        # byte-stable.
        lines.append(f"{_enum_alias(name)} = Literal[")
        lines.extend(f"    {member}" for member in members)
        lines.append("]")
    return "\n".join(lines) + "\n"


def _annotation(manifest: Manifest, column: Column) -> str:
    enum = resolve_enum(manifest, column)
    base = _enum_alias(column.enum_name or "") if enum is not None else TYPE_MAP[column.type]
    return f"{base} | None" if column.nullable else base


def _python_literal(value: object) -> str:
    """Render a default as Python source, double-quoted.

    `repr` would emit single quotes and the formatter would rewrite them, which
    means the generated file would never equal a fresh generation and
    `generate --check` would fail on every run.
    """
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_python_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        rendered = ", ".join(f"{json.dumps(k)}: {_python_literal(v)}" for k, v in value.items())
        return "{" + rendered + "}"
    raise TypeError(f"no Python rendering for default of type {type(value)!r}")


def _default_literal(column: Column) -> str | None:
    if column.nullable:
        return "None"
    if column.default is None:
        return None
    if column.type == "json" and isinstance(column.default, str):
        # The manifest declares the default as the SQL literal the DDL carries.
        # The model's default is the value that literal denotes, or the field's
        # declared type and its default would disagree.
        return _python_literal(json.loads(column.default))
    return _python_literal(column.default)


def _field(manifest: Manifest, column: Column) -> str:
    name = field_name(column.name)
    annotation = _annotation(manifest, column)
    default = _default_literal(column)

    if name != column.name:
        # `class` is a Python keyword and one column is named it. The wire name
        # stays authoritative; the alias is what a bundle and the DDL carry.
        assignment = f'Field(alias="{column.name}")'
        if default is not None:
            assignment = f'Field({default}, alias="{column.name}")'
        return f"    {name}: {annotation} = {assignment}"
    return f"    {name}: {annotation}" + (f" = {default}" if default is not None else "")


def emit_tables(manifest: Manifest) -> str:
    ordered = manifest.ordered_tables()
    # Only the enums some column actually constrains against. `value_event_type`
    # is declared but unreferenced -- source spec §4 gives `value_event.event_type`
    # no CHECK -- and importing it here would be an unused import in generated
    # code, which is a lint failure nobody can fix by hand.
    used = sorted(
        {
            column.enum_name
            for _, table in ordered
            for column in table.columns
            if column.enum_name is not None
        }
    )
    lines = [
        '"""One validating model per canonical table."""',
        "",
        _HEADER,
        "",
        "from typing import Any",
        "",
        "from pydantic import AwareDatetime, BaseModel, ConfigDict, Field",
        "",
        "from adopt_model._enums import (",
    ]
    lines.extend(f"    {_enum_alias(name)}," for name in used)
    lines.extend(
        [
            ")",
            "",
            "__all__ = [",
        ]
    )
    lines.extend(f'    "{class_name(name)}",' for name, _ in sorted(ordered))
    lines.extend(
        [
            "]",
            "",
            "",
            "_CONFIG = ConfigDict(",
            "    # Strict and closed: the schema is the egress allowlist.",
            '    extra="forbid",',
            "    populate_by_name=True,",
            "    # Several canonical columns legitimately start with `model_`",
            "    # (`model_card_ref`, `model_provider_version`), which is pydantic's",
            "    # protected prefix. The column names are the contract, so the",
            "    # protection is released rather than the columns renamed.",
            "    protected_namespaces=(),",
            ")",
        ]
    )

    for name, table in ordered:
        lines.extend(
            [
                "",
                "",
                f"class {class_name(name)}(BaseModel):",
                f'    """{table.purpose}"""',
                "",
                "    model_config = _CONFIG",
                "",
            ]
        )
        lines.extend(_field(manifest, column) for column in table.columns)
    return "\n".join(lines) + "\n"


def emit_package(manifest: Manifest) -> str:
    names = sorted(_enum_alias(n) for n in manifest.enums)
    models = sorted(class_name(n) for n in manifest.tables)
    lines = [
        '"""GENERATED typed models, one per table, enum and wire shape.',
        "",
        "Never hand-edited: CI regenerates this package and compares, and a hand",
        "edit is `SCHEMA_GENERATED_DRIFT`. This package imports only `pydantic`,",
        "enforced by the `generated-purity` contract, and holds no business logic.",
        "",
        "The error registry is **not** here -- it lives in `adopt_obs.errors`,",
        "because it is needed before this package is first generated (CR-21).",
        '"""',
        "",
        _HEADER,
        "",
        "from adopt_model._enums import (",
    ]
    lines.extend(f"    {name}," for name in names)
    lines.append(")")
    lines.append("from adopt_model._tables import (")
    lines.extend(f"    {name}," for name in models)
    lines.extend([")", "", "__all__ = ["])
    lines.extend(f'    "{name}",' for name in sorted(names + models))
    lines.append("]")
    return "\n".join(lines) + "\n"
