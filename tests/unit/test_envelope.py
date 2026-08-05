"""The outbound-envelope gate: one row per named rejection, and the derived deny-list.

*Fails when* client content starts validating under `metadata_only`, when a
caller's declared policy starts outranking the boundary, or when the content
deny-list stops widening with the schema. *Matters because* this is the last
check before anything could carry client content out of their environment, and
the cost is asymmetric: a wrongly rejected envelope is a support ticket, a
wrongly permitted one is a disclosure. *No other instrument catches it because*
the property test proves no *deny-listed* field passes without proving the
deny-list is the right set, and CUJ-9 walks one journey.
"""

import json
from pathlib import Path

import pytest

from adopt_detect import METADATA_ONLY, BoundaryView
from adopt_obs import DENIED_FIELDS, AdoptError, ErrorCode, now
from adopt_policy import content_fields, find_content_fields, validate_envelope
from adopt_schema.manifest import load_manifest

ENVELOPES = Path(__file__).resolve().parent.parent / "fixtures" / "envelopes"


def _boundary(*categories: str) -> BoundaryView:
    return BoundaryView(
        boundary_id="ob_01J8",
        system_id="sys_01J8",
        environment_id="env_01J8",
        tier="T4",
        archetype="ai",
        knowledge_plane_location="customer",
        control_plane_location="vendor",
        permitted_outbound_categories=categories or (METADATA_ONLY,),
        unavailable_capabilities=(),
        contractual_approval_ref=None,
        declared_at=now(),
        decline_recommended=False,
        archetype_floor_violated=False,
    )


def _envelope(name: str) -> dict[str, object]:
    document = json.loads((ENVELOPES / f"{name}.json").read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


#: (fixture, expected code, the failure the rejection prevents).
REJECTIONS: list[tuple[str, ErrorCode, str]] = [
    (
        "content_under_metadata_only",
        ErrorCode.ENVELOPE_CONTENT_UNDER_METADATA_ONLY,
        "a client runbook leaving the environment in a metadata event",
    ),
    (
        "nested_content_under_metadata_only",
        ErrorCode.ENVELOPE_CONTENT_UNDER_METADATA_ONLY,
        "content two levels down is still content",
    ),
    (
        "policy_not_permitted",
        ErrorCode.ENVELOPE_POLICY_NOT_PERMITTED,
        "a sender declaring its own permission to carry content",
    ),
    (
        "missing_scope_id",
        ErrorCode.MANIFEST_INVALID,
        "an envelope that cannot be checked against any boundary at all",
    ),
    (
        "payload_not_an_object",
        ErrorCode.MANIFEST_INVALID,
        "a payload nothing can inspect field by field cannot be cleared to leave",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("fixture", "code", "prevents"), REJECTIONS)
def test_envelope_rejection(fixture: str, code: ErrorCode, prevents: str) -> None:
    with pytest.raises(AdoptError) as caught:
        validate_envelope(_envelope(fixture), _boundary())
    assert caught.value.code is code, prevents


@pytest.mark.unit
def test_a_metadata_only_envelope_validates() -> None:
    """Counts, ids, timestamps and states are what metadata-only is *for*.

    A gate that rejected everything would be safe and useless, and the product
    would route around it.
    """
    validate_envelope(_envelope("valid_metadata_only"), _boundary())


@pytest.mark.unit
def test_the_boundary_is_the_authority_not_the_declaration() -> None:
    """Contracts §8 rule 3, in both directions.

    The same envelope is refused against a metadata-only boundary and accepted
    against one that permits its policy -- which is what makes the boundary, and
    not the sender, the thing that decides.
    """
    envelope = _envelope("policy_not_permitted")
    with pytest.raises(AdoptError):
        validate_envelope(envelope, _boundary())
    validate_envelope(envelope, _boundary(METADATA_ONLY, "full_content"))


@pytest.mark.unit
def test_content_policy_defaults_to_metadata_only() -> None:
    """Contracts §8 rule 1: the default is the strict one, not the absent one."""
    envelope = _envelope("content_under_metadata_only")
    del envelope["content_policy"]
    with pytest.raises(AdoptError) as caught:
        validate_envelope(envelope, _boundary())
    assert caught.value.code is ErrorCode.ENVELOPE_CONTENT_UNDER_METADATA_ONLY


@pytest.mark.unit
def test_a_boundary_permitting_nothing_refuses_even_metadata_only() -> None:
    """Fail closed: an empty permitted list permits nothing, not everything."""
    empty = BoundaryView(
        boundary_id="ob_01J8",
        system_id="sys_01J8",
        environment_id=None,
        tier="T0",
        archetype=None,
        knowledge_plane_location="customer",
        control_plane_location="vendor",
        permitted_outbound_categories=(),
        unavailable_capabilities=(),
        contractual_approval_ref=None,
        declared_at=now(),
        decline_recommended=True,
        archetype_floor_violated=False,
    )
    with pytest.raises(AdoptError) as caught:
        validate_envelope(_envelope("valid_metadata_only"), empty)
    assert caught.value.code is ErrorCode.ENVELOPE_POLICY_NOT_PERMITTED


@pytest.mark.unit
def test_the_deny_list_is_the_logger_list_plus_the_manifests_prose_columns() -> None:
    """CR-39's derivation, asserted rather than described.

    *Fails when* the two halves stop being combined -- which is silent: the
    envelope validator would simply stop recognising a category of content as
    content, and start letting it out.
    """
    derived = content_fields()
    assert set(DENIED_FIELDS) <= derived

    manifest = load_manifest()
    prose_columns = {
        column.name
        for _, table in manifest.exportable_tables()
        for column in table.columns
        if column.type in {"md", "text"}
    }
    assert prose_columns
    assert prose_columns <= derived


@pytest.mark.unit
def test_a_new_prose_column_widens_the_deny_list_by_itself() -> None:
    """The property that makes the derivation worth having.

    *Matters because* a hand-kept list is correct until the next `md` column is
    added by someone with no reason to look at this file -- and the failure is
    silent in the permissive direction.
    """
    manifest = load_manifest()
    name, table = manifest.exportable_tables()[0]
    invented = table.model_copy(
        update={
            "columns": [
                *table.columns,
                table.columns[0].model_copy(
                    update={"name": "field_invented_by_a_later_build", "type": "md"}
                ),
            ]
        }
    )
    widened = manifest.model_copy(update={"tables": {**manifest.tables, name: invented}})
    assert "field_invented_by_a_later_build" in content_fields(widened)
    assert "field_invented_by_a_later_build" not in content_fields()


@pytest.mark.unit
def test_violations_are_reported_as_sorted_json_paths() -> None:
    """*Fails when* two identical refusals read as two different ones in a log."""
    payload = {"body_md": "x", "nested": {"question": "y"}, "items": [{"answer": "z"}]}
    assert find_content_fields(payload) == ("body_md", "items[0].answer", "nested.question")
