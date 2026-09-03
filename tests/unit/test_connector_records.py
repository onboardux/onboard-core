"""The connector port: liveness is an observation, revocation is a decision.

*Fails when* `touch_connector` can move anything other than `last_seen_at`, or
when re-registration behaves as an upsert. *Matters because* the sense endpoint
calls `touch_connector` on **every** accepted payload from the relay it is
about to trust: if that path can also reach `status`, a revoked relay clears its
own revocation simply by continuing to post, and the operator's decision becomes
advisory. *No other instrument catches it because* a widened `UPDATE` writes a
perfectly valid row — the relay keeps reporting, the table keeps a plausible
`active` status, and nothing downstream can tell a revocation that was lifted
deliberately from one that was overwritten by the caller it was meant to stop.

The revoked-then-touched case is the whole point of the fixture. A revoked
connector that keeps posting is the **expected** shape of the failure this port
exists to prevent, not an edge case.
"""

import datetime as _dt
from collections.abc import Iterator
from pathlib import Path

import pytest

from adopt_model import Connector
from adopt_obs import ManualClock, new_id
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle

_START = _dt.datetime(2026, 8, 28, 9, 0, tzinfo=_dt.UTC)
_LATER = _dt.datetime(2026, 8, 28, 10, 30, tzinfo=_dt.UTC)


@pytest.fixture
def handle(tmp_path: Path) -> Iterator[SqliteStoreHandle]:
    store = open_store(tmp_path / "store.db", migrate=True, clock=ManualClock(_START))
    yield store
    store.close()


@pytest.fixture
def system_id(handle: SqliteStoreHandle) -> str:
    facade = handle.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    return system.id


def _connector(system_id: str, *, status: str = "active") -> Connector:
    return Connector(
        id=new_id("conn"),
        system_id=system_id,
        mode="outbound_relay",
        registered_at=_START,
        last_seen_at=_START,
        version="ci-sense/0.4.0",
        status=status,  # type: ignore[arg-type]
    )


@pytest.mark.unit
def test_an_unregistered_system_has_no_connector(handle: SqliteStoreHandle, system_id: str) -> None:
    """`None` is a real answer: it is what makes first contact registrable."""
    assert handle.connector_records().get_connector(system_id) is None


@pytest.mark.unit
def test_a_registered_connector_reads_back_for_its_system(
    handle: SqliteStoreHandle, system_id: str
) -> None:
    records = handle.connector_records()
    row = _connector(system_id)
    records.register_connector(row)

    found = records.get_connector(system_id)

    assert found is not None
    assert found.id == row.id
    assert found.mode == "outbound_relay"
    assert found.status == "active"


@pytest.mark.unit
def test_touching_a_revoked_connector_cannot_un_revoke_it(
    handle: SqliteStoreHandle, system_id: str
) -> None:
    """The defect sentence, asserted.

    A revoked relay that keeps posting must stay revoked. `touch_connector` is
    on the accepted-payload path, so if it could reach `status` the revocation
    would be lifted by the very traffic it exists to refuse.
    """
    records = handle.connector_records()
    row = _connector(system_id)
    records.register_connector(row)
    records.set_connector_status(row.id, "revoked")

    records.touch_connector(row.id, _LATER)

    found = records.get_connector(system_id)
    assert found is not None
    assert found.status == "revoked", "touch must not move status"
    assert found.last_seen_at == _LATER, "touch must still record the observation"


@pytest.mark.unit
def test_set_connector_status_moves_only_status(handle: SqliteStoreHandle, system_id: str) -> None:
    """The operator's path leaves the liveness record alone.

    The positive control for the test above: `status` genuinely is movable, so
    the assertion there is about the *path* and not about an immovable column.
    """
    records = handle.connector_records()
    row = _connector(system_id)
    records.register_connector(row)

    records.set_connector_status(row.id, "degraded")

    found = records.get_connector(system_id)
    assert found is not None
    assert found.status == "degraded"
    assert found.last_seen_at == _START, "status changes are not observations"
