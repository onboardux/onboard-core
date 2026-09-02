"""The elicitation agenda: what gets asked, of whom, and what is deliberately absent.

*Fails when* a dispositioned gap reappears on the agenda, an expired waiver stays
buried, or the rendering stops being byte-stable. *Matters because* this file is
the SME session: a gap that vanishes from it never gets asked about and the
handover ships without that knowledge, while a resolved gap that reappears wastes
the one hour of an expert's time the engagement gets. *No other instrument
catches it because* both mistakes produce a perfectly well-formed agenda -- the
only evidence is whether the right rows are on it.

The expired-waiver case is the one worth the fixture cost. Build 4 made
`waived_until` mandatory precisely so a waiver is a decision that comes back;
if it never came back, the mandatory date would be decoration.
"""

import datetime as _dt

import pytest
from adopt_handover import (
    UNASSIGNED,
    PackConflict,
    PackGap,
    agenda,
    ask_for,
    is_open_gap,
    render_agenda,
)

pytestmark = pytest.mark.unit

_NOW = _dt.datetime(2026, 9, 2, 12, 0, tzinfo=_dt.UTC)
_BASE = "onboard-v1://northwind/acme-erp/orders-api/prod"


def _gap(
    *,
    key: str,
    kind: str = "endpoint",
    status: str | None = None,
    owner: str | None = None,
    waived_until: _dt.datetime | None = None,
    note: str | None = None,
) -> PackGap:
    return PackGap(
        uri=f"{_BASE}/{kind}/-/{key}",
        kind=kind,
        gap_key=key,
        reasons=("no_knowledge",),
        status=status,
        owner_actor_id=owner,
        note=note,
        waived_until=waived_until,
    )


# -- what belongs on the agenda --------------------------------------------


def test_resolved_and_live_waivers_are_absent_and_an_expired_waiver_returns() -> None:
    """The three dispositions, and the date that makes a waiver temporary."""
    live = _NOW + _dt.timedelta(days=30)
    expired = _NOW - _dt.timedelta(days=1)
    gaps = [
        _gap(key="open-one"),
        _gap(key="acknowledged-one", status="acknowledged", owner="alice"),
        _gap(key="resolved-one", status="resolved"),
        _gap(key="waived-live", status="waived", waived_until=live),
        _gap(key="waived-expired", status="waived", waived_until=expired),
    ]

    result = agenda(gaps, now=_NOW)
    keys = {item.gap_key for group in result.groups for item in group.items}

    assert keys == {"open-one", "acknowledged-one", "waived-expired"}
    assert "resolved-one" not in keys, "somebody already wrote this knowledge"
    assert "waived-live" not in keys, "a live waiver is a decision, not a question"
    assert "waived-expired" in keys, "a waiver that expired is a question again"


def test_a_waiver_with_no_expiry_is_treated_as_expired() -> None:
    """The safe direction for a row Build 4's facade cannot produce.

    `GAP_WAIVER_NEEDS_UNTIL` makes the date mandatory, so this is unreachable
    through the product -- and if a row ever lacked one, a gap silently removed
    forever is worse than one question too many.
    """
    assert is_open_gap(_gap(key="k", status="waived", waived_until=None), now=_NOW)


def test_an_acknowledged_gap_stays_on_the_agenda_with_its_disposition_shown() -> None:
    """`acknowledged` means a session is booked, not that the answer exists."""
    result = agenda(
        [_gap(key="k", status="acknowledged", owner="alice", note="SME session booked")],
        now=_NOW,
    )

    item = result.groups[0].items[0]
    assert item.status == "acknowledged"
    assert item.note == "SME session booked"
    assert "SME session booked" in render_agenda(result)


# -- grouping ---------------------------------------------------------------


def test_gaps_group_by_owner_with_unassigned_last() -> None:
    """The agenda is a set of bookable sessions plus the triage pile."""
    gaps = [
        _gap(key="z", owner="zoe"),
        _gap(key="a", owner="alice"),
        _gap(key="u1"),
        _gap(key="u2"),
    ]

    result = agenda(gaps, now=_NOW)

    assert [group.owner for group in result.groups] == ["alice", "zoe", UNASSIGNED]
    assert result.groups[-1].is_unassigned
    assert len(result.groups[-1].items) == 2
    assert result.by_owner() == {"alice": 1, "zoe": 1, UNASSIGNED: 2}
    assert result.item_count == 4


# -- the asks themselves ----------------------------------------------------


def test_every_identity_kind_asks_a_question_naming_its_referent() -> None:
    """A kind with no template still gets a real question, never silence."""
    kinds = [
        "endpoint",
        "db_field",
        "state_transition",
        "symbol",
        "metadata_component",
        "prompt",
        "tool_schema",
        "model_pin",
        "retrieval_config",
        "flag",
        "job",
        "config_key",
        "ui_component",
        "a_kind_shipped_after_this_map",
        "?",
    ]

    for kind in kinds:
        ask = ask_for(kind, f"{_BASE}/{kind}/-/thing")
        assert ask.endswith("?"), f"{kind} does not ask a question"
        assert f"{_BASE}/{kind}/-/thing" in ask, f"{kind} does not name the referent"


def test_conflicts_are_carried_and_rendered_under_their_own_heading() -> None:
    """Bet 4's deliverable: the contradiction is a question for the same people."""
    conflict = PackConflict(
        uri=f"{_BASE}/endpoint/-/POST %2Fv1%2Frefunds",
        kind="endpoint",
        intent_revision_id="krev_01J",
        detected_at=_NOW,
    )

    rendered = render_agenda(agenda([_gap(key="k")], [conflict], now=_NOW))

    assert "Contradictions to resolve" in rendered
    assert "POST %2Fv1%2Frefunds" in rendered


# -- rendering --------------------------------------------------------------


def test_the_rendering_is_byte_stable_across_calls() -> None:
    """No clock and no dict order reaches the file, so a re-run is a no-op diff."""
    gaps = [_gap(key="b", owner="zoe"), _gap(key="a"), _gap(key="c", owner="alice")]
    result = agenda(gaps, now=_NOW)

    assert render_agenda(result) == render_agenda(result)
    assert render_agenda(result) == render_agenda(agenda(list(reversed(gaps)), now=_NOW))


def test_an_empty_agenda_says_so_rather_than_rendering_an_empty_file() -> None:
    """A blank page reads as a broken command; this reads as a finished one."""
    rendered = render_agenda(agenda([], now=_NOW))

    assert "No open gaps" in rendered
    assert "nothing to elicit" in rendered
