"""CUJ-4 -- a shared referent changes; only load-bearing bindings stale.

*Fails when* a change to one identity stales items whose binding to it was not
load-bearing. *Matters because* blanket propagation was the previous implicit
behaviour, and the arithmetic is the argument: one shared utility touching 200
items would stale all 200, of which 188 were never affected. Nobody triages 200
false stales twice, so the freshness column stops being read and the substrate
becomes decorative. *No other instrument catches it because* the propagation
property explores the shape at generated sizes and the level matrix drives one
binding at a time -- neither asserts the journey's own numbers, and this journey
is defined by them.

PRD §4 CUJ-4, three steps and one failure branch. The fixture is **the PRD's**:
200 items, 12 load-bearing.
"""

import pytest

from adopt_freshness import RULE_ITEM_STATE, RULE_SOURCE_IDENTITY_DEAD, resolve_freshness
from adopt_obs import ManualClock
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft
from adopt_store.api import SqliteStoreHandle

#: PRD §4 CUJ-4 step 1, verbatim: *"One identity bound by 200 items changes; 12
#: of those bindings have `is_load_bearing = 1`."* Written as the journey's own
#: numbers rather than as tunables, because changing them changes the journey.
# const-sync: ok -- CUJ-4's stated fixture size, not a product tunable.
_BOUND_ITEMS = 200
# const-sync: ok -- CUJ-4's stated load-bearing count, not a product tunable.
_LOAD_BEARING = 12


def _shared_referent_world(
    store: SqliteStoreHandle, scope: Scope
) -> tuple[str, list[str], set[str]]:
    """One identity, 200 items bound to it, 12 of them load-bearing.

    Returns `(identity_id, every_item_id, the_twelve)`.
    """
    shared = store.identities().observe(
        scope=scope, kind="symbol", namespace="billing", key=("charges", "refund")
    )
    items: list[str] = []
    load_bearing: set[str] = set()
    # One transaction: 200 items with a commit each measures fsync, not
    # propagation, and would put this journey over the suite's runtime ratchet.
    with store.backend.transaction():
        for index in range(_BOUND_ITEMS):
            is_load_bearing = index < _LOAD_BEARING
            item_id, _ = store.items().create(
                scope=scope,
                kind="answer",
                title=f"How refund path {index} behaves",
                revision=KnowledgeRevisionDraft(
                    authority_class="artifact_observed", body_md=f"v1 for {index}"
                ),
            )
            store.bindings().create(
                item_id=item_id,
                identity_id=shared.id,
                is_load_bearing=is_load_bearing,
                revision=BindingRevisionDraft(status="active", locator_rung=3),
            )
            items.append(item_id)
            if is_load_bearing:
                load_bearing.add(item_id)
    return shared.id, items, load_bearing


@pytest.mark.e2e
def test_cuj4_exactly_the_load_bearing_subset_stales(
    s4_store: SqliteStoreHandle, s4_scope: Scope, s4_clock: ManualClock
) -> None:
    # Step 1 -- one identity bound by 200 items; 12 bindings are load-bearing.
    identity_id, items, load_bearing = _shared_referent_world(s4_store, s4_scope)
    assert len(items) == _BOUND_ITEMS
    assert len(load_bearing) == _LOAD_BEARING

    # The shared referent changes.
    s4_store.identities().retire(identity_id=identity_id, reason="symbol deleted")

    # Step 2 -- resolve all 200.
    records = s4_store.freshness_records()
    resolutions = {item: resolve_freshness(records, item, clock=s4_clock) for item in items}

    # Step 3 -- exactly 12 resolve `stale`; the other 188 are unchanged.
    stale = {item for item, resolution in resolutions.items() if resolution.state == "stale"}
    assert stale == load_bearing
    assert len(stale) == _LOAD_BEARING

    for item in load_bearing:
        assert resolutions[item].level == "source"
        assert resolutions[item].deciding_rule == RULE_SOURCE_IDENTITY_DEAD
    for item in set(items) - load_bearing:
        # "Unchanged" is stronger than "not stale": these items still report
        # their own state at their own level, as though nothing had happened --
        # which, for them, nothing did.
        assert resolutions[item].state == "unverified"
        assert resolutions[item].deciding_rule == RULE_ITEM_STATE


@pytest.mark.e2e
def test_cuj4_failure_branch_an_unset_flag_stales_rather_than_reassures(
    s4_store: SqliteStoreHandle, s4_scope: Scope, s4_clock: ManualClock
) -> None:
    """*`is_load_bearing` was never set by the writer.*

    The column defaults to `1`, so the item stales. **The schema defaults toward
    false staleness, never toward false confidence** -- a false stale costs a
    triage, and a false fresh costs a confident wrong answer.
    """
    shared = s4_store.identities().observe(
        scope=s4_scope, kind="symbol", namespace="billing", key=("charges", "refund")
    )
    item_id, _ = s4_store.items().create(
        scope=s4_scope,
        kind="answer",
        title="How a refund is issued",
        revision=KnowledgeRevisionDraft(authority_class="artifact_observed", body_md="v1"),
    )
    binding_id, _ = s4_store.bindings().create(
        item_id=item_id,
        identity_id=shared.id,
        is_load_bearing=True,
        revision=BindingRevisionDraft(status="active", locator_rung=3),
    )
    # Erase the writer's choice, leaving the column at whatever the schema says.
    with s4_store.backend.transaction():
        s4_store.backend.execute(
            "UPDATE binding SET is_load_bearing = (SELECT 1) WHERE id = ?", (binding_id,)
        )
    s4_store.identities().retire(identity_id=shared.id, reason="symbol deleted")

    resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

    assert resolution.state == "stale"
    assert resolution.deciding_rule == RULE_SOURCE_IDENTITY_DEAD
