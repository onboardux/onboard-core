"""`recompute_coverage` agrees with an independent re-derivation of contracts Â§6.

*Fails when* the function's composition of the six inputs diverges from the
contract on some graph nobody thought to write down -- an identity with two
bindings where one qualifies, an item spanning environments, a boundary declared
for the wrong environment. *Matters because* `recompute_coverage` is the
authority every downstream gap report reads, and a composition bug is invisible:
the answer is a boolean, and a wrong boolean looks exactly like a right one. *No
other instrument catches it because* the unit table drives one input at a time
and cannot reach the combinations, and no CUJ walks a graph.

**The reference implementation is deliberately not refactored to share code with
the one under test.** It reads the same rows and re-derives the six conditions
from the contract text independently. A shared helper would make both agree on
the same mistake, which is the failure mode a differential property exists to
avoid -- so the duplication here is the instrument, not an oversight.
"""

import datetime as _dt
import itertools
from collections.abc import Iterator
from dataclasses import replace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adopt_coverage import recompute_coverage
from adopt_coverage.records import CoverageRecords
from adopt_obs import ManualClock, format_timestamp, new_id
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft, open_store
from adopt_store.api import SqliteStoreHandle

_START = _dt.datetime(2026, 8, 5, 9, 0, 0, tzinfo=_dt.UTC)

#: Enough shape to reach the interesting combinations without generating a store
#: so large the property stops finishing inside the unit budget.
# const-sync: ok -- a generation bound for this property, not a product value.
_MAX_IDENTITIES = 4
# const-sync: ok -- a generation bound for this property, not a product value.
_MAX_ITEMS = 3

#: Uniqueness comes from a counter, never from generated or time-derived data.
#: A truncated ULID looks unique and is not: ULIDs share a time prefix, so two
#: examples in the same millisecond produce the same slug and the store refuses
#: the second with `SCOPE_SLUG_REUSED` -- a failure that has nothing to do with
#: coverage and reproduces only under load.
_SYSTEMS = itertools.count()


def _next_system_slug() -> str:
    return f"sys-{next(_SYSTEMS):06x}"


def _reference_coverage(records: CoverageRecords, system_id: str) -> dict[str, bool]:
    """Contracts Â§6, re-derived from the text. Independent by construction.

    > Evaluates, per identity in scope: an active `identity_revision` - at least
    > one non-retired `binding` - an active `knowledge_revision` on the bound
    > item - applicable audience and environment - the `observability_boundary`
    > for the scope - verification requirements.
    """
    identities = records.identities_in_scope(system_id=system_id, environment_id=None)
    identity_status = records.head_identity_statuses(system_id=system_id, environment_id=None)
    binding_status = records.head_binding_statuses(system_id=system_id, environment_id=None)
    bindings = records.bindings_in_scope(system_id=system_id, environment_id=None)
    items = {row.id: row for row in records.items_in_scope(system_id=system_id)}
    verification = records.head_item_verifications(system_id=system_id)
    audiences = records.audience_counts(system_id=system_id)
    boundaries = records.boundaries_for_system(system_id=system_id)

    verdicts: dict[str, bool] = {}
    for identity in identities:
        active = identity_status.get(identity.id) == "active"
        governed = any(
            boundary.environment_id in (None, identity.environment_id) for boundary in boundaries
        )
        carried = False
        for binding in bindings:
            if binding.identity_id != identity.id:
                continue
            if binding_status.get(binding.id) in (None, "retired"):
                continue
            item = items.get(binding.item_id)
            if item is None or item.current_revision_id is None:
                continue
            if item.freshness_state == "retired":
                continue
            if audiences.get(item.id, 0) == 0:
                continue
            if item.environment_id not in (None, identity.environment_id):
                continue
            if binding.item_id in verification and verification[binding.item_id] == "conflicted":
                continue
            carried = True
            break
        verdicts[identity.id] = active and governed and carried
    return verdicts


@pytest.fixture(scope="module")
def clock() -> ManualClock:
    return ManualClock(_START)


@pytest.fixture(scope="module")
def store(
    tmp_path_factory: pytest.TempPathFactory, clock: ManualClock
) -> Iterator[SqliteStoreHandle]:
    """One store for the run: creating schema version 3 costs the full 37-table
    DDL, and every example works under its own system."""
    path = tmp_path_factory.mktemp("coverage") / "store.db"
    handle = open_store(path, migrate=True, clock=clock)
    yield handle
    handle.close()


@pytest.fixture(scope="module")
def firm_and_engagement(store: SqliteStoreHandle) -> tuple[str, str]:
    facade = store.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    return firm.id, engagement.id


_GRAPHS = st.fixed_dictionaries(
    {
        # (identity is retired, identity is moved)
        "identities": st.lists(
            st.tuples(st.booleans(), st.booleans()), min_size=1, max_size=_MAX_IDENTITIES
        ),
        # (item is retired, item has an audience, item is environment-scoped,
        #  item verification is conflicted)
        "items": st.lists(
            st.tuples(st.booleans(), st.booleans(), st.booleans(), st.booleans()),
            min_size=1,
            max_size=_MAX_ITEMS,
        ),
        # (identity index, item index, binding is retired)
        "bindings": st.lists(
            st.tuples(
                st.integers(min_value=0, max_value=_MAX_IDENTITIES - 1),
                st.integers(min_value=0, max_value=_MAX_ITEMS - 1),
                st.booleans(),
            ),
            max_size=_MAX_IDENTITIES * _MAX_ITEMS,
        ),
        # Which boundary rows exist: none, system-wide, or a foreign environment.
        "boundary": st.sampled_from(("none", "system_wide", "other_environment")),
    }
)


@pytest.mark.property
@settings(
    max_examples=120, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(graph=_GRAPHS)
def test_recompute_equals_an_independent_reference_implementation(
    store: SqliteStoreHandle,
    firm_and_engagement: tuple[str, str],
    clock: ManualClock,
    graph: dict[str, object],
) -> None:
    _, engagement_id = firm_and_engagement
    facade = store.scope()
    slug = _next_system_slug()
    system = facade.create_system(engagement_id=engagement_id, slug=slug, name="generated")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    facade.create_environment(system_id=system.id, slug="staging", name="Staging")
    scope = facade.resolve(f"northwind/acme-erp/{slug}/prod")
    other = facade.resolve(f"northwind/acme-erp/{slug}/staging")
    assert scope.environment is not None and other.environment is not None

    identities = []
    for index, (retired, moved) in enumerate(graph["identities"]):  # type: ignore[call-overload]
        identity = store.identities().observe(
            scope=scope, kind="endpoint", namespace=None, key=f"GET /v1/r{index}"
        )
        if retired:
            store.identities().retire(identity_id=identity.id, reason="generated")
        elif moved:
            store.identities().move(
                identity_id=identity.id,
                scope=scope,
                kind="endpoint",
                namespace=None,
                key=f"GET /v2/r{index}",
            )
        identities.append(identity.id)

    items = []
    for index, (retired, tagged, scoped, conflicted) in enumerate(graph["items"]):  # type: ignore[call-overload]
        # An item with no environment *spans* them (the column is nullable for
        # exactly that reason), which is the case the environment predicate has
        # to get right in both directions.
        item_scope = scope if scoped else replace(scope, environment=None)
        item_id, _ = store.items().create(
            scope=item_scope,
            kind="answer",
            title=f"item {index}",
            revision=KnowledgeRevisionDraft(
                authority_class="human_confirmed",
                body_md="v1",
                verification="conflicted" if conflicted else "verified",
            ),
        )
        if tagged:
            with store.backend.transaction():
                store.backend.execute(
                    "INSERT INTO audience_tag (item_id, audience) VALUES (?, ?)",
                    (item_id, "engineering"),
                )
        if retired:
            store.revisions().retire(parent_id=item_id, reason="generated")
        items.append(item_id)

    bound: set[tuple[str, str]] = set()
    for identity_index, item_index, retired in graph["bindings"]:  # type: ignore[call-overload]
        if identity_index >= len(identities) or item_index >= len(items):
            continue
        pair = (items[item_index], identities[identity_index])
        if pair in bound:
            continue
        bound.add(pair)
        binding_id, _ = store.bindings().create(
            item_id=pair[0],
            identity_id=pair[1],
            is_load_bearing=True,
            revision=BindingRevisionDraft(status="active", locator_rung=1),
        )
        if retired:
            store.bindings().retire(binding_id=binding_id, reason="generated")

    if graph["boundary"] != "none":
        environment_id = None if graph["boundary"] == "system_wide" else other.environment.id
        with store.backend.transaction():
            store.backend.execute(
                "INSERT INTO observability_boundary "
                "(id, system_id, environment_id, tier, knowledge_plane_location, "
                " control_plane_location, permitted_outbound_categories, declared_at, "
                " contractual) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("ob"),
                    system.id,
                    environment_id,
                    "T2",
                    "customer",
                    "customer",
                    '["metadata_only"]',
                    format_timestamp(clock.now()),
                    0,
                ),
            )

    records = store.coverage_records()
    actual = {
        entry.identity_id: entry.covered
        for entry in recompute_coverage(records, system.id, clock=clock).identities
    }

    assert actual == _reference_coverage(records, system.id)


@pytest.mark.property
@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(graph=_GRAPHS)
def test_a_covered_identity_always_has_no_reasons_and_the_reverse(
    store: SqliteStoreHandle,
    firm_and_engagement: tuple[str, str],
    clock: ManualClock,
    graph: dict[str, object],
) -> None:
    """The verdict and its explanation cannot disagree.

    A covered identity with a reason attached, or an uncovered one with none,
    would make the CLI's `disagreements[]` output unreadable -- and an operator
    who stops trusting the explanation stops reading the alarm.
    """
    _, engagement_id = firm_and_engagement
    facade = store.scope()
    slug = _next_system_slug()
    system = facade.create_system(engagement_id=engagement_id, slug=slug, name="generated")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    scope = facade.resolve(f"northwind/acme-erp/{slug}/prod")

    for index, _ in enumerate(graph["identities"]):  # type: ignore[call-overload]
        store.identities().observe(
            scope=scope, kind="endpoint", namespace=None, key=f"GET /v1/x{index}"
        )

    for entry in recompute_coverage(store.coverage_records(), system.id, clock=clock).identities:
        assert entry.covered == (entry.reasons == ())
