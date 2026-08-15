"""`adopt_map.xmlsafe` -- the seam every export bundle is read through.

*Defect sentence.* Fails when a client document can reach `ElementTree` carrying
a DTD or an entity declaration, or when an unreadable document raises instead of
degrading; matters because `01` N8 promises a client tree cannot detonate inside
this tool and a billion-laughs document detonates the **parser** before any of
our code runs, and because a bundle with one bad entry must still map its other
entries (`01` F9.2); no other instrument catches it because the extractor audit
reads *imports* and this hazard arrives through an import the seam is allowed to
make.
"""

import pytest
from adopt_map.xmlsafe import child, child_text, iter_elements, local_name, parse_xml, text_of

pytestmark = pytest.mark.unit

#: The classic amplification document, at three entities rather than nine. The
#: point is the *declaration*, not the blow-up: this module refuses the shape, so
#: a test that needed a real expansion to fail would be a test that had already
#: let the parser have it.
_BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol1 "&lol;&lol;&lol;&lol;">
  <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;">
]>
<lolz>&lol2;</lolz>"""

_EXTERNAL_ENTITY = """<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<foo>&xxe;</foo>"""

_SALESFORCE = """<?xml version="1.0" encoding="UTF-8"?>
<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">
  <fields>
    <fullName>ZFIELD_003__c</fullName>
    <type>Text</type>
  </fields>
  <fields>
    <fullName>Status__c</fullName>
    <label>Order Status</label>
    <type>Picklist</type>
  </fields>
</CustomObject>"""


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("billion_laughs", _BILLION_LAUGHS),
        ("external_entity", _EXTERNAL_ENTITY),
        ("entity_without_doctype", '<!ENTITY x "y"><root/>'),
        ("lowercase_doctype", '<!doctype foo [<!ENTITY a "b">]><foo/>'),
        ("not_well_formed", "<root><unclosed></root>"),
        ("empty", ""),
        ("not_xml_at_all", "this is a README"),
    ],
)
def test_a_document_that_cannot_be_read_safely_returns_none(name: str, text: str) -> None:
    """Refusal is a `None`, never an exception and never a partial parse."""
    assert parse_xml(text) is None, name


def test_a_declaring_document_is_refused_before_the_parser_sees_it() -> None:
    """The scan is the point: `ElementTree` is never handed the bytes.

    Asserted through the seam's own contract rather than by patching -- the
    refusal and the "never parsed" claim are the same claim, because there is
    exactly one call site and it is guarded.
    """
    assert parse_xml(_BILLION_LAUGHS) is None
    # The same document with its prolog removed is ordinary XML, which is what
    # makes the refusal attributable to the declaration rather than to the shape.
    assert parse_xml("<lolz>lol</lolz>") is not None


def test_a_namespaced_document_reads_by_local_name() -> None:
    root = parse_xml(_SALESFORCE)
    assert root is not None
    assert local_name(root.tag) == "CustomObject"
    fields = list(iter_elements(root, "fields"))
    assert [child_text(field, "fullName") for field in fields] == ["ZFIELD_003__c", "Status__c"]


def test_document_order_is_preserved_across_two_reads() -> None:
    """`02` §7 obligation 3 rests on this: order is the file's, not the machine's."""
    first = [child_text(f, "fullName") for f in iter_elements(parse_xml(_SALESFORCE), "fields")]  # type: ignore[arg-type]
    second = [child_text(f, "fullName") for f in iter_elements(parse_xml(_SALESFORCE), "fields")]  # type: ignore[arg-type]
    assert first == second == ["ZFIELD_003__c", "Status__c"]


def test_absent_and_empty_children_are_one_answer() -> None:
    """*The bundle does not say* is one fact, so it has one spelling."""
    root = parse_xml("<a><empty></empty><blank>   </blank><said>x</said></a>")
    assert root is not None
    assert child_text(root, "empty") is None
    assert child_text(root, "blank") is None
    assert child_text(root, "missing") is None
    assert child_text(root, "said") == "x"
    assert child(root, "missing") is None
    assert text_of(None) is None


def test_traversal_is_depth_bounded() -> None:
    """A hostile nesting exhausts the bound rather than the stack."""
    depth = 500
    text = "<r>" + "<n>" * depth + "<leaf/>" + "</n>" * depth + "</r>"
    root = parse_xml(text)
    assert root is not None
    assert list(iter_elements(root, "leaf")) == []
