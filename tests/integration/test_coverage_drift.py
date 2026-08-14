"""The cache is written and never trusted -- `01` F10, `03` §5.7.

*Defect sentence.* Fails when a corrupted `covered_cache` stops being detected
within the same run, when a cold cache starts alarming, or when a printed figure
stops tracing to `recompute_coverage()`; matters because `01` F10.2 makes the
recompute the only authority and a cache that quietly becomes authoritative for
one number reintroduces exactly the invisible coverage decay the v3 rebuild
deleted; no other instrument catches it because a wrong cache produces a
*plausible* ratio.
"""

import json
from pathlib import Path

import pytest
from adopt_extractors_common import StubExtractor
from adopt_extractors_common.stub import MANIFEST
from adopt_map.coverage import COVERAGE_SOURCE, report_coverage
from adopt_map.writer import FactBatch

from adopt_model import Identity
from adopt_store.api import SqliteStoreHandle
from tests.build1_conftest import build_scoped_store, context_for, surface_writer_for

pytestmark = pytest.mark.integration


@pytest.fixture
def populated(tmp_path_factory: pytest.TempPathFactory) -> tuple[SqliteStoreHandle, str, str]:
    """A store with one completed run in it, so coverage has something to report."""
    handle, scopes = build_scoped_store(tmp_path_factory.mktemp("coverage"))
    resolved = scopes["prod"]
    surface_writer_for(handle).write_batches(
        resolved=resolved,
        batches=(
            FactBatch(manifest=MANIFEST, facts=tuple(StubExtractor().extract(context_for(".")))),
        ),
        vcs_revision=None,
    )
    return handle, resolved.system_id, resolved.environment_id


def test_every_identity_this_build_writes_is_uncovered_and_says_why(
    populated: tuple[SqliteStoreHandle, str, str],
) -> None:
    """**B1-CR-62.** The ratio is 0.0 on every run, and it is not ours to fix.

    Build 0's coverage rule needs six inputs and input 4 is *"an applicable
    audience and environment"*, realized as `audience_count == 0` blocking the
    identity. `audience_tag` is on `00` §5's **never writes** list for Build 1.
    So `01` §6's M1 target of `>= 0.60` cannot be moved by anything in this build
    and `05` S1.4's `jq '.coverage.ratio' >= 0.60` is unreachable as written.

    This test exists to keep that finding *measured* rather than remembered. It
    is deliberately written to fail the day the premise changes -- if a future
    build supplies audience tags, the ratio moves and this test says so loudly
    instead of the finding quietly rotting in a register row.
    """
    handle, system, environment = populated
    report = report_coverage(
        handle.coverage_records(),
        handle.backend,
        system_id=system,
        environment_id=environment,
    )
    assert report.discovered > 0
    assert report.covered == 0
    assert report.ratio == 0.0
    assert report.blocked_by == {"audience_or_environment_inapplicable": report.discovered}
    assert report.blockers()[0].startswith("audience_or_environment_inapplicable")


def test_a_first_run_does_not_alarm(populated: tuple[SqliteStoreHandle, str, str]) -> None:
    """A first run has nothing to disagree with, so nothing alarms.

    True today for a reason worth naming: because of B1-CR-62 every identity is
    uncovered, so the `covered_cache` column's `false` default *agrees* with the
    recompute. The cold/drift split below is what keeps this true when that
    changes.
    """
    handle, system, environment = populated
    report = report_coverage(
        handle.coverage_records(),
        handle.backend,
        system_id=system,
        environment_id=environment,
    )
    assert report.drift == ()
    assert report.cache_agreement is True
    assert report.rows_written == report.discovered


def test_a_cache_that_was_never_written_is_cold_rather_than_drifted(
    populated: tuple[SqliteStoreHandle, str, str],
) -> None:
    """B1-CR-59, proven by planting the state rather than waiting for it.

    A run cannot currently *produce* a cold disagreement -- B1-CR-62 makes every
    identity uncovered, so the default agrees. The guard still matters: the day
    anything makes a surface identity covered, a first run would otherwise alarm
    on every row. So the state is planted directly -- `covered_cache` flipped to
    true with `covered_cache_at` still NULL, which is exactly "never written, and
    now disagreeing" -- because a guard proven only by a state the build cannot
    reach is a guard nobody has seen work.
    """
    handle, system, environment = populated
    with handle.backend.transaction():
        handle.backend.execute("UPDATE identity SET covered_cache = 1, covered_cache_at = NULL")

    report = report_coverage(
        handle.coverage_records(),
        None,
        system_id=system,
        environment_id=environment,
        rebuild=False,
    )
    assert report.cold == report.discovered, "a never-written cache was not classified cold"
    assert report.drift == (), "a cold cache alarmed as drift"
    assert report.cache_agreement is True


def test_a_deliberately_corrupted_cache_is_detected_within_the_same_run(
    populated: tuple[SqliteStoreHandle, str, str],
) -> None:
    """`01` F10.3's acceptance signal, exactly.

    The cache is written once (so `covered_cache_at` is set and the row is no
    longer cold), then flipped. The next report must alarm -- **before** it
    rebuilds, because a rebuild that ran first would be comparing the authority
    against a cache it had just written from the authority.
    """
    handle, system, environment = populated
    report_coverage(
        handle.coverage_records(), handle.backend, system_id=system, environment_id=environment
    )

    # Corrupt it. This is the planted defect, and it is planted through the
    # store's own connection rather than through any Build 1 code path -- there
    # is no Build 1 code path that could write a wrong cache, which is the point.
    with handle.backend.transaction():
        handle.backend.execute("UPDATE identity SET covered_cache = NOT covered_cache")

    drifted = report_coverage(
        handle.coverage_records(), handle.backend, system_id=system, environment_id=environment
    )
    assert drifted.drift, "a corrupted cache was not detected"
    assert drifted.cache_agreement is False
    assert "CACHE DRIFT" in drifted.headline()


def test_the_drift_is_repaired_only_after_it_has_been_reported(
    populated: tuple[SqliteStoreHandle, str, str],
) -> None:
    """`03` §5.7 invariant 2: alarms rather than **silently** self-heals.

    Rebuilding after the alarm is not silent self-healing -- the evidence
    survives the repair, in the returned report and in `run_report.json`. Leaving
    a known-wrong cache in place would be worse.
    """
    handle, system, environment = populated
    report_coverage(
        handle.coverage_records(), handle.backend, system_id=system, environment_id=environment
    )
    with handle.backend.transaction():
        handle.backend.execute("UPDATE identity SET covered_cache = NOT covered_cache")

    first = report_coverage(
        handle.coverage_records(), handle.backend, system_id=system, environment_id=environment
    )
    second = report_coverage(
        handle.coverage_records(), handle.backend, system_id=system, environment_id=environment
    )
    assert first.drift != ()
    assert second.drift == (), "the rebuild did not restore agreement"


def test_looking_does_not_repair_when_rebuild_is_off(
    populated: tuple[SqliteStoreHandle, str, str],
) -> None:
    """`adopt surface coverage`'s default, and `store doctor`'s.

    A command whose default rewrote the cache would destroy the evidence of
    whatever wrote it wrong, every time an operator ran it to find out what was
    wrong.
    """
    handle, system, environment = populated
    report_coverage(
        handle.coverage_records(), handle.backend, system_id=system, environment_id=environment
    )
    with handle.backend.transaction():
        handle.backend.execute("UPDATE identity SET covered_cache = NOT covered_cache")

    looked = report_coverage(
        handle.coverage_records(),
        None,
        system_id=system,
        environment_id=environment,
        rebuild=False,
    )
    assert looked.drift != ()
    assert looked.rows_written == 0
    again = report_coverage(
        handle.coverage_records(),
        None,
        system_id=system,
        environment_id=environment,
        rebuild=False,
    )
    assert again.drift != (), "looking repaired the drift it was asked to report"


def test_every_reported_figure_names_recompute_as_its_source(
    populated: tuple[SqliteStoreHandle, str, str],
) -> None:
    """`02` §9.2's `coverage.source` is the literal `"recompute"`.

    In the artifact rather than only in our code, so a reader can check the
    authority without reading the implementation.
    """
    handle, system, environment = populated
    report = report_coverage(
        handle.coverage_records(), handle.backend, system_id=system, environment_id=environment
    )
    block = report.as_report_block()
    assert block["source"] == COVERAGE_SOURCE == "recompute"
    assert "recompute" in report.headline()


def test_an_empty_scope_reports_a_ratio_of_zero_and_not_one(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A system with no identities has covered nothing.

    Reporting 1.0 would say an empty scope is perfectly covered, which is the
    shape of claim `01` §1.6 exists to refuse -- and it is the shape a naive
    `covered / discovered or 1` produces.
    """
    handle, scopes = build_scoped_store(tmp_path_factory.mktemp("empty"))
    resolved = scopes["prod"]
    report = report_coverage(
        handle.coverage_records(),
        None,
        system_id=resolved.system_id,
        environment_id=resolved.environment_id,
        rebuild=False,
    )
    assert report.discovered == 0
    assert report.ratio == 0.0


def test_the_by_kind_breakdown_parses_the_kind_out_of_the_canonical_uri(
    populated: tuple[SqliteStoreHandle, str, str],
) -> None:
    """Read from the URI via `parse_uri`, not joined from a second column.

    The URI round-trips, so a kind read this way is the kind the identity was
    minted with rather than a second copy that could drift.
    """
    handle, system, environment = populated
    report = report_coverage(
        handle.coverage_records(),
        None,
        system_id=system,
        environment_id=environment,
        rebuild=False,
    )
    kinds = {row.uri.split("/")[6] for row in _identities(handle)}
    assert set(report.by_kind) == kinds
    assert sum(discovered for _, discovered in report.by_kind.values()) == report.discovered


def _identities(handle: SqliteStoreHandle) -> list[Identity]:
    return list(handle.export_records().table_rows("identity", Identity))


def test_the_source_scan_finds_covered_cache_named_only_in_coverage_py() -> None:
    """`05` S1.3's Final Output Validation line 5, as a test rather than a shell
    pipeline -- so it fails in CI on the machine that broke it.

    The `no-covered-cache-read` import contract enforces this too; the difference
    is that the contract fails a lint job and this fails the suite, and `01` F10.2
    is worth both.
    """
    root = Path(__file__).resolve().parents[2] / "packages" / "adopt-map" / "src"
    offenders = [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*.py"))
        if "covered_cache" in path.read_text(encoding="utf-8") and path.name != "coverage.py"
    ]
    assert offenders == [], f"covered_cache is named outside coverage.py: {offenders}"


def test_the_report_block_is_json_serialisable_for_both_artifacts() -> None:
    """`surface.json` and `run_report.json` both carry this block verbatim."""
    from adopt_map.coverage import CoverageReport

    block = CoverageReport(discovered=4, covered=3).as_report_block()
    assert json.loads(json.dumps(block)) == block
    assert block["ratio"] == 0.75
