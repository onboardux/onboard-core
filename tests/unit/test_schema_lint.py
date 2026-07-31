"""The additive-only linter rejects each breaking change and permits each widening.

*Fails when* a rule stops firing, or when a legal widening starts firing.
*Matters because* this gate is the only thing standing between a convenient edit
and a store in the field that the next binary cannot read -- and the whole reason
the schema was rebuilt from scratch was to avoid ever carrying such an edit.
*No other instrument catches it because* every one of these changes is valid YAML
that loads, generates and applies perfectly well against an empty database.

One row per rule, plus one legal row per widening edge and per additive shape.
"""

from typing import Any

import pytest

from adopt_schema.lint import lint
from adopt_schema.manifest import Manifest


def _manifest(**overrides: Any) -> Manifest:
    base: dict[str, Any] = {
        "schema_version": 3,
        "export_version": 3,
        "enums": {"status": {"since": 3, "values": ["a", "b"]}},
        "tables": {
            "item": {
                "since": 3,
                "purpose": "A thing.",
                "scope_level": "global",
                "exportable": True,
                "primary_key": ["id"],
                "columns": [
                    {"name": "id", "type": "id", "nullable": False},
                    {"name": "count", "type": "int", "nullable": False},
                    {"name": "note", "type": "text", "nullable": True},
                    {"name": "state", "type": "enum(status)", "nullable": False},
                ],
                "indexes": [{"name": "idx_item_state", "columns": ["state"]}],
            }
        },
    }
    base.update(overrides)
    return Manifest.model_validate(base)


def _with_columns(columns: list[dict[str, Any]]) -> Manifest:
    manifest = _manifest()
    payload = manifest.model_dump()
    payload["tables"]["item"]["columns"] = columns
    return Manifest.model_validate(payload)


def _columns() -> list[dict[str, Any]]:
    return [dict(column) for column in _manifest().model_dump()["tables"]["item"]["columns"]]


def _retype(name: str, new_type: str) -> Manifest:
    columns = _columns()
    for column in columns:
        if column["name"] == name:
            column["type"] = new_type
    return _with_columns(columns)


def _breaking_cases() -> list[tuple[str, Manifest, Manifest, str]]:
    """Each row is `(id, base, head, expected rule)` -- base and head per case.

    Narrowing rows need a *widened* base, so every case carries its own pair
    rather than sharing one and reversing itself for two of them.
    """
    no_tables = _manifest().model_dump()
    no_tables["tables"] = {}

    without_note = _with_columns([c for c in _columns() if c["name"] != "note"])

    tightened = _columns()
    for column in tightened:
        if column["name"] == "note":
            column["nullable"] = False
    not_null_note = _with_columns(tightened)

    changed_key = _manifest().model_dump()
    changed_key["tables"]["item"]["primary_key"] = ["id", "count"]

    smaller_enum = _manifest().model_dump()
    smaller_enum["enums"]["status"]["values"] = ["a"]

    dropped_index = _manifest().model_dump()
    dropped_index["tables"]["item"]["indexes"] = []

    edited_since = _manifest().model_dump()
    edited_since["tables"]["item"]["since"] = 4

    return [
        ("table-removed", _manifest(), Manifest.model_validate(no_tables), "table-removed"),
        ("column-removed", _manifest(), without_note, "column-removed"),
        (
            "type-narrowed-real-to-int",
            _retype("count", "real"),
            _manifest(),
            "type-narrowed",
        ),
        ("type-narrowed-text-to-json", _manifest(), _retype("note", "json"), "type-narrowed"),
        ("nullability-tightened", _manifest(), not_null_note, "nullability-tightened"),
        (
            "primary-key-changed",
            _manifest(),
            Manifest.model_validate(changed_key),
            "primary-key-changed",
        ),
        (
            "enum-value-removed",
            _manifest(),
            Manifest.model_validate(smaller_enum),
            "enum-value-removed",
        ),
        ("index-removed", _manifest(), Manifest.model_validate(dropped_index), "index-removed"),
        ("since-edited", _manifest(), Manifest.model_validate(edited_since), "since-edited"),
    ]


BREAKING = _breaking_cases()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("base", "head", "rule"),
    [(base, head, rule) for _, base, head, rule in BREAKING],
    ids=[name for name, _, _, _ in BREAKING],
)
def test_rejects_non_additive_change(base: Manifest, head: Manifest, rule: str) -> None:
    assert rule in {violation.rule for violation in lint(base, head)}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("base", "head"),
    [
        pytest.param(_manifest(), _retype("count", "real"), id="widen-int-to-real"),
        pytest.param(_manifest(), _retype("note", "md"), id="widen-text-to-md"),
        pytest.param(
            _with_columns(
                [dict(c, nullable=False) if c["name"] == "note" else c for c in _columns()]
            ),
            _manifest(),
            id="widen-not-null-to-nullable",
        ),
        pytest.param(_manifest(), _manifest(), id="no-change"),
    ],
)
def test_permits_widening(base: Manifest, head: Manifest) -> None:
    assert lint(base, head) == []


@pytest.mark.unit
def test_permits_enum_superset() -> None:
    head = _manifest().model_dump()
    head["enums"]["status"]["values"] = ["a", "b", "c"]

    assert lint(_manifest(), Manifest.model_validate(head)) == []


@pytest.mark.unit
def test_permits_additions() -> None:
    head = _manifest().model_dump()
    head["tables"]["item"]["columns"].append({"name": "extra", "type": "text", "nullable": True})
    head["tables"]["item"]["indexes"].append({"name": "idx_item_extra", "columns": ["extra"]})
    head["tables"]["other"] = {
        "since": 4,
        "purpose": "Added later.",
        "scope_level": "global",
        "exportable": True,
        "primary_key": ["id"],
        "columns": [{"name": "id", "type": "id", "nullable": False}],
    }

    assert lint(_manifest(), Manifest.model_validate(head)) == []


@pytest.mark.unit
def test_an_absent_base_manifest_is_not_a_violation() -> None:
    """The commit that introduces the manifest has nothing behind it to break."""
    assert lint(None, _manifest()) == []


@pytest.mark.unit
def test_every_violation_names_the_table_the_rule_and_both_remedies() -> None:
    """A gate that only says "no" is a gate people route around.

    There are exactly two ways to make a breaking change additive, and a message
    that names neither leaves the reader to guess which one applies to them.
    """
    without_note = _with_columns([c for c in _columns() if c["name"] != "note"])

    rendered = lint(_manifest(), without_note)[0].render()

    assert "column-removed" in rendered
    assert "item.note" in rendered
    assert "add a new column" in rendered
    assert "retired_in_version" in rendered
