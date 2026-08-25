"""Gap dispositions: recorded, keyed deterministically, and never an existence claim.

The disposition table is the one thing Build 4 adds to the schema, and the one
way it could go wrong is by becoming a second answer to *"what is uncovered?"*.
`recompute_coverage()` owns that question; these rows only say what a human
decided. Every test here is about keeping that line intact.
"""

import datetime as _dt
from pathlib import Path

import pytest
from adopt_knowledge import gap_key_for

from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_store import open_store
from tests.golden.fixture import FIXTURE_START

pytestmark = pytest.mark.unit

_URI = "onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/POST %2Fv1%2Forders"


def _clock() -> ManualClock:
    return ManualClock(FIXTURE_START)


@pytest.fixture
def store(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A store holding one real identity and **no** dispositions.

    Real, because `coverage_gap.identity_id` is a foreign key and a disposition
    is meaningless without the identity it is about -- a test using an invented
    id would be asserting facade behaviour against a row the database refuses.

    Built from the facades rather than from the G0 fixture, which now seeds a
    `coverage_gap` row of its own: starting from a store that already holds a
    disposition would make every "one row" assertion below count two, and the
    tests would be about the fixture rather than about the facade.
    """
    handle = open_store(tmp_path / "store.db", migrate=True, clock=_clock())
    scopes = handle.scope()
    firm = scopes.create_firm(slug="northwind", name="Northwind LLP")
    engagement = scopes.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP rollout")
    system = scopes.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    scopes.create_environment(system_id=system.id, slug="prod", name="Production")
    scope = scopes.resolve("northwind/acme-erp/orders-api/prod")
    handle.identities().observe(scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders")
    yield handle
    handle.close()


@pytest.fixture
def governance(store):  # type: ignore[no-untyped-def]
    return store.governance()


@pytest.fixture
def identity_id(store) -> str:  # type: ignore[no-untyped-def]
    return str(store.backend.query("SELECT id FROM identity LIMIT 1")[0]["id"])


# -- the key ----------------------------------------------------------------


def test_the_gap_key_is_the_uri_its_environment_and_its_kind() -> None:
    """v6.1 §6 Build 4's definition, literally.

    *Fails when* the key stops being derivable from the URI alone. *Matters
    because* it is what a disposition survives regeneration by: a key that
    included a row id, a timestamp or a scan order would make every disposition
    orphan itself the next time `adopt map` ran.
    """
    assert gap_key_for(_URI) == f"{_URI}|prod|endpoint"


def test_the_same_identity_always_produces_the_same_key() -> None:
    assert gap_key_for(_URI) == gap_key_for(_URI)


def test_an_unparseable_uri_still_yields_a_key() -> None:
    """A malformed URI is still a gap, and refusing to key it would hide it."""
    assert gap_key_for("not-a-uri") == "not-a-uri|?|?"


# -- recording --------------------------------------------------------------


def test_a_disposition_is_recorded_and_read_back(governance, identity_id) -> None:  # type: ignore[no-untyped-def]
    row = governance.dispose_gap(
        gap_key="k1",
        identity_id=identity_id,
        status="acknowledged",
        owner_actor_id="alice",
        note="SME session booked",
    )

    assert row.status == "acknowledged"
    assert row.owner_actor_id == "alice"
    assert governance.gap_disposition("k1") == row
    assert governance.gap_dispositions() == {"k1": row}


def test_re_disposing_keeps_one_row_its_id_and_its_first_seen_date(governance, identity_id) -> None:  # type: ignore[no-untyped-def]
    """A revisited decision is the same decision, not a second one.

    *Fails when* the upsert mints a new id or resets `created_at`. *Matters
    because* two rows for one gap make "what did we decide about this" a
    question with two answers, and a moving id breaks any reference taken to it.
    """
    first = governance.dispose_gap(gap_key="k1", identity_id=identity_id, status="acknowledged")
    second = governance.dispose_gap(gap_key="k1", identity_id=identity_id, status="resolved")

    assert second.id == first.id
    assert second.created_at == first.created_at
    assert second.status == "resolved"
    assert len(governance.gap_dispositions()) == 1


def test_a_waiver_without_an_expiry_is_refused(governance, identity_id) -> None:  # type: ignore[no-untyped-def]
    """*Fails when* `waived_until` becomes optional on a waiver.

    *Matters because* a waiver is the one disposition that removes a gap from
    everyone's attention: without a date it silently outlives the reasoning
    behind it and nothing ever brings the gap back. *No other instrument catches
    it because* the DDL cannot express "mandatory for one status value", so the
    column is legitimately nullable and a missing date looks like every other
    unset field.
    """
    with pytest.raises(AdoptError) as raised:
        governance.dispose_gap(gap_key="k1", identity_id=identity_id, status="waived")

    assert raised.value.code is ErrorCode.GAP_WAIVER_NEEDS_UNTIL
    assert governance.gap_dispositions() == {}


def test_a_waiver_with_an_expiry_is_recorded(governance, identity_id) -> None:  # type: ignore[no-untyped-def]
    until = _dt.datetime(2026, 12, 31, tzinfo=_dt.UTC)

    row = governance.dispose_gap(
        gap_key="k1", identity_id=identity_id, status="waived", waived_until=until
    )

    assert row.status == "waived"
    assert row.waived_until == until


def test_dispositions_are_keyed_independently(governance, identity_id) -> None:  # type: ignore[no-untyped-def]
    governance.dispose_gap(gap_key="k1", identity_id=identity_id, status="acknowledged")
    governance.dispose_gap(gap_key="k2", identity_id=identity_id, status="resolved")

    assert sorted(governance.gap_dispositions()) == ["k1", "k2"]
