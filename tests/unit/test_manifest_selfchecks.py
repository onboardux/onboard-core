"""Every manifest self-check rejects the manifest it exists to reject.

*Fails when* a self-check stops catching its defect. *Matters because* the
manifest is the only schema authority: a defect that survives loading is emitted
into two dialects, the export schema and the generated models, and is then
discovered at whichever one somebody happens to run first -- or, for a foreign-key
cycle, at first insert in the field. *No other instrument catches it because* a
manifest that loads and a manifest that is correct produce identical output right
up to the point where they do not.

One row per check, and no rows beyond them: the tables are generated, so testing
generated output table by table would be the test-per-function reflex applied to
a schema.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from adopt_obs import AdoptError, ErrorCode
from adopt_schema.manifest import load_manifest


def _base() -> dict[str, Any]:
    """A minimal manifest that loads cleanly; every row below breaks one thing."""
    return {
        "schema_version": 3,
        "export_version": 3,
        "enums": {"status": {"since": 3, "values": ["a", "b"]}},
        "tables": {
            "firm": {
                "since": 3,
                "purpose": "The root scope.",
                "scope_level": "firm",
                "scope_ref": {"firm": "id"},
                "exportable": True,
                "primary_key": ["id"],
                "columns": [
                    {"name": "id", "type": "id", "nullable": False},
                    {"name": "state", "type": "enum(status)", "nullable": False},
                ],
            },
            "child": {
                "since": 3,
                "purpose": "A scoped child.",
                "scope_level": "firm",
                "scope_ref": {"via": ["firm_id"]},
                "exportable": True,
                "primary_key": ["id"],
                "columns": [
                    {"name": "id", "type": "id", "nullable": False},
                    {"name": "firm_id", "type": "id", "nullable": False, "references": "firm.id"},
                ],
                "indexes": [{"name": "idx_child_firm", "columns": ["firm_id"]}],
            },
        },
    }


def _write(tmp_path: Path, manifest: dict[str, Any]) -> Path:
    path = tmp_path / "canonical.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return path


def _unknown_key(m: dict[str, Any]) -> None:
    m["tables"]["firm"]["stowaway"] = True


def _no_primary_key(m: dict[str, Any]) -> None:
    del m["tables"]["firm"]["primary_key"]


def _primary_key_unknown_column(m: dict[str, Any]) -> None:
    m["tables"]["firm"]["primary_key"] = ["nope"]


def _fk_unknown_table(m: dict[str, Any]) -> None:
    m["tables"]["child"]["columns"][1]["references"] = "ghost.id"


def _fk_unknown_column(m: dict[str, Any]) -> None:
    m["tables"]["child"]["columns"][1]["references"] = "firm.ghost"


def _undeclared_enum(m: dict[str, Any]) -> None:
    m["tables"]["firm"]["columns"][1]["type"] = "enum(nowhere)"


def _unknown_type(m: dict[str, Any]) -> None:
    m["tables"]["firm"]["columns"][1]["type"] = "blob"


def _cycle(m: dict[str, Any]) -> None:
    m["tables"]["firm"]["columns"].append(
        {"name": "child_id", "type": "id", "references": "child.id"}
    )


def _duplicate_index_name(m: dict[str, Any]) -> None:
    m["tables"]["firm"]["indexes"] = [{"name": "idx_child_firm", "columns": ["id"]}]


def _duplicate_column(m: dict[str, Any]) -> None:
    m["tables"]["firm"]["columns"].append({"name": "id", "type": "text"})


def _scoped_without_scope_ref(m: dict[str, Any]) -> None:
    del m["tables"]["child"]["scope_ref"]


def _unscoped_without_reason(m: dict[str, Any]) -> None:
    m["tables"]["child"]["scope_level"] = "unscoped"
    del m["tables"]["child"]["scope_ref"]


def _global_with_scope_ref(m: dict[str, Any]) -> None:
    m["tables"]["child"]["scope_level"] = "global"


def _scope_ref_unknown_column(m: dict[str, Any]) -> None:
    m["tables"]["child"]["scope_ref"] = {"via": ["ghost_id"]}


def _index_unknown_column(m: dict[str, Any]) -> None:
    m["tables"]["child"]["indexes"][0]["columns"] = ["ghost"]


def _unsafe_identifier(m: dict[str, Any]) -> None:
    m["tables"]["Firm; DROP"] = m["tables"].pop("firm")
    m["tables"]["child"]["columns"][1]["references"] = "Firm; DROP.id"


CASES: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("unknown-key", _unknown_key, "grammar"),
    ("no-primary-key", _no_primary_key, "grammar"),
    ("primary-key-unknown-column", _primary_key_unknown_column, "primary key"),
    ("fk-unknown-table", _fk_unknown_table, "no `tables:` entry declares"),
    ("fk-unknown-column", _fk_unknown_column, "has no such column"),
    ("undeclared-enum", _undeclared_enum, "no `enums:` entry declares"),
    ("unknown-type", _unknown_type, "vocabulary"),
    ("fk-cycle", _cycle, "cycle"),
    ("duplicate-index-name", _duplicate_index_name, "one namespace"),
    ("duplicate-column", _duplicate_column, "declared twice"),
    ("scoped-without-scope-ref", _scoped_without_scope_ref, "no scope_ref"),
    ("unscoped-without-reason", _unscoped_without_reason, "unscoped_reason"),
    ("global-with-scope-ref", _global_with_scope_ref, "may not have both"),
    ("scope-ref-unknown-column", _scope_ref_unknown_column, "does not declare"),
    ("index-unknown-column", _index_unknown_column, "no such column"),
    ("unsafe-identifier", _unsafe_identifier, "plain lowercase identifier"),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "expected"),
    [(mutate, expected) for _, mutate, expected in CASES],
    ids=[name for name, _, _ in CASES],
)
def test_manifest_self_check_rejects(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None], expected: str
) -> None:
    manifest = _base()
    mutate(manifest)

    with pytest.raises(AdoptError) as raised:
        load_manifest(_write(tmp_path, manifest))

    assert raised.value.code is ErrorCode.MANIFEST_INVALID
    assert expected in raised.value.message


@pytest.mark.unit
def test_the_base_manifest_loads(tmp_path: Path) -> None:
    """The rows above prove rejection only if the unmutated fixture is accepted."""
    manifest = load_manifest(_write(tmp_path, _base()))

    assert sorted(manifest.tables) == ["child", "firm"]


@pytest.mark.unit
def test_a_table_may_declare_no_primary_key_deliberately(tmp_path: Path) -> None:
    """CR-04: `schema_meta` has none, and the rule is that none is never accidental."""
    manifest = _base()
    manifest["tables"]["firm"]["primary_key"] = []

    loaded = load_manifest(_write(tmp_path, manifest))

    assert loaded.tables["firm"].primary_key == []
