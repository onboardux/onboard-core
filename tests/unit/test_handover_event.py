"""The handover record: grouping, ordering, step order, and a detail that rots.

The record is the whole of Build 9's "recorded state, resumable, auditable", and
it is derived rather than stored -- so every way it could be wrong is a way the
product would confidently describe a handover that did not happen that way.

Four defects are covered here, and each names why nothing else catches it:

* **Grouping and closure.** *Fails when* two handovers of one system fold into
  one record, or `current` resumes a closed event. *Matters because* a system
  handed over twice -- an engagement that came back, a second receiving team a
  year later -- would otherwise have its second event append steps to an
  acceptance both parties already signed. *No other instrument catches it
  because* the rows are individually valid either way; only the fold decides
  which event they belong to.
* **Step order.** *Fails when* a step stops requiring its predecessor, or a
  refusal stops naming the verb to run. *Matters because* the order **is** the
  checklist: `close` reachable from `start` would transfer ownership of a system
  nobody verified and nobody snapshotted. *No other instrument catches it
  because* every step writes a well-formed row whatever order it ran in.
* **Re-runs.** *Fails when* a second elicitation pass or verification round
  replaces the first instead of superseding it for reporting. *Matters because*
  a record showing only the last round could turn "they failed four tasks, then
  passed once we wrote the runbook" into "they passed".
* **A detail that does not decode.** *Fails when* the fold raises on one. *Matters
  because* `status` is exactly what an operator reaches for when a store looks
  wrong, and a describer that refuses to describe is useless at the only moment
  it is needed.
"""

import datetime as _dt
from typing import Any

import pytest
from adopt_handover import (
    HANDOVER_CLOSED,
    HANDOVER_ELICITED,
    HANDOVER_EVENT_TYPES,
    HANDOVER_PACK_EMITTED,
    HANDOVER_SNAPSHOT_TAKEN,
    HANDOVER_STARTED,
    HANDOVER_VERIFIED,
    PREREQUISITE,
    STEP_ORDER,
    VERB_FOR,
    current,
    decode_detail,
    encode_detail,
    fold,
    require,
)

from adopt_model import AuditEvent
from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit

_START = _dt.datetime(2026, 9, 2, 12, 0, tzinfo=_dt.UTC)
_SYSTEM = "sys_01J000000000000000000000SY"
_FIRM = "firm_01J0000000000000000000FIRM"
_OTHER_SYSTEM = "sys_01J000000000000000000OTHER"


def _row(
    *,
    event_id: str,
    event_type: str,
    handover_id: str | None = None,
    minutes: int = 0,
    detail: dict[str, Any] | str | None = None,
    actor_id: str | None = "alice",
    system_id: str = _SYSTEM,
) -> AuditEvent:
    """One audit row. `handover_id` defaults to the row's own id (the start row)."""
    if isinstance(detail, dict):
        encoded: str | None = encode_detail(detail)
    else:
        encoded = detail
    return AuditEvent(
        id=event_id,
        firm_id=_FIRM,
        system_id=system_id,
        event_type=event_type,
        actor_id=actor_id,
        subject_ref=handover_id or event_id,
        detail=encoded,
        occurred_at=_START + _dt.timedelta(minutes=minutes),
    )


def _opening(scope: str = "northwind/acme-erp/orders-api/prod") -> dict[str, Any]:
    return {
        "scope": scope,
        "environment_id": "env_01J00000000000000000000ENV",
        "receiving_owner": "client-platform",
        "is_group": True,
        "opening": {"identities": 12, "covered": 4, "open_gaps": 8},
    }


def _started(event_id: str, *, minutes: int = 0, scope: str | None = None) -> AuditEvent:
    detail = _opening() if scope is None else _opening(scope)
    return _row(event_id=event_id, event_type=HANDOVER_STARTED, minutes=minutes, detail=detail)


# -- grouping, ordering, closure -------------------------------------------


def test_two_handovers_of_one_system_fold_into_two_records() -> None:
    """A second handover a year later is its own event, not a resumption."""
    first_id = "aud_01J00000000000000000FIRST"
    second_id = "aud_01J0000000000000000SECOND"
    events = [
        _started(first_id),
        _row(event_id="aud_a", event_type=HANDOVER_ELICITED, handover_id=first_id, minutes=1),
        _row(event_id="aud_b", event_type=HANDOVER_CLOSED, handover_id=first_id, minutes=2),
        _started(second_id, minutes=10, scope="northwind/acme-erp/orders-api/dr"),
        _row(event_id="aud_c", event_type=HANDOVER_ELICITED, handover_id=second_id, minutes=11),
    ]

    records = fold(events, system_id=_SYSTEM)

    assert len(records) == 2, "the two events were not separated by subject_ref"
    # Newest first.
    assert records[0].id == second_id
    assert records[1].id == first_id
    assert records[1].is_closed
    assert not records[0].is_closed
    # `current` resumes the open one, never the accepted one.
    open_record = current(records)
    assert open_record is not None
    assert open_record.id == second_id
    assert open_record.scope.endswith("/dr")


def test_a_fully_closed_system_has_no_current_handover() -> None:
    """The negative control for the test above: nothing open means `None`.

    Without this, a `current` that returned the newest record whatever its state
    would pass the previous test on the strength of the ordering alone.
    """
    handover_id = "aud_01J00000000000000000FIRST"
    records = fold(
        [
            _started(handover_id),
            _row(event_id="aud_b", event_type=HANDOVER_CLOSED, handover_id=handover_id, minutes=2),
        ],
        system_id=_SYSTEM,
    )

    assert len(records) == 1
    assert current(records) is None


def test_another_systems_rows_and_foreign_event_types_are_dropped() -> None:
    """The fold is scoped to one system and to the six steps."""
    handover_id = "aud_01J00000000000000000FIRST"
    events = [
        _started(handover_id),
        _row(
            event_id="aud_other",
            event_type=HANDOVER_ELICITED,
            handover_id="aud_elsewhere",
            minutes=1,
            system_id=_OTHER_SYSTEM,
        ),
        _row(
            event_id="aud_noise",
            event_type="activation_completed",
            handover_id=handover_id,
            minutes=1,
        ),
    ]

    records = fold(events, system_id=_SYSTEM)

    assert len(records) == 1
    assert [row.event_type for row in records[0].events] == [HANDOVER_STARTED]


def test_a_group_with_no_start_row_is_skipped() -> None:
    """There is no frozen scope without the start row, so there is no record.

    `audit_event` has no delete path anywhere in the product, so this is not a
    reachable state -- but a record invented with a blank scope would let every
    later step operate on a scope nobody froze.
    """
    records = fold(
        [_row(event_id="aud_orphan", event_type=HANDOVER_ELICITED, handover_id="aud_missing")],
        system_id=_SYSTEM,
    )

    assert records == ()


def test_events_within_a_handover_are_ordered_by_time_then_id() -> None:
    """Two steps in one millisecond still order deterministically."""
    handover_id = "aud_01J00000000000000000FIRST"
    same_moment = 5
    events = [
        _row(
            event_id="aud_zz",
            event_type=HANDOVER_PACK_EMITTED,
            handover_id=handover_id,
            minutes=same_moment,
            detail={"audience": "technical"},
        ),
        _row(
            event_id="aud_aa",
            event_type=HANDOVER_ELICITED,
            handover_id=handover_id,
            minutes=same_moment,
        ),
        _started(handover_id),
    ]

    record = fold(events, system_id=_SYSTEM)[0]

    assert [row.id for row in record.events] == [
        "aud_01J00000000000000000FIRST",
        "aud_aa",
        "aud_zz",
    ]


# -- step order -------------------------------------------------------------


@pytest.mark.parametrize("step", sorted(PREREQUISITE))
def test_a_step_refuses_without_its_prerequisite_and_names_the_verb(step: str) -> None:
    """Each of the five ordered steps refuses, naming the command to run first."""
    handover_id = "aud_01J00000000000000000FIRST"
    record = fold([_started(handover_id)], system_id=_SYSTEM)[0]
    prerequisite = PREREQUISITE[step]
    if prerequisite == HANDOVER_STARTED:
        pytest.skip("its prerequisite is the start row this record already has")

    with pytest.raises(AdoptError) as raised:
        require(record, step)

    assert raised.value.code is ErrorCode.HANDOVER_STEP_OUT_OF_ORDER
    assert VERB_FOR[prerequisite] in raised.value.message
    assert raised.value.hint is not None
    assert VERB_FOR[prerequisite] in raised.value.hint


@pytest.mark.parametrize("step", sorted(PREREQUISITE))
def test_a_step_is_permitted_once_its_prerequisite_is_recorded(step: str) -> None:
    """The positive control: the refusal above is about the order, not the step.

    Without this, a `require` that refused everything would pass every case of
    the test above while making the whole checklist unusable.
    """
    handover_id = "aud_01J00000000000000000FIRST"
    events = [_started(handover_id)]
    for index, earlier in enumerate(STEP_ORDER[1:], start=1):
        if earlier == step:
            break
        events.append(
            _row(
                event_id=f"aud_{index}",
                event_type=earlier,
                handover_id=handover_id,
                minutes=index,
                detail={"audience": "technical"} if earlier == HANDOVER_PACK_EMITTED else {},
            )
        )
    record = fold(events, system_id=_SYSTEM)[0]

    require(record, step)  # does not raise


def test_next_step_walks_the_order_and_ends_at_none() -> None:
    """`status` reads this to tell an operator what to do next."""
    handover_id = "aud_01J00000000000000000FIRST"
    events = [_started(handover_id)]
    record = fold(events, system_id=_SYSTEM)[0]
    assert record.next_step == HANDOVER_ELICITED

    for index, step in enumerate(STEP_ORDER[1:], start=1):
        events.append(
            _row(
                event_id=f"aud_{index}",
                event_type=step,
                handover_id=handover_id,
                minutes=index,
                detail={"audience": "technical"} if step == HANDOVER_PACK_EMITTED else {},
            )
        )
    record = fold(events, system_id=_SYSTEM)[0]

    assert record.steps_done == STEP_ORDER
    assert record.next_step is None
    assert record.is_closed


# -- re-runs ----------------------------------------------------------------


def test_a_re_run_step_supersedes_for_reporting_and_keeps_both_rows() -> None:
    """A fresh snapshot reports as the current one; the earlier row survives."""
    handover_id = "aud_01J00000000000000000FIRST"
    events = [
        _started(handover_id),
        _row(
            event_id="aud_snap1",
            event_type=HANDOVER_SNAPSHOT_TAKEN,
            handover_id=handover_id,
            minutes=1,
            detail={"bundle_digest": "aaa", "rows": 10},
        ),
        _row(
            event_id="aud_snap2",
            event_type=HANDOVER_SNAPSHOT_TAKEN,
            handover_id=handover_id,
            minutes=2,
            detail={"bundle_digest": "bbb", "rows": 12},
        ),
    ]

    record = fold(events, system_id=_SYSTEM)[0]

    assert record.snapshot["bundle_digest"] == "bbb"
    assert len(record.all_of(HANDOVER_SNAPSHOT_TAKEN)) == 2, "the earlier row was lost"


def test_every_verification_round_is_reported_not_only_the_last() -> None:
    """Two sittings are two rounds -- the first one's failures are the record."""
    handover_id = "aud_01J00000000000000000FIRST"
    events = [
        _started(handover_id),
        _row(
            event_id="aud_v1",
            event_type=HANDOVER_VERIFIED,
            handover_id=handover_id,
            minutes=1,
            detail={"round": 1, "passed": 1, "failed": 4},
        ),
        _row(
            event_id="aud_v2",
            event_type=HANDOVER_VERIFIED,
            handover_id=handover_id,
            minutes=2,
            detail={"round": 2, "passed": 5, "failed": 0},
        ),
    ]

    record = fold(events, system_id=_SYSTEM)[0]

    assert [row["round"] for row in record.verification_rounds] == [1, 2]
    assert record.verification_rounds[0]["failed"] == 4


def test_packs_report_the_newest_emission_per_audience() -> None:
    """A re-emitted pack replaces its own audience and hides none of the others."""
    handover_id = "aud_01J00000000000000000FIRST"
    events = [
        _started(handover_id),
        _row(
            event_id="aud_p1",
            event_type=HANDOVER_PACK_EMITTED,
            handover_id=handover_id,
            minutes=1,
            detail={"audience": "technical", "markdown_sha256": "aaa"},
        ),
        _row(
            event_id="aud_p2",
            event_type=HANDOVER_PACK_EMITTED,
            handover_id=handover_id,
            minutes=1,
            detail={"audience": "client_ops", "markdown_sha256": "bbb"},
        ),
        _row(
            event_id="aud_p3",
            event_type=HANDOVER_PACK_EMITTED,
            handover_id=handover_id,
            minutes=5,
            detail={"audience": "technical", "markdown_sha256": "ccc"},
        ),
    ]

    record = fold(events, system_id=_SYSTEM)[0]

    assert [pack["audience"] for pack in record.packs] == ["client_ops", "technical"]
    assert record.packs[1]["markdown_sha256"] == "ccc"


# -- detail codec -----------------------------------------------------------


def test_detail_round_trips_and_renders_one_way() -> None:
    """Sorted keys, no spaces: two runs recording the same facts write one string."""
    payload = {"zeta": 1, "alpha": ["b", "a"], "nested": {"y": True, "x": None}}

    encoded = encode_detail(payload)

    assert encoded == encode_detail(dict(reversed(list(payload.items()))))
    assert " " not in encoded
    assert decode_detail(encoded) == payload


def test_a_detail_that_does_not_decode_is_reported_rather_than_raised() -> None:
    """`status` must still describe a handover whose trail was edited outside us."""
    handover_id = "aud_01J00000000000000000FIRST"
    events = [
        _started(handover_id),
        _row(
            event_id="aud_bad",
            event_type=HANDOVER_ELICITED,
            handover_id=handover_id,
            minutes=1,
            detail="{not json",
        ),
    ]

    record = fold(events, system_id=_SYSTEM)[0]

    assert record.malformed == ("aud_bad",)
    assert record.steps_done == (HANDOVER_STARTED, HANDOVER_ELICITED)
    assert record.detail_of(HANDOVER_ELICITED) == {}
    assert decode_detail(None) == {}
    assert decode_detail("[1,2]") == {}, "a JSON array is not a detail mapping"


def test_the_asked_for_event_types_are_exactly_the_six_steps() -> None:
    """The port's read and the checklist cannot drift apart.

    `list_audit_events` returns nothing for a type nobody asks for, so a step
    added to `STEP_ORDER` and forgotten here would be a step the fold could
    never see -- and the handover would silently stop at the step before it.
    """
    assert HANDOVER_EVENT_TYPES == STEP_ORDER
    assert len(set(STEP_ORDER)) == 6
    assert set(PREREQUISITE) == set(STEP_ORDER) - {HANDOVER_STARTED}
    assert set(VERB_FOR) == set(STEP_ORDER)
