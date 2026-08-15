"""The packaged-platform pack -- `01` F8.3, `02` §3.1, `05` S1.6.

One table over the three readers' key forms, because `02` §3.1's
`metadata_component` row is the contract two extractors must agree on or one
referent forks into two identities. Everything else about these readers is either
asserted by the shared conformance suite (the eight obligations, over every
registered extractor) or by the golden (the observation set and its evidence
band), so this module holds the claims neither of those can see: **which key each
vendor's artefact produces, and what is deliberately not read out of it.**
"""

from pathlib import Path

import pytest
from adopt_extractors_platform import pack as platform_pack
from adopt_extractors_platform.sf_metadata import SfMetadataExtractor
from adopt_extractors_platform.snow_updateset import SnowUpdateSetExtractor

from tests.build1_conftest import context_for

pytestmark = pytest.mark.unit

_TREE = Path("fixtures/repos/sf-metadata-bundle")


def _facts() -> dict[str, dict[str, object]]:
    """Every fact the pack emits over the fixture, keyed by `namespace/local_key`."""
    ctx = context_for(_TREE, archetype="platform")
    found: dict[str, dict[str, object]] = {}
    for extractor in platform_pack():
        for fact in extractor.extract(ctx):
            found[f"{fact.namespace}/{fact.local_key}"] = {
                "attributes": fact.attributes,
                "opaque": fact.opaque,
                "title": fact.title,
                "extractor": extractor.manifest().id,
            }
    return found


#: `(key, the label the export states or None, the component type)`. One row per
#: shape rather than per component: three vendors and the labelled/unlabelled
#: split is the matrix that matters, and the fixture's remaining components are
#: the same shapes again.
_KEYS: list[tuple[str, str | None, str]] = [
    # Salesforce: the vendor that labels some things and not others.
    ("salesforce/CustomObject.Order__c", "Order", "CustomObject"),
    ("salesforce/CustomField.Order__c.Status__c", "Order Status", "CustomField"),
    ("salesforce/CustomField.Order__c.ZFIELD_003__c", None, "CustomField"),
    ("salesforce/CustomField.Account.Legacy_Code__c", None, "CustomField"),
    # SAP: the request carries the only human text in the artefact.
    ("sap/TransportRequest.DEVK900123", "Customer master extensions", "TransportRequest"),
    ("sap/R3TR.TABL.ZCUSTOMER", None, "R3TR TABL"),
    ("sap/LIMU.REPS.ZORDER_REPORT", None, "LIMU REPS"),
    # ServiceNow: table plus target name, sys id stripped.
    ("servicenow/sys_script_include.OrderUtils", None, "Script Include"),
    ("servicenow/sys_ui_policy.Hide legacy fields", None, "UI Policy"),
]


@pytest.mark.parametrize(("key", "label", "component_type"), _KEYS, ids=[row[0] for row in _KEYS])
def test_each_vendor_mints_the_key_shape_the_contract_states(
    key: str, label: str | None, component_type: str
) -> None:
    """*Defect sentence.* Fails when a reader's `local_key` form changes, or when
    a component acquires a label its export never stated; matters because the key
    is the identity -- a shifted form re-mints every component in a client's org
    as new, and an invented label empties the unlabelled bucket by lying into it
    (`01` §8 puts labelling in the human-only row); no other instrument catches
    the label half, because recall counts identities and is blind to attributes.
    """
    facts = _facts()
    assert key in facts, f"{key} not emitted; got {sorted(facts)[:8]}"
    attributes = facts[key]["attributes"]
    assert isinstance(attributes, dict)
    assert attributes.get("label") == label
    assert attributes.get("component_type") == component_type


def test_the_sys_id_is_stripped_and_the_table_survives() -> None:
    """A ServiceNow `name` is `<table>_<32-hex sys id>`; the sys id is per instance.

    Keeping it would re-mint every record the day a client refreshes a sub-prod
    instance from production, which is the one thing an identity must survive.
    """
    facts = _facts()
    assert "servicenow/sys_script.Set order approval state" in facts
    assert not any("9f8c1a2b" in key for key in facts)


def test_the_update_set_payload_never_reaches_a_fact() -> None:
    """`03` §5.9 invariant 4: no client source content in any artefact.

    The fixture's payloads carry a Script Include's source and a business rule's
    condition. Asserted over every attribute value of every fact rather than over
    the one field somebody might have put it in, because the failure this guards
    against is a well-meaning "record the payload for context" commit.
    """
    forbidden = ("Class.create", "current.state.changes", "record_update")
    for key, fact in _facts().items():
        attributes = fact["attributes"]
        assert isinstance(attributes, dict)
        rendered = " ".join(str(value) for value in attributes.values())
        for marker in forbidden:
            assert marker not in rendered, f"{key} carries client source: {marker}"


def test_a_document_declaring_an_entity_yields_no_facts_and_no_error(tmp_path: Path) -> None:
    """The `xmlsafe` refusal, reached through a real extractor.

    *Defect sentence.* Fails when a declining parse raises instead of degrading,
    or when an extractor reads a document the seam refused; matters because a
    bundle with one hostile or malformed entry must still map its other entries
    (`01` F9.2), and because `01` N8's "does not detonate" has to hold for the
    parser as well as for the interpreter; no other instrument catches it,
    because `test_xmlsafe` proves the seam and not that the readers go through it.
    """
    bundle = tmp_path / "bundle"
    (bundle / "objects").mkdir(parents=True)
    (bundle / "objects" / "Bomb__c.object-meta.xml").write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE lolz [<!ENTITY lol "lol">]>\n'
        '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        "  <label>&lol;</label>\n"
        "  <fields><fullName>X__c</fullName></fields>\n"
        "</CustomObject>\n",
        encoding="utf-8",
    )
    ctx = context_for(bundle, archetype="platform")
    assert list(SfMetadataExtractor().extract(ctx)) == []
    assert list(SnowUpdateSetExtractor().extract(ctx)) == []
