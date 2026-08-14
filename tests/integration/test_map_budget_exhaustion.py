"""A budget-exhausted run exits 3 with usable output -- `02` §8, `01` F11.2.

`05` S1.3's Final Output Validation line 6, as a test rather than a manual drill:
*"A budget-exhausted run exits 3, emits stage-1 artifacts, populates
`truncated_families[]` and commits the transaction."*

*Defect sentence.* Fails when budget exhaustion loses the work that completed,
stops naming what is missing, or reports itself as a failure; matters because
`02` §8 makes exit 3 *"a successful run with less output"* that callers treat as
usable -- a partial map claiming completeness would be worse than no map, and one
that refused to commit would throw away wall-clock time a client paid for; no
other instrument catches it because an aborted run and a truncated run both end
with a non-zero exit and a shorter map.

**Exhaustion is produced by arithmetic, not by waiting.** `Budget.now` is
injected and the deadline is set in the past, so the drill runs in milliseconds --
`03` §5 bans sleeps in tests, and a drill that waited out `MAP_TOTAL_BUDGET_S`
would take an hour.
"""

import json
from pathlib import Path

import pytest
from adopt_extractors_common import pack
from adopt_map.orchestrator import run as run_map
from adopt_map.plugins import ExtractorRegistry
from adopt_map.report import RunResult

from adopt_model import Identity, IdentityRevision
from adopt_obs import MapExitCode
from adopt_store.api import SqliteStoreHandle
from tests.build1_conftest import build_scoped_store, surface_writer_for

pytestmark = pytest.mark.integration

_TREE = Path("fixtures/repos/poisoned-import")


def _run(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    *,
    total_budget_s: float,
) -> tuple[RunResult, SqliteStoreHandle, Path]:
    handle, scopes = build_scoped_store(tmp_path_factory.mktemp("budget"))
    registry = ExtractorRegistry()
    registry.register_all(pack())
    out = tmp_path / "out"
    result = run_map(
        resolved=scopes["prod"],
        root=_TREE,
        registry=registry,
        adopt_version="0.3.0",
        writer=surface_writer_for(handle),
        coverage_records=handle.coverage_records(),
        cache=handle.backend,
        out_dir=out,
        formats=("md", "json"),
        total_budget_s=total_budget_s,
        stage1_budget_s=total_budget_s,
        sequential=True,
    )
    return result, handle, out


def test_an_exhausted_budget_exits_three_and_names_what_is_missing(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Exit 3 with `truncated_families[]` populated -- the whole of `02` §8's row."""
    result, _, _ = _run(tmp_path, tmp_path_factory, total_budget_s=-1.0)
    assert result.exit_code == MapExitCode.PARTIAL_BUDGET_EXHAUSTED == 3
    assert result.truncated_families, "exit 3 with nothing named as truncated"


def test_the_stage1_artifacts_exist_after_an_exhausted_run(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """*"Stage-1 artifacts at minimum"*. The reader gets a map, not an error page."""
    _, _, out = _run(tmp_path, tmp_path_factory, total_budget_s=-1.0)
    assert (out / "surface.md").is_file()
    assert (out / "run_report.json").is_file()
    surface = (out / "surface.md").read_text(encoding="utf-8")
    assert "Truncated by budget" in surface.split("## 8. Inventory")[0]


def test_the_transaction_commits_what_completed(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """`02` §8: *"transaction committed with what completed"*.

    The store is opened and read back, so this is a claim about rows rather than
    about a counter the run happened to return.
    """
    result, handle, _ = _run(tmp_path, tmp_path_factory, total_budget_s=-1.0)
    records = handle.export_records()
    identities = list(records.table_rows("identity", Identity))
    revisions = list(records.table_rows("identity_revision", IdentityRevision))
    assert result.write_result is not None
    # A run stopped at the first budget check may legitimately have nothing to
    # commit; what it may never do is return counters that disagree with the
    # store. That is the assertion -- the report and the rows tell one story.
    assert len(identities) == result.write_result.identities_seen
    assert len(revisions) >= len(identities)


def test_the_run_report_records_the_truncation_for_a_machine(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """`02` §9.3. An integrator scripting exit 3 reads the report, not the prose."""
    result, _, out = _run(tmp_path, tmp_path_factory, total_budget_s=-1.0)
    report = json.loads((out / "run_report.json").read_text(encoding="utf-8"))
    assert report["exit_code"] == 3
    assert report["truncated_families"] == list(result.truncated_families)
    assert any(entry["status"] == "truncated" for entry in report["extractors"])


def test_a_generous_budget_completes_at_exit_zero(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """**The control.** Without it, a run that always exited 3 would pass every
    assertion above -- and exit 3 would stop meaning anything."""
    result, _, _ = _run(tmp_path, tmp_path_factory, total_budget_s=3_600.0)
    assert result.exit_code == MapExitCode.COMPLETE == 0
    assert result.truncated_families == ()
    assert result.total_facts() > 0
