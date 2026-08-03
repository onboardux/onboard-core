"""Identity observe / re-observe / move / dead -- CUJ-2 and contracts §4 rule 9.

*Fails when* observing a referent twice creates a second identity or a second
revision, when a move rewrites the old URI instead of aliasing it, or when a
moved identity loses the pointer to where it went. *Matters because* this is
Bet 1 in executable form -- knowledge binds to identity, never to rendering -- and
CUJ-2 is the precondition for the G2 gate: a cosmetic change must leave the
binding intact, and a real move must stay traceable from the old address forever.
*No other instrument catches it because* the URI round-trip properties prove the
*name* is stable while saying nothing about whether the store reuses the row, and
the chain property proves the chain is single-headed while saying nothing about
whether a `moved` revision names its destination.
"""

import datetime as _dt
from collections.abc import Iterator

import pytest

from adopt_identity import build_uri
from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_scope import Scope
from adopt_store import doctor, open_store
from adopt_store.api import SqliteStoreHandle

_START = _dt.datetime(2026, 8, 3, 9, 0, 0, tzinfo=_dt.UTC)
#: How much later the second sighting is. Stated rather than waited for --
#: sleeps in tests are banned (implementation spec §5).
_ELAPSED = _dt.timedelta(days=1, hours=2, minutes=30)
_LATER = _START + _ELAPSED


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(_START)


@pytest.fixture
def store(
    tmp_path_factory: pytest.TempPathFactory, clock: ManualClock
) -> Iterator[SqliteStoreHandle]:
    path = tmp_path_factory.mktemp("identity") / "store.db"
    handle = open_store(path, migrate=True, clock=clock)
    yield handle
    handle.close()


@pytest.fixture
def scope(store: SqliteStoreHandle) -> Scope:
    facade = store.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    return facade.resolve("northwind/acme-erp/orders-api/prod")


def _revision_count(store: SqliteStoreHandle, identity_id: str) -> int:
    return len(store.revision_records().revision_ids("identity_revision", identity_id))


@pytest.mark.unit
def test_a_first_sighting_creates_the_identity_and_one_active_revision(
    store: SqliteStoreHandle, scope: Scope
) -> None:
    identity = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )

    assert identity.uri == build_uri(scope, "endpoint", None, "POST /v1/orders")
    assert identity.first_seen == _START
    assert identity.last_seen == _START
    assert _revision_count(store, identity.id) == 1
    assert doctor(store) == []


@pytest.mark.unit
def test_re_observing_advances_last_seen_and_writes_no_revision(
    store: SqliteStoreHandle, scope: Scope, clock: ManualClock
) -> None:
    """CUJ-2 steps 2-4: a cosmetic change yields the identical URI.

    A revision per sighting would turn the chain into a log of extractor runs, and
    the chain-integrity property would then be measuring scan cadence rather than
    change.
    """
    first = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )
    clock.advance(_ELAPSED)

    again = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )

    assert again.id == first.id, "matched by uri, never re-created"
    assert again.uri == first.uri
    assert again.first_seen == _START, "first_seen never moves"
    assert again.last_seen == _LATER
    assert _revision_count(store, first.id) == 1, "nothing about the identity changed"


@pytest.mark.unit
def test_the_stored_row_agrees_with_the_uri_it_carries(
    store: SqliteStoreHandle, scope: Scope
) -> None:
    """`namespace` and `local_key` are derived from the parsed URI, so the row and
    its URI cannot disagree even by one normalization."""
    identity = store.identities().observe(
        scope=scope, kind="db_field", namespace="public", key=("orders", "total_cents")
    )

    assert identity.namespace == "public"
    assert identity.local_key == "orders/total_cents"
    assert identity.uri.endswith("/db_field/public/orders/total_cents")


@pytest.mark.unit
def test_a_move_creates_a_new_identity_and_aliases_the_old_one(
    store: SqliteStoreHandle, scope: Scope, clock: ManualClock
) -> None:
    """CUJ-2's failure branch: a genuine move.

    The old URI is never rewritten (contracts §4 rule 9) -- a bundle exported last
    year still resolves it, and the alias chain says where the referent went.
    """
    source = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )
    clock.advance(_ELAPSED)

    destination = store.identities().move(
        identity_id=source.id, scope=scope, kind="endpoint", namespace=None, key="POST /v2/orders"
    )

    reread = store.identities().get(source.id)
    assert reread is not None
    assert reread.uri == source.uri, "the old URI is never rewritten"
    assert destination.id != source.id
    assert destination.uri.endswith("POST%20%2Fv2%2Forders")

    rows = store.backend.query(
        "SELECT status, alias_of_identity_id FROM identity_revision "
        "WHERE identity_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (source.id,),
    )
    assert rows[0]["status"] == "moved"
    assert rows[0]["alias_of_identity_id"] == destination.id
    assert doctor(store) == []


@pytest.mark.unit
def test_moving_a_referent_to_where_it_already_is_is_refused(
    store: SqliteStoreHandle, scope: Scope
) -> None:
    """A self-alias would make the identity its own destination, and the alias
    chain would then have a cycle nothing could follow."""
    source = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )

    with pytest.raises(AdoptError) as raised:
        store.identities().move(
            identity_id=source.id,
            scope=scope,
            kind="endpoint",
            namespace=None,
            key="POST /v1/orders",
        )

    assert raised.value.code is ErrorCode.URI_MALFORMED


@pytest.mark.unit
def test_moving_an_identity_that_does_not_exist_is_refused(
    store: SqliteStoreHandle, scope: Scope
) -> None:
    with pytest.raises(AdoptError) as raised:
        store.identities().move(
            identity_id="idn_01JZZZZZZZZZZZZZZZZZZZZZZZ",
            scope=scope,
            kind="endpoint",
            namespace=None,
            key="POST /v1/orders",
        )

    assert raised.value.code is ErrorCode.REVISION_HEAD_DANGLING


@pytest.mark.unit
def test_a_dead_identity_keeps_its_row_its_uri_and_its_history(
    store: SqliteStoreHandle, scope: Scope
) -> None:
    """`dead` is a status, not a deletion (PRD F6.7)."""
    identity = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )

    store.identities().retire(identity_id=identity.id, reason="endpoint removed in v3")

    reread = store.identities().get(identity.id)
    assert reread is not None
    assert reread.uri == identity.uri
    assert _revision_count(store, identity.id) == 2
    rows = store.backend.query(
        "SELECT status FROM identity_revision WHERE identity_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (identity.id,),
    )
    assert rows[0]["status"] == "dead"


@pytest.mark.unit
def test_two_environments_of_one_referent_are_two_identities(
    store: SqliteStoreHandle, scope: Scope
) -> None:
    """Why `environment` is mandatory in the URI, asserted through the store.

    Without it, a production and a staging observation of one field would upsert
    onto the same row -- which is the mixing the mandatory segment exists to stop.
    """
    facade = store.scope()
    assert scope.system is not None
    facade.create_environment(system_id=scope.system.id, slug="staging", name="Staging")
    staging = facade.resolve("northwind/acme-erp/orders-api/staging")

    in_prod = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )
    in_staging = store.identities().observe(
        scope=staging, kind="endpoint", namespace=None, key="POST /v1/orders"
    )

    assert in_prod.id != in_staging.id
    assert in_prod.uri != in_staging.uri
