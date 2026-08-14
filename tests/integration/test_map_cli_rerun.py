"""`adopt map` twice, then a rename -- `05` S1.2's Final Output Validation, in CI.

Two of S1.2's validation lines are **shell drills against the real binary**:

    uv run adopt map && uv run adopt map --json | jq '.revisions_written'
    # rename a stub fixture file, re-run, confirm one `moved` revision

They are run literally at the end of the sprint. They are *also* run here,
through the same entry point, for the reason `03` §7 gives for the
`packaged-artifact` gate: a drill that only ever runs by hand runs once. What
this adds over `tests/integration/test_idempotence.py` and `test_moves.py` is the
**subject** -- those exercise `SurfaceWriter`; this exercises the command, its
flag parsing, its store wiring and its JSON envelope, which is where a
composition mistake lives.

The tree is copied into a temporary directory first: the drill renames a file,
and a test that mutated `fixtures/repos/stub-tree` in place would leave the
repository dirty and the next run comparing against a tree the last run edited.
"""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adopt_cli.main import app
from adopt_store import open_store

pytestmark = pytest.mark.integration

_FIXTURE = Path("fixtures/repos/stub-tree")


@pytest.fixture
def drill(tmp_path: Path) -> tuple[Path, Path, Callable[..., object]]:
    """A copied tree, a fresh store with one scope, and a `map` invocation."""
    tree = tmp_path / "tree"
    shutil.copytree(_FIXTURE, tree)

    store = tmp_path / "store.db"
    handle = open_store(store, migrate=True)
    facade = handle.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    from adopt_scope import Scope, ScopeNode

    handle.boundary().declare(
        scope=Scope(
            firm=ScopeNode(id=firm.id, slug=firm.slug),
            system=ScopeNode(id=system.id, slug=system.slug),
        ),
        tier="T2",
        knowledge_plane_location="customer",
        control_plane_location="customer",
        permitted_outbound_categories=["metadata_only"],
    )
    handle.close()

    runner = CliRunner()

    def _map() -> dict[str, object]:
        result = runner.invoke(
            app,
            [
                "map",
                str(tree),
                "--firm",
                firm.id,
                "--engagement",
                engagement.id,
                "--system",
                system.id,
                "--store",
                str(store),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert isinstance(payload, dict)
        return payload

    return tree, store, _map


def test_a_second_map_run_reports_zero_revisions_written(
    drill: tuple[Path, Path, Callable[..., dict[str, object]]],
) -> None:
    """`uv run adopt map && uv run adopt map --json | jq '.revisions_written'`."""
    _, _, run_map = drill

    first = run_map()
    assert sum(first["revisions_written"].values()) > 0  # type: ignore[union-attr]

    second = run_map()
    assert second["revisions_written"] == {"identity": 0, "knowledge": 0, "binding": 0}
    assert second["moves"] == []
    assert second["conflicts"] == []


def test_renaming_a_file_produces_one_move_and_no_orphan(
    drill: tuple[Path, Path, Callable[..., dict[str, object]]],
) -> None:
    """The rename drill, end to end through the command.

    `orders/api.py` becomes `orders/views.py`: every declaration keeps its
    signature, so `sem` matches and each identity moves rather than being
    orphaned beside an unrelated new one.
    """
    tree, _, run_map = drill
    run_map()

    (tree / "orders" / "api.py").rename(tree / "orders" / "views.py")
    second = run_map()

    moves = second["moves"]
    assert isinstance(moves, list)
    assert len(moves) == 2, "both declarations in the renamed file moved"
    for move in moves:
        assert "orders.api." in move["from"]
        assert "orders.views." in move["to"]
    assert second["conflicts"] == [], "an unambiguous rename declines nothing"

    third = run_map()
    assert third["moves"] == [], "the move is recorded once, not once per run"
    assert third["revisions_written"] == {"identity": 0, "knowledge": 0, "binding": 0}
