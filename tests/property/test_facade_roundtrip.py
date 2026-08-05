"""Every facade write round-trips through its generated model.

*Fails when* a value written through a facade comes back different — a timestamp
that lost its timezone, a boolean stored as text, a `None` that became an empty
string. *Matters because* the generated models are the egress allowlist
(implementation spec §1.1: strict closed schemas replace whole categories of
tests), and an allowlist that silently rewrites what passes through it is not a
contract, it is a transformation nobody documented. *No other instrument catches
it because* the write path and the read path are both hand-written translations
between a model and a column, and each is individually plausible: only comparing
the ends detects a mismatch.

Written as a property rather than as examples because the failures live in value
*classes* — the empty string, the `None`, the non-ASCII name, the boolean — and
an example set only covers the classes whoever wrote it thought of.
"""

import datetime as _dt
import itertools
import string
from collections.abc import Iterator

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adopt_obs import ManualClock
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle

#: Uniqueness comes from a counter, never from generated data: hypothesis
#: deliberately repeats inputs while shrinking, and a repeated scope slug is a
#: `SCOPE_SLUG_REUSED` refusal rather than the failure under test.
_TAGS = itertools.count()

_SLUG_BODY = st.text(alphabet=string.ascii_lowercase + string.digits + "-", max_size=10)
_SLUG_EDGE = st.sampled_from(string.ascii_lowercase + string.digits)

#: A valid slug by construction: edge, body, edge.
_SLUGS = st.builds(lambda a, b, c: f"{a}{b}{c}", _SLUG_EDGE, _SLUG_BODY, _SLUG_EDGE)

#: Free text. Surrogates are excluded because they are not encodable at all, and
#: a store that cannot hold them is not the defect this test is looking for.
_NAMES = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")),
    min_size=1,
    max_size=40,
)
_OPTIONAL_TEXT = st.one_of(st.none(), _NAMES)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SqliteStoreHandle]:
    """One store for the whole run: creating schema version 3 costs the full
    37-table DDL, and every example works under its own firm, so no example can
    observe another's writes."""
    path = tmp_path_factory.mktemp("roundtrip") / "store.db"
    clock = ManualClock(_dt.datetime(2026, 8, 3, 12, 30, 15, 123000, tzinfo=_dt.UTC))
    handle = open_store(path, migrate=True, clock=clock)
    yield handle
    handle.close()


@pytest.mark.property
@settings(
    max_examples=75,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    engagement_slug=_SLUGS,
    system_slug=_SLUGS,
    environment_slug=_SLUGS,
    name=_NAMES,
    client_label=_OPTIONAL_TEXT,
    residency=_OPTIONAL_TEXT,
    billable=st.booleans(),
)
def test_every_scope_write_round_trips(
    store: SqliteStoreHandle,
    engagement_slug: str,
    system_slug: str,
    environment_slug: str,
    name: str,
    client_label: str | None,
    residency: str | None,
    billable: bool,
) -> None:
    scope = store.scope()
    tag = f"{next(_TAGS):x}"

    firm = scope.create_firm(slug=f"firm-{tag}", name=name)
    engagement = scope.create_engagement(
        firm_id=firm.id, slug=engagement_slug, name=name, client_label=client_label
    )
    system = scope.create_system(
        engagement_id=engagement.id, slug=system_slug, name=name, archetype="platform"
    )
    environment = scope.create_environment(
        system_id=system.id,
        slug=environment_slug,
        name=name,
        is_billable=billable,
        data_residency_region=residency,
    )

    records = store.scope()._records
    assert records.find_firm(firm.slug) == firm
    assert records.find_engagement(firm.id, engagement.slug) == engagement
    assert records.find_system(engagement.id, system.slug) == system
    assert records.find_environment(system.id, environment.slug) == environment


@pytest.mark.property
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(engagement_slug=_SLUGS, system_slug=_SLUGS, environment_slug=_SLUGS)
def test_resolve_returns_ids_and_slugs_for_every_level(
    store: SqliteStoreHandle, engagement_slug: str, system_slug: str, environment_slug: str
) -> None:
    """Implementation spec §4.5 behaviour 5. Returning one and not the other is
    what pushes a lookup into the caller's loop."""
    scope = store.scope()
    tag = f"{next(_TAGS):x}"

    firm = scope.create_firm(slug=f"firm-{tag}", name="Northwind LLP")
    engagement = scope.create_engagement(firm_id=firm.id, slug=engagement_slug, name="ACME")
    system = scope.create_system(engagement_id=engagement.id, slug=system_slug, name="Orders")
    environment = scope.create_environment(system_id=system.id, slug=environment_slug, name="prod")

    resolved = scope.resolve(f"{firm.slug}/{engagement.slug}/{system.slug}/{environment.slug}")

    assert resolved.depth == len(resolved.slugs())
    assert resolved.slugs() == (firm.slug, engagement.slug, system.slug, environment.slug)
    assert resolved.firm.id == firm.id
    assert resolved.engagement is not None and resolved.engagement.id == engagement.id
    assert resolved.system is not None and resolved.system.id == system.id
    assert resolved.environment is not None and resolved.environment.id == environment.id


@pytest.mark.property
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    engagement_slug=_SLUGS,
    system_slug=_SLUGS,
    environment_slug=_SLUGS,
    kind=st.sampled_from(("webhook", "ci", "audit_trail", "probe", "trace", "contract")),
    cadence=st.one_of(st.none(), st.integers(min_value=1, max_value=86_400)),
    owner=_OPTIONAL_TEXT,
    outcome=st.sampled_from(("success", "empty", "failure", "skipped")),
    detail=_OPTIONAL_TEXT,
)
def test_every_sensor_write_round_trips(
    store: SqliteStoreHandle,
    engagement_slug: str,
    system_slug: str,
    environment_slug: str,
    kind: str,
    cadence: int | None,
    owner: str | None,
    outcome: str,
    detail: str | None,
) -> None:
    """S4's new facade, in the same property as the rest.

    `expected_cadence_seconds` is generated as **`None` or an integer** because
    the nullable case is the one that silently disables the missed-heartbeat
    check, and a round-trip that turned `None` into `0` would turn "no cadence
    declared" into "a cadence of zero seconds" -- a sensor permanently past its
    deadline rather than one never checked against it.
    """
    scope_facade = store.scope()
    tag = f"{next(_TAGS):x}"
    firm = scope_facade.create_firm(slug=f"firm-{tag}", name="Northwind LLP")
    engagement = scope_facade.create_engagement(firm_id=firm.id, slug=engagement_slug, name="ACME")
    system = scope_facade.create_system(
        engagement_id=engagement.id, slug=system_slug, name="Orders"
    )
    scope_facade.create_environment(system_id=system.id, slug=environment_slug, name="prod")
    scope = scope_facade.resolve(f"{firm.slug}/{engagement.slug}/{system.slug}/{environment_slug}")

    sensors = store.sensors()
    registered = sensors.register(
        scope=scope,
        kind=kind,  # type: ignore[arg-type]
        expected_cadence_seconds=cadence,
        owner_actor_id=owner,
    )
    assert sensors.get(registered.id) == registered

    sensors.heartbeat(sensor_id=registered.id, outcome=outcome, detail=detail)  # type: ignore[arg-type]

    after = sensors.get(registered.id)
    assert after is not None
    # The heartbeat moved health and the observation timestamps, and **nothing
    # else**: the cadence a sensor is measured against is not the reporting
    # path's to rewrite.
    assert after.expected_cadence_seconds == cadence
    assert after.owner_actor_id == owner
    assert after.kind == registered.kind
    assert after.last_attempted_at is not None
    assert (after.health == "HEALTHY") is (outcome in {"success", "empty"})
    assert sensors.for_scope(system_id=system.id, environment_id=scope.environment.id) == (after,)  # type: ignore[union-attr]
