"""A lifecycle transition is never silent, for any sequence of transitions.

*Fails when* a system's `lifecycle_state` moves without a `system_lifecycle_event`
recording the move, or when a merge or split records only one side. *Matters
because* the event log is the only account of how a system reached the state it
is in, and PRD F3.1 makes it normative that no transition is silent — a state
that changed with no event is indistinguishable from a state that was always
that way. *No other instrument catches it because* the state column and the event
table are written by the same call, so any test that reads only one of them
passes whether or not the other was written.

The sequence is generated rather than enumerated because the guarantee is over
*all* sequences: the failure mode this protects against is a code path added
later that changes state directly, and an example-based test only covers the
paths that existed when it was written.
"""

import datetime as _dt
import itertools
from collections.abc import Iterator

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adopt_model._enums import LifecycleState
from adopt_obs import ManualClock
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle

_STATES: tuple[LifecycleState, ...] = (
    "DISCOVERED",
    "SETUP",
    "PILOT",
    "LIVE",
    "PAUSED",
    "DEGRADED",
    "ARCHIVED",
    "DISCONNECTED",
)

#: Scope slugs must be unique, and hypothesis deliberately repeats inputs while
#: shrinking -- so uniqueness comes from a counter, never from generated data.
_TAGS = itertools.count()

#: One transition: the state to move to, and whether it names a counterpart.
_TRANSITIONS = st.lists(
    st.tuples(st.sampled_from(_STATES), st.booleans()),
    min_size=1,
    max_size=6,
)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SqliteStoreHandle]:
    """One store for the whole property run.

    Built once because creating schema version 3 costs the full 37-table DDL,
    and paying that per example would buy nothing: every example works on its
    own freshly created system, so no example can observe another's writes.
    """
    path = tmp_path_factory.mktemp("lifecycle") / "store.db"
    clock = ManualClock(_dt.datetime(2026, 8, 3, tzinfo=_dt.UTC))
    handle = open_store(path, migrate=True, clock=clock)
    yield handle
    handle.close()


def _fresh_system_pair(handle: SqliteStoreHandle) -> tuple[str, str]:
    """A system and a counterpart under their own engagement."""
    tag = f"{next(_TAGS):x}"
    scope = handle.scope()
    firm = scope.create_firm(slug=f"firm-{tag}", name="Northwind LLP")
    engagement = scope.create_engagement(firm_id=firm.id, slug=f"eng-{tag}", name="ACME")
    primary = scope.create_system(engagement_id=engagement.id, slug=f"sys-{tag}", name="Primary")
    other = scope.create_system(engagement_id=engagement.id, slug=f"alt-{tag}", name="Counterpart")
    return primary.id, other.id


@pytest.mark.property
@settings(
    max_examples=60,
    # File I/O in a temp directory is not a fixed-cost operation on every
    # platform, and a wall-clock deadline on a test whose subject is not
    # latency buys flakes rather than information.
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(sequence=_TRANSITIONS)
def test_every_state_change_is_recorded_by_exactly_one_event(
    store: SqliteStoreHandle, sequence: list[tuple[LifecycleState, bool]]
) -> None:
    primary_id, other_id = _fresh_system_pair(store)
    scope = store.scope()

    expected_changes = 0
    state: LifecycleState = "DISCOVERED"
    for to_state, paired in sequence:
        scope.transition(
            primary_id,
            to_state,
            reason="generated sequence",
            related_system_id=other_id if paired else None,
        )
        if to_state != state:
            expected_changes += 1
            state = to_state

    rows = store.backend.query(
        "SELECT from_state, to_state, related_system_id FROM system_lifecycle_event "
        "WHERE system_id = ? ORDER BY occurred_at, id;",
        (primary_id,),
    )
    recorded_changes = [row for row in rows if row["from_state"] != row["to_state"]]

    assert len(recorded_changes) == expected_changes
    assert len(rows) == len(sequence), "every transition call leaves exactly one event"

    stored = store.backend.query("SELECT lifecycle_state FROM system WHERE id = ?;", (primary_id,))
    assert stored[0]["lifecycle_state"] == state


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(to_state=st.sampled_from(_STATES))
def test_a_merge_or_split_records_both_sides(
    store: SqliteStoreHandle, to_state: LifecycleState
) -> None:
    """Implementation spec §4.5 behaviour 4. A one-sided merge leaves the absorbed
    system's history showing nothing at all, which is the version of this rule
    that silently stops being true."""
    primary_id, other_id = _fresh_system_pair(store)

    events = store.scope().transition(
        primary_id, to_state, reason="merged", related_system_id=other_id
    )

    by_system = {event.system_id: event for event in events}
    assert set(by_system) == {primary_id, other_id}
    assert by_system[primary_id].related_system_id == other_id
    assert by_system[other_id].related_system_id == primary_id
