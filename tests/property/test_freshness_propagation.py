"""For N bindings of which K are load-bearing, **exactly** the K-affected stale.

*Fails when* propagation is blanket (every item stales), when it is inverted
(none do), or when some ordering of bindings makes the count right and the
membership wrong. *Matters because* this is the rule CUJ-4 is built on and the
one whose failure is quietest: mass false staleness does not break anything, it
just trains people to stop believing the freshness column, and after that the
whole substrate is decorative. *No other instrument catches it because* the level
matrix drives one binding at a time and cannot see a rule that is right for one
and wrong for a hundred.

**Both directions are asserted.** "At least the K stale" passes a rule that
stales everything; "at most the K" passes one that stales nothing. Only set
equality distinguishes the rule from its two degenerate neighbours.
"""

import datetime as _dt
import itertools
from collections.abc import Iterator

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adopt_freshness import RULE_ITEM_STATE, resolve_freshness
from adopt_obs import ManualClock
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft, open_store
from adopt_store.api import SqliteStoreHandle

_START = _dt.datetime(2026, 8, 5, 9, 0, 0, tzinfo=_dt.UTC)

#: How many items may bind the one shared referent. CUJ-4 uses 200; the property
#: explores the shape and the E2E asserts the journey's own number.
# const-sync: ok -- a generation bound for this property, not a product value.
_MAX_BINDINGS = 24

#: Uniqueness comes from a counter, never from generated or time-derived data.
#: A truncated ULID looks unique and is not: ULIDs share a time prefix, so two
#: examples in the same millisecond produce the same slug and the store refuses
#: the second with `SCOPE_SLUG_REUSED` -- a failure that has nothing to do with
#: propagation and reproduces only under load.
_SYSTEMS = itertools.count()


def _next_system_slug() -> str:
    return f"sys-{next(_SYSTEMS):06x}"


@pytest.fixture(scope="module")
def clock() -> ManualClock:
    return ManualClock(_START)


@pytest.fixture(scope="module")
def store(
    tmp_path_factory: pytest.TempPathFactory, clock: ManualClock
) -> Iterator[SqliteStoreHandle]:
    path = tmp_path_factory.mktemp("freshness") / "store.db"
    handle = open_store(path, migrate=True, clock=clock)
    yield handle
    handle.close()


@pytest.fixture(scope="module")
def engagement_id(store: SqliteStoreHandle) -> str:
    facade = store.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    return facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP").id


@pytest.mark.property
@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(load_bearing=st.lists(st.booleans(), min_size=1, max_size=_MAX_BINDINGS))
def test_exactly_the_load_bearing_items_stale_when_the_shared_referent_dies(
    store: SqliteStoreHandle,
    engagement_id: str,
    clock: ManualClock,
    load_bearing: list[bool],
) -> None:
    facade = store.scope()
    slug = _next_system_slug()
    system = facade.create_system(engagement_id=engagement_id, slug=slug, name="generated")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    scope: Scope = facade.resolve(f"northwind/acme-erp/{slug}/prod")

    # One shared referent, N items bound to it, K of them load-bearing.
    shared = store.identities().observe(
        scope=scope, kind="symbol", namespace="billing", key=("charges", "refund")
    )
    expected_stale: set[str] = set()
    items: list[str] = []
    for index, is_load_bearing in enumerate(load_bearing):
        item_id, _ = store.items().create(
            scope=scope,
            kind="answer",
            title=f"item {index}",
            revision=KnowledgeRevisionDraft(authority_class="human_confirmed", body_md="v1"),
        )
        store.bindings().create(
            item_id=item_id,
            identity_id=shared.id,
            is_load_bearing=is_load_bearing,
            revision=BindingRevisionDraft(status="active", locator_rung=3),
        )
        items.append(item_id)
        if is_load_bearing:
            expected_stale.add(item_id)

    # The shared referent changes. Nothing else in the store moves.
    store.identities().retire(identity_id=shared.id, reason="symbol deleted")

    records = store.freshness_records()
    resolutions = {item_id: resolve_freshness(records, item_id, clock=clock) for item_id in items}
    actually_stale = {
        item_id for item_id, resolution in resolutions.items() if resolution.state == "stale"
    }

    assert actually_stale == expected_stale
    for item_id in set(items) - expected_stale:
        # The unaffected items are not merely "not stale" -- they are untouched,
        # still reporting their own state at their own level.
        assert resolutions[item_id].deciding_rule == RULE_ITEM_STATE
        assert resolutions[item_id].state == "unverified"


@pytest.mark.property
@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(count=st.integers(min_value=1, max_value=_MAX_BINDINGS))
def test_an_unset_load_bearing_flag_errs_toward_staleness(
    store: SqliteStoreHandle, engagement_id: str, clock: ManualClock, count: int
) -> None:
    """CUJ-4's failure branch: *`is_load_bearing` was never set by the writer.*

    The column defaults to `1`, so the item stales. The schema defaults toward
    false staleness and never toward false confidence, and this asserts the
    default is what the *resolver* sees rather than what the DDL says.
    """
    facade = store.scope()
    slug = _next_system_slug()
    system = facade.create_system(engagement_id=engagement_id, slug=slug, name="generated")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    scope: Scope = facade.resolve(f"northwind/acme-erp/{slug}/prod")

    shared = store.identities().observe(
        scope=scope, kind="symbol", namespace="billing", key=("charges", "refund")
    )
    items: list[str] = []
    for index in range(count):
        item_id, _ = store.items().create(
            scope=scope,
            kind="answer",
            title=f"item {index}",
            revision=KnowledgeRevisionDraft(authority_class="human_confirmed", body_md="v1"),
        )
        binding_id, _ = store.bindings().create(
            item_id=item_id,
            identity_id=shared.id,
            is_load_bearing=True,
            revision=BindingRevisionDraft(status="active", locator_rung=3),
        )
        # Erase the writer's choice, leaving the column at its schema default.
        with store.backend.transaction():
            store.backend.execute(
                "UPDATE binding SET is_load_bearing = (SELECT 1) WHERE id = ?", (binding_id,)
            )
        items.append(item_id)

    store.identities().retire(identity_id=shared.id, reason="symbol deleted")

    records = store.freshness_records()
    assert all(
        resolve_freshness(records, item_id, clock=clock).state == "stale" for item_id in items
    )
