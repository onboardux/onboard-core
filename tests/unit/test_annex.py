"""The runtime annex: contracts §12, owner-ratified as CR-08 on 2026-08-06.

*Fails when* the idempotency lookup stops being keyed on `(scope_ref,
idempotency_key)`, when a race hands the loser its own record back, or when
`agent_run` finds its way into the canonical manifest. *Matters because* PRD
F13.5 promises a replay returns the recorded result with **zero** provider
calls, and every one of those defects turns that promise into a paid provider
call -- or, in the manifest case, puts our record of every model call we made
about a client's system into the bundle we hand that client. *No other
instrument catches them because* the conformance suite's case 12 drives replay
through the whole seam and would attribute the failure to the runner, and S7's
validation item 7 (`ls /tmp/b3/tables | grep agent_run`) catches the manifest
defect only after an export has been written.
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

import adopt_schema
from adopt_agent.annex import AgentRunRecord, AnnexRecords
from adopt_obs import new_id
from adopt_store.annex import SqliteAnnexRecords, annex_path, open_annex

pytestmark = pytest.mark.unit


def _record(*, scope_ref: str, key: str, run_id: str | None = None) -> AgentRunRecord:
    return AgentRunRecord(
        id=run_id or new_id("ag"),
        scope_ref=scope_ref,
        idempotency_key=key,
        skill_ref="skills/detect/v1",
        skill_sha256="0" * 64,
        inputs_sha256="1" * 64,
        adapter="fake_recorded",
        model="recorded",
        params_hash="2" * 64,
        status="ok",
        input_tokens=10,
        output_tokens=20,
        cost_usd=0.0,
        wall_ms=5,
        trace_json=json.dumps({"steps": []}),
        output_ref=None,
        created_at="2026-08-06T09:00:00.000Z",
    )


@pytest.fixture
def annex(tmp_path: Path) -> Iterator[SqliteAnnexRecords]:
    with open_annex(tmp_path / ".adopt" / "runtime.db") as records:
        yield records


def test_a_recorded_run_is_found_by_its_scope_and_key(annex: SqliteAnnexRecords) -> None:
    stored = annex.record_run(_record(scope_ref="northwind/acme-erp", key="k-1"))

    found = annex.find_run(scope_ref="northwind/acme-erp", idempotency_key="k-1")

    assert found is not None
    assert found == stored


def test_an_unrecorded_key_is_none_rather_than_an_error(annex: SqliteAnnexRecords) -> None:
    # `None` is what permits a provider call. An implementation that raised
    # would make the first run of every key an error path.
    assert annex.find_run(scope_ref="northwind/acme-erp", idempotency_key="never") is None


def test_the_same_key_in_a_different_scope_is_a_different_run(
    annex: SqliteAnnexRecords,
) -> None:
    """Two engagements may legitimately choose the same key.

    A lookup keyed on the key alone passes every single-tenant test and hands
    one client's recorded run to another client's replay.
    """
    mine = annex.record_run(_record(scope_ref="northwind/acme-erp", key="shared"))
    theirs = annex.record_run(_record(scope_ref="contoso/ledger", key="shared"))

    assert mine.id != theirs.id
    assert annex.find_run(scope_ref="northwind/acme-erp", idempotency_key="shared") == mine
    assert annex.find_run(scope_ref="contoso/ledger", idempotency_key="shared") == theirs


def test_the_loser_of_a_race_is_handed_the_winners_record(annex: SqliteAnnexRecords) -> None:
    """The return value, not the absence of an exception, is the guarantee.

    A realization that returned the caller's own record would satisfy the type
    and send the loser back to a provider call that has already been paid for.
    """
    winner = annex.record_run(_record(scope_ref="northwind/acme-erp", key="raced"))
    loser_attempt = _record(scope_ref="northwind/acme-erp", key="raced")

    observed = annex.record_run(loser_attempt)

    assert observed.id == winner.id
    assert observed.id != loser_attempt.id


def test_the_realization_satisfies_the_port(annex: SqliteAnnexRecords) -> None:
    """Structural conformance, checked by mypy rather than asserted at runtime.

    The assignment below is the test: `mypy --strict` rejects it the moment
    `SqliteAnnexRecords` and `AnnexRecords` disagree on a signature, which
    `isinstance` against a `runtime_checkable` Protocol would not -- it checks
    only that the method names exist.
    """
    port: AnnexRecords = annex

    assert port.find_run(scope_ref="x/y", idempotency_key="z") is None


def test_agent_run_is_absent_from_the_canonical_manifest() -> None:
    """CR-08's whole content, asserted where it is cheap to assert.

    If `agent_run` ever enters the manifest it becomes exportable, and the
    bundle we hand a client starts carrying our record of every model call we
    made about their system.
    """
    assert "agent_run" not in adopt_schema.load_manifest().tables


def test_opening_an_existing_annex_twice_preserves_its_rows(tmp_path: Path) -> None:
    """`IF NOT EXISTS` is what makes the annex openable without a version stamp.

    A DDL that recreated the table would silently empty the idempotency record
    on the next process start, and every replay would become a provider call.
    """
    path = tmp_path / ".adopt" / "runtime.db"
    with open_annex(path) as first:
        first.record_run(_record(scope_ref="northwind/acme-erp", key="persist"))

    with open_annex(path) as second:
        assert second.find_run(scope_ref="northwind/acme-erp", idempotency_key="persist")


def test_the_annex_carries_no_user_version(tmp_path: Path) -> None:
    """The annex is outside `schema_version` (CR-08), and says so in the file.

    A stamped annex is one somebody migrates alongside the canonical store,
    which is the coupling the ratification exists to prevent.
    """
    path = tmp_path / ".adopt" / "runtime.db"
    with open_annex(path):
        pass

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version;").fetchone()[0] == 0
    finally:
        connection.close()


def test_the_annex_sits_beside_the_store_it_belongs_to(tmp_path: Path) -> None:
    """An annex beside a *different* store answers for runs never made against it."""
    assert annex_path(tmp_path / "sub" / "store.db") == tmp_path / "sub" / "runtime.db"
