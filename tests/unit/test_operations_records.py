"""Ownership resolution, and the three append-only operations tables.

*Fails when* `current_owner` picks the wrong assignment, or picks one at all
when nobody owns the system. *Matters because* v6.1 §6 Build 7 routes every
unknown question to the current owner and makes an unowned live system a
**refused activation** — a resolver that fell back to any default would make
that refusal unreachable, and a resolver that preferred the broader assignment
would route a system's questions to whoever owns the engagement even after
somebody deliberately assigned that system to a named team. *No other instrument
catches it because* both wrong answers are plausible rows: nothing downstream can
tell a mis-resolved owner from a correct one.

The overlap case is the one worth the fixture cost. A system-scoped assignment
and an engagement-scoped one covering the same system is the **normal** state
during a handover, not an edge case, and "narrowest wins" is only a rule if
something asserts it.
"""

import datetime as _dt
from collections.abc import Iterator
from pathlib import Path

import pytest

from adopt_model import Approval, AuditEvent, OwnershipAssignment, ValueEvent
from adopt_obs import ManualClock, new_id
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle

_START = _dt.datetime(2026, 8, 26, 12, 0, tzinfo=_dt.UTC)


@pytest.fixture
def handle(tmp_path: Path) -> Iterator[SqliteStoreHandle]:
    store = open_store(tmp_path / "store.db", migrate=True, clock=ManualClock(_START))
    yield store
    store.close()


@pytest.fixture
def scope_ids(handle: SqliteStoreHandle) -> dict[str, str]:
    facade = handle.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    return {"firm": firm.id, "engagement": engagement.id, "system": system.id}


def _assignment(
    *,
    system_id: str | None,
    engagement_id: str | None,
    actor: str,
    effective_from: _dt.datetime,
    effective_to: _dt.datetime | None = None,
) -> OwnershipAssignment:
    return OwnershipAssignment(
        id=new_id("own"),
        system_id=system_id,
        engagement_id=engagement_id,
        scope="system" if system_id is not None else "engagement",
        actor_or_group_id=actor,
        is_group=True,
        effective_from=effective_from,
        effective_to=effective_to,
        assigned_by="tester",
        assignment_reason="handover",
    )


@pytest.mark.unit
def test_an_unowned_system_has_no_owner(
    handle: SqliteStoreHandle, scope_ids: dict[str, str]
) -> None:
    """`None` is a real answer, and the activation refusal depends on it."""
    records = handle.operations_records()
    assert records.current_owner(system_id=scope_ids["system"], at=_START) is None


@pytest.mark.unit
def test_the_narrowest_active_assignment_wins(
    handle: SqliteStoreHandle, scope_ids: dict[str, str]
) -> None:
    """A system assignment beats an engagement one covering the same system.

    Both rows are active and the engagement row is *newer*, so a resolver that
    ordered by time alone would return it. That is the failure this asserts.
    """
    records = handle.operations_records()
    with records.transaction():
        records.insert_assignment(
            _assignment(
                system_id=scope_ids["system"],
                engagement_id=None,
                actor="platform-team",
                effective_from=_START,
            )
        )
        records.insert_assignment(
            _assignment(
                system_id=None,
                engagement_id=scope_ids["engagement"],
                actor="delivery-practice",
                effective_from=_START + _dt.timedelta(hours=1),
            )
        )

    owner = records.current_owner(system_id=scope_ids["system"], at=_START + _dt.timedelta(hours=2))
    assert owner is not None
    assert owner.actor_or_group_id == "platform-team"


@pytest.mark.unit
def test_an_engagement_assignment_covers_a_system_with_none_of_its_own(
    handle: SqliteStoreHandle, scope_ids: dict[str, str]
) -> None:
    """The group default: the engagement owns what no system-level row claims.

    Resolved through `system.engagement_id` by the query, not by the caller —
    a caller supplying the wrong engagement would otherwise produce a plausible
    owner for a system that has none.
    """
    records = handle.operations_records()
    with records.transaction():
        records.insert_assignment(
            _assignment(
                system_id=None,
                engagement_id=scope_ids["engagement"],
                actor="delivery-practice",
                effective_from=_START,
            )
        )

    owner = records.current_owner(system_id=scope_ids["system"], at=_START)
    assert owner is not None
    assert owner.actor_or_group_id == "delivery-practice"


@pytest.mark.unit
def test_an_expired_assignment_does_not_own_anything(
    handle: SqliteStoreHandle, scope_ids: dict[str, str]
) -> None:
    """`effective_to` is exclusive, and a closed assignment stops owning.

    Asserted at two instants from one row, so the test cannot pass by the
    query ignoring `effective_to` in one direction.
    """
    records = handle.operations_records()
    ended = _START + _dt.timedelta(hours=1)
    with records.transaction():
        records.insert_assignment(
            _assignment(
                system_id=scope_ids["system"],
                engagement_id=None,
                actor="platform-team",
                effective_from=_START,
                effective_to=ended,
            )
        )

    assert records.current_owner(system_id=scope_ids["system"], at=_START) is not None
    assert records.current_owner(system_id=scope_ids["system"], at=ended) is None


@pytest.mark.unit
def test_an_assignment_not_yet_effective_does_not_own_anything(
    handle: SqliteStoreHandle, scope_ids: dict[str, str]
) -> None:
    """A future handover must not route today's questions to tomorrow's owner."""
    records = handle.operations_records()
    with records.transaction():
        records.insert_assignment(
            _assignment(
                system_id=scope_ids["system"],
                engagement_id=None,
                actor="incoming-team",
                effective_from=_START + _dt.timedelta(days=7),
            )
        )

    assert records.current_owner(system_id=scope_ids["system"], at=_START) is None


@pytest.mark.unit
def test_closing_an_assignment_ends_it_without_deleting_it(
    handle: SqliteStoreHandle, scope_ids: dict[str, str]
) -> None:
    """Who owned what, when, is history — a transfer ends a row, never removes it."""
    records = handle.operations_records()
    row = _assignment(
        system_id=scope_ids["system"],
        engagement_id=None,
        actor="platform-team",
        effective_from=_START,
    )
    with records.transaction():
        records.insert_assignment(row)

    closed_at = _START + _dt.timedelta(hours=3)
    with records.transaction():
        records.close_assignment(row.id, effective_to=closed_at)

    assert records.current_owner(system_id=scope_ids["system"], at=closed_at) is None
    # The row is still there, and still says who owned the system before.
    assert records.current_owner(system_id=scope_ids["system"], at=_START) is not None


@pytest.mark.unit
def test_approvals_audit_and_value_rows_round_trip(
    handle: SqliteStoreHandle, scope_ids: dict[str, str]
) -> None:
    """The three append-only tables write and read back as what was written.

    One test for three because the risk they share is the one that matters: a
    column the realization renders differently from what the generated model
    expects fails on the way back in, and the model is the column authority.
    """
    records = handle.operations_records()
    approval = Approval(
        id=new_id("apr"),
        firm_id=scope_ids["firm"],
        engagement_id=scope_ids["engagement"],
        subject_type="knowledge_revision",
        subject_id=new_id("krev"),
        actor_id="alice",
        approved_at=_START,
        scope_note="confirmed in Slack",
    )
    audit = AuditEvent(
        id=new_id("aud"),
        firm_id=scope_ids["firm"],
        system_id=scope_ids["system"],
        event_type="activation_completed",
        actor_id="operator",
        subject_ref=scope_ids["system"],
        detail="imported 42 rows",
        occurred_at=_START,
    )
    value = ValueEvent(
        id=new_id("ve"),
        system_id=scope_ids["system"],
        occurred_at=_START,
        event_type="answer_served",
        minutes=4.5,
        actor_id="alice",
        source_ref="ask",
        confidence_label="measured",
    )
    with records.transaction():
        records.insert_approval(approval)
        records.insert_audit_event(audit)
        records.insert_value_event(value)

    served = records.list_value_events(system_id=scope_ids["system"])
    assert [row.id for row in served] == [value.id]
    assert served[0] == value
    assert (
        records.list_value_events(system_id=scope_ids["system"], event_type="capture_banked") == []
    )


@pytest.mark.unit
def test_audit_events_come_back_newest_first_and_only_the_types_asked_for(
    handle: SqliteStoreHandle, scope_ids: dict[str, str]
) -> None:
    """`list_audit_events` filters by type and orders newest first.

    *Fails when* the type filter admits a row it was not asked for, or the order
    is not newest-first. *Matters because* S7.3's continuity status reads "the
    last delivery" as `[0]` of this list and decides dueness from its
    `occurred_at` — so a reversed order reports a tenant's *first* copy as their
    most recent one, which makes a stale tenant look current and silently
    doubles the loss window the cadence exists to bound. *No other instrument
    catches it because* both wrong answers are well-formed rows of the right
    shape: nothing downstream can tell a mis-ordered list from a correct one.

    The empty-list case is here rather than in its own test because it is the
    same defect wearing a different hat: SQLite would make `IN ()` a syntax
    error and Postgres would match nothing, so a caller passing no types has to
    mean "no rows" in both realizations or the two disagree about a caller
    mistake.
    """
    records = handle.operations_records()
    later = _START + _dt.timedelta(hours=1)
    delivered_first = AuditEvent(
        id=new_id("aud"),
        firm_id=scope_ids["firm"],
        event_type="continuity_export_delivered",
        actor_id="plane.continuity_export",
        subject_ref="digest-one",
        detail="s3://bucket/northwind-acme-erp.tar",
        occurred_at=_START,
    )
    delivered_second = AuditEvent(
        id=new_id("aud"),
        firm_id=scope_ids["firm"],
        event_type="continuity_export_delivered",
        actor_id="plane.continuity_export",
        subject_ref="digest-two",
        detail="s3://bucket/northwind-acme-erp.tar",
        occurred_at=later,
    )
    unrelated = AuditEvent(
        id=new_id("aud"),
        firm_id=scope_ids["firm"],
        system_id=scope_ids["system"],
        event_type="export_served",
        actor_id="operator",
        subject_ref="digest-three",
        occurred_at=later,
    )
    with records.transaction():
        records.insert_audit_event(delivered_first)
        records.insert_audit_event(delivered_second)
        records.insert_audit_event(unrelated)

    found = records.list_audit_events(event_types=("continuity_export_delivered",))
    assert [row.id for row in found] == [delivered_second.id, delivered_first.id]
    assert found[0] == delivered_second

    both = records.list_audit_events(event_types=("continuity_export_delivered", "export_served"))
    assert {row.id for row in both} == {delivered_first.id, delivered_second.id, unrelated.id}

    assert records.list_audit_events(event_types=()) == []
    assert records.list_audit_events(event_types=("continuity_export_failed",)) == []
