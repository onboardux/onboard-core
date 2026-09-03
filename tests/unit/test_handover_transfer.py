"""The close transaction: ownership moves, open items transfer, or nothing happens.

Two defects, and they are the build's two honesty rules:

* **The transfer itself.** *Fails when* the receiving owner does not end up
  owning the system, an already-named gap owner is overwritten, or an unowned
  gap is left unowned. *Matters because* v6.1 §6 Build 9 requires that
  *"unresolved items transfer with named owners rather than being closed to look
  complete"* -- a gap that arrives at the new team owned by nobody is exactly the
  item that rots, and a gap whose named owner was overwritten by a bulk
  reassignment loses a decision somebody made deliberately. *No other instrument
  catches it because* every one of those states is a well-formed row: nothing
  downstream can tell a correctly transferred gap from a wrongly transferred one.
* **The rollback.** *Fails when* any write escapes the transaction that the
  ownership post-check guards. *Matters because* the other honesty rule is that
  *"the event cannot close with the system unowned"* -- a half-applied close
  leaves an assignment, some re-dispositioned gaps and a `handover_closed` row
  claiming an acceptance that did not happen, and that row is what both parties
  keep. *No other instrument catches it because* the refusal itself would look
  correct: the operator sees an error, and the damage is in the store.

The overlap case is not synthetic. A system-scoped assignment beside an
engagement-scoped one is the **normal** state during a handover -- the
engagement owns everything, and this one system is moving out -- so "narrowest
wins" and "do not close the wider row" are both load-bearing here.
"""

import datetime as _dt
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from adopt_handover import HANDOVER_STARTED, fold

from adopt_cli.commands import _handover_support as support
from adopt_model import OwnershipAssignment
from adopt_obs import AdoptError, ErrorCode, ManualClock, new_id
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle

pytestmark = pytest.mark.unit

_START = _dt.datetime(2026, 9, 2, 12, 0, tzinfo=_dt.UTC)
_COUNTED = (
    "ownership_assignment",
    "coverage_gap",
    "audit_event",
    "value_event",
    "escalation",
    "identity",
    "knowledge_item",
    "binding",
)


@pytest.fixture
def handle(tmp_path: Path) -> Iterator[SqliteStoreHandle]:
    store = open_store(tmp_path / "store.db", migrate=True, clock=ManualClock(_START))
    yield store
    store.close()


@pytest.fixture
def system(handle: SqliteStoreHandle) -> support.ResolvedSystem:
    """A firm/engagement/system with one identity and no knowledge -> one gap."""
    facade = handle.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    created = facade.create_system(
        engagement_id=engagement.id, slug="orders-api", name="Orders API"
    )
    facade.create_environment(system_id=created.id, slug="prod", name="prod")
    return support.resolve_system(handle, system="orders-api", scope=None)


def _counts(store: Path) -> dict[str, int]:
    with sqlite3.connect(store) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
            for table in _COUNTED
        }


def _start(handle: SqliteStoreHandle, system: support.ResolvedSystem, **detail: Any) -> Any:
    """Open a handover that has reached the snapshot, so `close` is permitted."""
    from adopt_handover import (
        HANDOVER_ELICITED,
        HANDOVER_PACK_EMITTED,
        HANDOVER_SNAPSHOT_TAKEN,
        HANDOVER_VERIFIED,
    )

    payload = {
        "scope": system.scope_path,
        "environment_id": system.environment_id,
        "receiving_owner": "client-platform",
        "is_group": True,
        "opening": {},
    }
    payload.update(detail)
    row = support.write_event(
        handle,
        event_type=HANDOVER_STARTED,
        system=system,
        detail=payload,
        actor="alice",
        now=_START,
        handover_id=None,
    )
    for step in (
        HANDOVER_ELICITED,
        HANDOVER_PACK_EMITTED,
        HANDOVER_VERIFIED,
        HANDOVER_SNAPSHOT_TAKEN,
    ):
        support.write_event(
            handle,
            event_type=step,
            system=system,
            detail={"bundle_digest": "abc"} if step == HANDOVER_SNAPSHOT_TAKEN else {},
            actor="alice",
            now=_START,
            handover_id=row.id,
        )
    return fold(
        handle.operations_records().list_audit_events(
            event_types=__import__("adopt_handover").HANDOVER_EVENT_TYPES
        ),
        system_id=system.system_id,
    )[0]


def _assign(
    handle: SqliteStoreHandle,
    *,
    system_id: str | None,
    engagement_id: str | None,
    actor: str,
) -> str:
    assignment = OwnershipAssignment(
        id=new_id("own"),
        system_id=system_id,
        engagement_id=engagement_id,
        scope="system" if system_id else "engagement",
        actor_or_group_id=actor,
        is_group=True,
        effective_from=_START - _dt.timedelta(days=30),
        assigned_by="setup",
        assignment_reason="policy",
    )
    handle.operations_records().insert_assignment(assignment)
    return assignment.id


# -- the transfer -----------------------------------------------------------


def test_close_transfers_ownership_and_ends_only_the_system_scoped_assignment(
    handle: SqliteStoreHandle, system: support.ResolvedSystem
) -> None:
    """Narrowest wins, and the engagement's own assignment is left alone."""
    engagement_id = str(system.scope.engagement.id)  # type: ignore[union-attr]
    wider = _assign(handle, system_id=None, engagement_id=engagement_id, actor="delivery-team")
    narrower = _assign(handle, system_id=system.system_id, engagement_id=None, actor="fde-alice")
    record = _start(handle, system)

    detail = support.close_handover(
        handle, record, system=system, accepted_by="bob", actor="alice", now=_START
    )

    assert support.current_owner_of(handle, system_id=system.system_id, at=_START) == (
        "client-platform"
    )
    assert detail["closed_prior_assignment_id"] == narrower
    operations = handle.operations_records()
    ended = {
        row.id: row.effective_to
        for row in [operations.current_owner(system_id=system.system_id, at=_START)]
        if row is not None
    }
    assert ended, "the receiving owner does not resolve"
    # The engagement-scoped row is untouched: closing it would silently un-own
    # every other system under the same engagement.
    with sqlite3.connect(handle.backend.path) as connection:  # type: ignore[attr-defined]
        effective_to = connection.execute(
            "SELECT effective_to FROM ownership_assignment WHERE id = ?", (wider,)
        ).fetchone()[0]
    assert effective_to is None, "the engagement-wide assignment was ended"


def test_close_records_the_transfer_and_resolves_nothing(
    handle: SqliteStoreHandle, system: support.ResolvedSystem
) -> None:
    """The honesty rule: items transfer with owners; none is closed."""
    record = _start(handle, system)

    detail = support.close_handover(
        handle, record, system=system, accepted_by="bob", actor="alice", now=_START
    )

    assert detail["accepted_by"] == "bob"
    assert detail["bundle_digest"] == "abc"
    assert set(detail["transferred"]) == {
        "gaps_assigned",
        "gaps_kept_owner",
        "questions",
        "conflicts",
    }
    # Nothing in this build can mark a gap resolved.
    dispositions = handle.governance().gap_dispositions()
    assert all(row.status != "resolved" for row in dispositions.values())


def test_a_gap_that_already_names_an_owner_keeps_them(
    handle: SqliteStoreHandle, system: support.ResolvedSystem
) -> None:
    """A named owner is a decision; a transfer is not a reason to unmake it.

    The positive control sits beside it: an unowned gap in the same store is
    assigned to the receiving owner, so a `close_handover` that touched nothing
    at all would fail this test rather than pass it.
    """
    from adopt_coverage import recompute_coverage

    scope = handle.scope().resolve(f"{system.scope_path}/prod")
    identities = handle.identities()
    for key in ("KEPT_KEY", "MOVED_KEY"):
        identities.observe(scope=scope, kind="config_key", namespace=None, key=key)

    now = _START
    gaps = support.open_gaps(handle, system=system, now=now)
    assert len(gaps) == 2, "the fixture did not produce two derived gaps"
    kept = next(gap for gap in gaps if "KEPT_KEY" in gap.uri)
    identity_id = next(
        row.identity_id
        for row in recompute_coverage(
            handle.coverage_records(), system.system_id, system.environment_id
        ).identities
        if row.uri == kept.uri
    )
    handle.governance().dispose_gap(
        gap_key=kept.gap_key,
        identity_id=identity_id,
        status="acknowledged",
        owner_actor_id="alice",
        note="alice owns this one",
    )
    record = _start(handle, system)

    detail = support.close_handover(
        handle, record, system=system, accepted_by="bob", actor="alice", now=now
    )

    transferred = detail["transferred"]
    assert transferred["gaps_kept_owner"] == [kept.gap_key]
    assert len(transferred["gaps_assigned"]) == 1
    dispositions = handle.governance().gap_dispositions()
    assert dispositions[kept.gap_key].owner_actor_id == "alice", "a named owner was overwritten"
    assigned_key = transferred["gaps_assigned"][0]
    assert dispositions[assigned_key].owner_actor_id == "client-platform"
    assert dispositions[assigned_key].status == "acknowledged"


# -- the rollback -----------------------------------------------------------


def test_a_close_that_cannot_own_the_system_writes_nothing_at_all(
    handle: SqliteStoreHandle, system: support.ResolvedSystem, tmp_path: Path
) -> None:
    """The post-check refusal rolls every write back, asserted by row count.

    Driven through the composition with a blank receiving owner past the CLI's
    own guard -- the same state a handover whose start detail was damaged would
    reach. A refusal that left the assignment, the re-dispositioned gaps and the
    `handover_closed` row behind would look identical to the operator and would
    have transferred a system to nobody while recording that it had not.
    """
    record = _start(handle, system, receiving_owner="   ")
    before = _counts(tmp_path / "store.db")

    with pytest.raises(AdoptError) as raised:
        support.close_handover(
            handle, record, system=system, accepted_by="bob", actor="alice", now=_START
        )

    assert raised.value.code is ErrorCode.HANDOVER_UNOWNED
    assert _counts(tmp_path / "store.db") == before, "a write escaped the refusal"
    assert support.current_owner_of(handle, system_id=system.system_id, at=_START) is None


def test_the_post_check_runs_against_the_port_the_router_asks(
    handle: SqliteStoreHandle, system: support.ResolvedSystem, tmp_path: Path
) -> None:
    """A close whose assignment lands in a shape `current_owner` cannot read fails.

    The check is deliberately not `did we insert a row` -- it asks the same
    question the escalation router asks, of the same port. This drives the
    failure by closing the new assignment inside the same instant, which is the
    cheapest way to make a written row invisible to the reader.
    """
    record = _start(handle, system)
    before = _counts(tmp_path / "store.db")

    operations = handle.operations_records()
    original = operations.insert_assignment

    def _insert_then_end(row: OwnershipAssignment) -> None:
        original(row)
        operations.close_assignment(row.id, effective_to=_START)

    operations.insert_assignment = _insert_then_end  # type: ignore[method-assign]
    try:
        with pytest.raises(AdoptError) as raised:
            support.close_handover(
                handle, record, system=system, accepted_by="bob", actor="alice", now=_START
            )
    finally:
        operations.insert_assignment = original  # type: ignore[method-assign]

    assert raised.value.code is ErrorCode.HANDOVER_UNOWNED
    assert "rather than" in raised.value.message
    assert _counts(tmp_path / "store.db") == before, "a write escaped the refusal"
