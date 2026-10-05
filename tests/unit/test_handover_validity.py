"""Independence status: which passed exit tests a later change has invalidated.

*Fails when* a later round stops superseding an earlier one for the same task,
when a cosmetic or pre-drill change invalidates a pass, or when a semantic
change after the drill does not. *Matters because* each turns the independence
summary into a wrong statement about whether the receiving team could still do
the job without the builder -- the one number this offering sells. *No other
instrument catches it because* the handover journey drives a single round and a
single death; these orderings only exist in the judgement function.
"""

import datetime as _dt
import json

import pytest
from adopt_handover import HANDOVER_VERIFIED, HandoverRecord
from adopt_handover.validity import INVALIDATED, VALID, ReferentFacts, assess

from adopt_model import AuditEvent

_T0 = _dt.datetime(2026, 10, 1, 9, tzinfo=_dt.UTC)
_U1 = "onboard-v1://f/e/s/prod/endpoint/-/POST%20%2Fv1%2Frefunds"
_U2 = "onboard-v1://f/e/s/prod/job/ci/deploy"


def _round(event_id: str, at: _dt.datetime, outcomes: list[dict[str, object]]) -> AuditEvent:
    return AuditEvent(
        id=event_id,
        firm_id="firm_1",
        system_id="sys_1",
        event_type=HANDOVER_VERIFIED,
        actor_id="alice",
        subject_ref="aud_1",
        detail=json.dumps({"outcomes": outcomes}),
        occurred_at=at,
    )


@pytest.mark.unit
def test_only_change_after_the_latest_passing_round_invalidates_a_task() -> None:
    record = HandoverRecord(
        id="aud_1",
        system_id="sys_1",
        scope="f/e/s",
        environment_id=None,
        receiving_owner="platform",
        is_group=True,
        started_at=_T0,
        started_by="alice",
        events=(
            _round(
                "aud_2",
                _T0,
                [
                    {"id": "refund", "outcome": "pass", "uri": _U1},
                    {"id": "deploy", "outcome": "fail", "uri": _U2},
                ],
            ),
            # The second sitting passes the task the first one failed.
            _round(
                "aud_3",
                _T0 + _dt.timedelta(days=1),
                [
                    {"id": "deploy", "outcome": "pass", "uri": _U2},
                ],
            ),
        ),
    )
    facts = {
        _U1: ReferentFacts(
            found=True,
            status="active",
            changes=(
                ("BINDING_INTACT_SEMANTICS_CHANGED", _T0 - _dt.timedelta(hours=1)),  # pre-drill
                ("BINDING_INTACT_SEMANTICS_CHANGED", _T0 + _dt.timedelta(days=2)),
            ),
        ),
        _U2: ReferentFacts(
            found=True,
            status="active",
            # After the passing round, but cosmetic: never an invalidation (H5).
            changes=(("BINDING_INTACT_RENDER_ONLY", _T0 + _dt.timedelta(days=3)),),
        ),
    }

    judged = {task.task_id: task for task in assess(record, facts).tasks}

    assert judged["refund"].status == INVALIDATED
    assert judged["refund"].cause == "BINDING_INTACT_SEMANTICS_CHANGED"
    assert judged["refund"].since == _T0 + _dt.timedelta(days=2)
    assert judged["deploy"].status == VALID
    assert judged["deploy"].verified_at == _T0 + _dt.timedelta(days=1)
