"""No envelope carrying a deny-listed field ever validates under `metadata_only`.

*Fails when* a content field reaches a place the walk does not look -- inside a
list, under a key whose sibling is innocent, at a nesting depth nobody wrote a
fixture for. *Matters because* the rejection table asserts the five shapes
somebody thought of, and the shapes that leak client content are the ones nobody
thought of. *No other instrument catches it because* a table is a list of
remembered cases and this is a statement about **all** payloads.

This is a permanent zero-violation gate, in the same family as the planted-secret
egress property: it does not measure a rate, it asserts that a set is empty.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from adopt_detect import METADATA_ONLY, BoundaryView
from adopt_obs import AdoptError, ErrorCode, now
from adopt_policy import content_fields, validate_envelope

BOUNDARY = BoundaryView(
    boundary_id="ob_01J8",
    system_id="sys_01J8",
    environment_id="env_01J8",
    tier="T4",
    archetype="ai",
    knowledge_plane_location="customer",
    control_plane_location="vendor",
    permitted_outbound_categories=(METADATA_ONLY,),
    unavailable_capabilities=(),
    contractual_approval_ref=None,
    declared_at=now(),
    decline_recommended=False,
    archetype_floor_violated=False,
)

#: Sorted so hypothesis's shrinking is reproducible across runs.
DENIED = sorted(content_fields())

#: Keys that are *not* content, used to bury a denied one among innocent siblings.
INNOCENT = st.sampled_from(["count", "item_id", "occurred_at", "state", "duration_ms"])

#: Values a payload may legitimately carry: metadata, never prose.
SAFE_VALUES = st.one_of(st.integers(), st.booleans(), st.none(), st.just("ki_01J8"))


def _nest(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Bury a value under dicts and lists to an arbitrary depth."""
    return st.recursive(
        inner,
        lambda children: st.one_of(
            st.dictionaries(INNOCENT, children, min_size=1, max_size=3),
            st.lists(children, min_size=1, max_size=3),
        ),
        max_leaves=6,
    )


def _envelope(payload: object) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "firm_id": "firm_01J8",
        "engagement_id": "eng_01J8",
        "system_id": "sys_01J8",
        "environment_id": "env_01J8",
        "event_type": "knowledge.marked_stale",
        "occurred_at": "2026-08-05T10:15:00.000Z",
        "content_policy": METADATA_ONLY,
        "payload": payload,
    }


@pytest.mark.property
@settings(max_examples=200)
@given(
    field=st.sampled_from(DENIED),
    value=st.text(max_size=40),
    buried=st.data(),
)
def test_no_payload_carrying_a_denied_field_validates(
    field: str, value: str, buried: st.DataObject
) -> None:
    """For every deny-listed field, at any depth, under any siblings: rejected."""
    carrier: dict[str, object] = {field: value}
    payload = buried.draw(_nest(st.just(carrier)))
    if not isinstance(payload, dict):
        payload = {"wrapper": payload}

    with pytest.raises(AdoptError) as caught:
        validate_envelope(_envelope(payload), BOUNDARY)
    assert caught.value.code is ErrorCode.ENVELOPE_CONTENT_UNDER_METADATA_ONLY


@pytest.mark.property
@settings(max_examples=100)
@given(payload=_nest(SAFE_VALUES))
def test_a_payload_of_only_metadata_validates(payload: object) -> None:
    """The other half, and the reason the first half is not vacuous.

    A validator that rejected every payload would satisfy the property above and
    be useless. This says the gate lets through exactly what metadata-only is
    for -- so the two together pin it from both sides.
    """
    if not isinstance(payload, dict):
        payload = {"wrapper": payload}
    validate_envelope(_envelope(payload), BOUNDARY)
