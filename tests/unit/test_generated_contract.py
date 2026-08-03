"""Every canonical table has a model and a DDL table, and they agree.

*Fails when* a target drifts from the manifest -- a model missing a field, a DDL
table missing a column, a table emitted before something it references.
*Matters because* the generated models are the only validators in the product
(contracts §1.4): a column the model does not know is a column that silently
fails to round-trip through the export, and a forward `REFERENCES` is rejected
outright by Postgres and deferred to first insert by SQLite. *No other instrument
catches it because* each of the four targets is generated independently, so any
one of them can be wrong while the other three are right.

One contract test, not one per table: re-testing generated output table by table
tests the generator's loop rather than the contract.
"""

import re

import pytest

from adopt_model import __all__ as MODEL_EXPORTS
from adopt_schema.emitters import postgres, sqlite
from adopt_schema.emitters._shared import class_name, field_name
from adopt_schema.generate import render_all
from adopt_schema.manifest import Manifest, load_manifest

_CREATE_TABLE_RE = re.compile(r"CREATE TABLE (\w+) \((.*?)\n\);", re.DOTALL)


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    return load_manifest()


def _ddl_columns(ddl: str) -> dict[str, list[str]]:
    tables: dict[str, list[str]] = {}
    for name, body in _CREATE_TABLE_RE.findall(ddl):
        columns: list[str] = []
        for raw in body.splitlines():
            token = raw.strip().split(" ")[0].rstrip(",")
            if token and token not in {"PRIMARY", "UNIQUE"}:
                columns.append(token)
        tables[name] = columns
    return tables


@pytest.mark.unit
def test_every_table_has_a_model_whose_fields_match_the_ddl(manifest: Manifest) -> None:
    ddl_tables = _ddl_columns(sqlite.emit(manifest))

    for name, table in manifest.tables.items():
        model = class_name(name)
        assert model in MODEL_EXPORTS, f"{name} has no generated model"
        assert name in ddl_tables, f"{name} has no emitted DDL table"

        declared = [column.name for column in table.columns]
        assert ddl_tables[name] == declared, f"{name}: DDL columns differ from the manifest"


@pytest.mark.unit
def test_model_fields_cover_every_column(manifest: Manifest) -> None:
    import adopt_model

    for name, table in manifest.tables.items():
        model = getattr(adopt_model, class_name(name))
        fields = set(model.model_fields)

        assert fields == {field_name(column.name) for column in table.columns}


@pytest.mark.unit
def test_emitted_order_is_a_valid_topological_order(manifest: Manifest) -> None:
    """CR-18. SQLite would accept a forward reference and fail at first insert."""
    emitted: list[str] = []
    for name, table in manifest.ordered_tables():
        for column in table.columns:
            parts = column.reference_parts
            if parts is not None and parts[0] != name:
                assert parts[0] in emitted, (
                    f"{name}.{column.name} references {parts[0]}, which is emitted later"
                )
        emitted.append(name)


@pytest.mark.unit
def test_both_dialects_declare_the_same_tables_and_columns(manifest: Manifest) -> None:
    """The realizations differ only by the mechanical delta in contracts §3.1."""
    assert _ddl_columns(sqlite.emit(manifest)) == _ddl_columns(postgres.emit(manifest))


@pytest.mark.unit
def test_every_scoped_table_gets_a_forced_policy(manifest: Manifest) -> None:
    """PRD F4.2: RLS is expressed from row scope, and `FORCE`d so application
    code cannot read across scope even when it forgets to filter."""
    ddl = postgres.emit(manifest)

    for name, table in manifest.tables.items():
        if table.scope_level in {"global", "unscoped"}:
            assert f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;" not in ddl
            continue
        assert f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;" in ddl
        assert f"ALTER TABLE {name} FORCE  ROW LEVEL SECURITY;" in ddl
        assert f"CREATE POLICY scope_isolation ON {name}" in ddl


@pytest.mark.unit
def test_no_canonical_table_is_unscoped(manifest: Manifest) -> None:
    """Every table is either `global` or has a derivable scope.

    *Fails when* a table is added whose row-level-security policy cannot be
    derived. *Matters because* such a table holds tenant data outside tenant
    isolation while looking entirely normal in the DDL. *No other instrument
    catches it because* the emitter emits an explanatory comment where the policy
    would be and CI stays green -- the store simply has a hole in it.

    `unscoped` remains expressible on purpose: if a future table genuinely cannot
    be scoped, the loader forces a written reason rather than accepting silence,
    and this test is the alarm that makes the decision reach a human. `approval`
    was the one such table and CR-28 closed it.
    """
    unscoped = {
        name: table.unscoped_reason
        for name, table in manifest.tables.items()
        if table.scope_level == "unscoped"
    }

    assert unscoped == {}, (
        "these tables hold rows no scope can reach, so row-level security cannot "
        f"protect them: {unscoped}"
    )


@pytest.mark.unit
def test_the_runtime_annex_is_not_in_the_canonical_schema(manifest: Manifest) -> None:
    """CR-08/CR-11: `agent_run` lives in a separate store and workflow state in
    DBOS's own tables. Either appearing here would silently widen the export."""
    assert "agent_run" not in manifest.tables
    assert not [name for name in manifest.tables if name.startswith("workflow")]


@pytest.mark.unit
def test_generated_files_carry_the_do_not_edit_notice(manifest: Manifest) -> None:
    for path, content in render_all(manifest).items():
        assert "DO NOT EDIT" in content, f"{path} does not say it is generated"
