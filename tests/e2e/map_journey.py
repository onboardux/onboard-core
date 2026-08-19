"""The harness `01` section 4's six `adopt map` journeys are driven through.

**Not a test module.** It holds the one composition six journey files share, for
the reason `03` section 7 gives for the journeys themselves: *"New features extend
these; they do not add journeys"* -- and six copies of a store-seeding block is six
places for the journeys to stop agreeing about what a scope is.

**Why these journeys drive the CLI rather than the writer.** `tests/integration/`
already exercises `SurfaceWriter`, the move rule, the budget and the guards, each
against its own seam. A journey that re-asserted their arithmetic would be a
pyramid duplicate charging full maintenance for zero detection. What a journey adds
is the **subject**: the command an operator types, its flag parsing, its store
wiring, its artifacts on disk and its exit code -- the composition, which is where
a defect survives every unit test in the build. So each journey asserts the
observable outcome of `01` section 4's numbered steps and its failure branches, and
nothing below them.

**Build 0's nine CUJs already own `test_cuj1.py` .. `test_cuj9.py`.** Build 1's six
are `test_map_cuj1.py` .. `test_map_cuj6.py`: two builds, two journey sets, two
PRDs numbering their own from one. Naming Build 1's by number alone would put two
different CUJ-1s in one directory.
"""

import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from adopt_cli.main import app
from adopt_model import Identity
from adopt_model._enums import Tier
from adopt_scope import Scope, ScopeNode
from adopt_store import open_store

#: `02` section 9.1 item 8 -- where the first screen ends and the inventory begins.
INVENTORY_HEADING: str = "## 8. Inventory"

#: Fixture trees, by the archetype whose journey reads them.
FIXTURES: dict[str, Path] = {
    "web": Path("fixtures/repos/django-orders"),
    "ai": Path("fixtures/repos/langgraph-support"),
    "stub": Path("fixtures/repos/stub-tree"),
    "poisoned": Path("fixtures/repos/poisoned-import"),
}


@dataclass
class RunOutcome:
    """One `adopt map` invocation, as an operator sees it."""

    exit_code: int
    stdout: str
    #: stdout **and** stderr. `02` section 8 puts the JSON object on stdout and the
    #: logs on stderr, and an abort's error envelope travels with the logs -- so a
    #: failure-branch journey reading only stdout would assert against an empty
    #: string and pass for the wrong reason.
    output: str
    payload: dict[str, Any]
    out_dir: Path

    @property
    def surface_md(self) -> str:
        return (self.out_dir / "surface.md").read_text(encoding="utf-8")

    @property
    def surface_json(self) -> dict[str, Any]:
        """The full run artifact.

        `--json` puts a summary on stdout and `02` section 9.2's `facts[]` is not
        in it: the fact list belongs to `surface.json`, which is where the
        labeled-corpus tooling reads it from. A journey asserting about facts
        reads the file rather than widening the stdout envelope.
        """
        artifact: dict[str, Any] = json.loads(
            (self.out_dir / "surface.json").read_text(encoding="utf-8")
        )
        return artifact

    @property
    def run_report(self) -> dict[str, Any]:
        report: dict[str, Any] = json.loads(
            (self.out_dir / "run_report.json").read_text(encoding="utf-8")
        )
        return report

    def first_screen(self) -> str:
        """Everything before the inventory heading.

        `02` section 9.1 makes the first screen's *order* normative and says a
        degradation that does not appear in it is a defect. A journey that
        searched the whole document would pass on a degradation buried on page
        forty, which is precisely the failure this build exists to prevent.
        """
        text = self.surface_md
        marker = text.find(INVENTORY_HEADING)
        return text if marker < 0 else text[:marker]


class Journey:
    """A scoped store, a copied tree, and `adopt map` run against them."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        fixture: str = "web",
        environments: Sequence[str] = ("prod",),
        tier: Tier = "T2",
        boundary: bool = True,
    ) -> None:
        self.root = tmp_path
        self.tree = tmp_path / "tree"
        shutil.copytree(FIXTURES[fixture], self.tree)
        self.store = tmp_path / "store.db"

        handle = open_store(self.store, migrate=True)
        facade = handle.scope()
        firm = facade.create_firm(slug="northwind", name="Northwind LLP")
        engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
        system = facade.create_system(
            engagement_id=engagement.id, slug="orders-api", name="Orders API"
        )
        if boundary:
            handle.boundary().declare(
                scope=Scope(
                    firm=ScopeNode(id=firm.id, slug=firm.slug),
                    system=ScopeNode(id=system.id, slug=system.slug),
                ),
                tier=tier,
                knowledge_plane_location="customer",
                control_plane_location="customer",
                permitted_outbound_categories=["metadata_only"],
            )
        self.environments = {
            slug: facade.create_environment(system_id=system.id, slug=slug, name=slug.title())
            for slug in environments
        }
        handle.close()

        self.firm_id = firm.id
        self.engagement_id = engagement.id
        self.system_id = system.id
        self.runner = CliRunner()
        self._runs = 0

    def map(
        self,
        *extra: str,
        environment: str | None = "prod",
        expect: int | None = 0,
    ) -> RunOutcome:
        """Run `adopt map` the way `02` section 8 documents it.

        `expect=None` accepts any exit code, which the failure-branch journeys
        need: an abort's whole claim is *which* code it returned, so a helper
        that asserted success would make those branches unassertable.
        """
        self._runs += 1
        out = self.root / f"out{self._runs}"
        argv = [
            "map",
            str(self.tree),
            "--firm",
            self.firm_id,
            "--engagement",
            self.engagement_id,
            "--system",
            self.system_id,
            "--store",
            str(self.store),
            "--out",
            str(out),
            "--json",
            *extra,
        ]
        if environment is not None:
            argv += ["--environment", self.environments[environment].id]
        result = self.runner.invoke(app, argv)
        payload: dict[str, Any] = {}
        if result.stdout.strip():
            try:
                parsed = json.loads(result.stdout)
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}
        if expect is not None:
            assert result.exit_code == expect, (
                f"expected exit {expect}, got {result.exit_code}\n{result.output}"
            )
        return RunOutcome(
            exit_code=result.exit_code,
            stdout=result.stdout,
            output=result.output,
            payload=payload,
            out_dir=out,
        )

    def identity_uris(self) -> set[str]:
        """Every URI in the store, read back through Build 0's export port.

        Read from the **store**, not from `surface.json`. A journey asserting that
        a staging run emitted no production URI has to look where the rows landed;
        the run artifact is this run's own account of itself and would agree with
        a writer that wrote the wrong thing.
        """
        handle = open_store(self.store, migrate=False)
        try:
            rows = handle.export_records().table_rows("identity", Identity)
            return {row.uri for row in rows}
        finally:
            handle.close()
