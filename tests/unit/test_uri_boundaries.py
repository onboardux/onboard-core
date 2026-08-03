"""The identity URI boundary table -- contracts §4, one row per rule.

*Fails when* the URI grammar accepts something it must refuse, refuses something
it must accept, or renders a referent differently from the way contracts §4
writes it down. *Matters because* `identity.uri` is `UNIQUE` and is the only name
a referent has after the store leaves our hands: a grammar that drifts either
merges two referents or loses one, and both are undetectable afterwards. *No
other instrument catches it because* the round-trip properties assert
self-consistency -- a builder and parser that agreed on the *wrong* grammar would
pass every one of them.

The seven worked examples are asserted verbatim against the document, which is
the one check that cannot be satisfied by a self-consistent mistake.

No per-`identity_kind` test: they are one equivalence class (impl spec §4.6).
"""

import pytest

from adopt_const import URI_MAX_BYTES
from adopt_identity import EMPTY_NAMESPACE, IdentityUri, build_uri, parse_uri, validate_uri
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope, ScopeNode

_PROD = Scope(
    firm=ScopeNode(id="firm_x", slug="northwind"),
    engagement=ScopeNode(id="eng_x", slug="acme-erp"),
    system=ScopeNode(id="sys_x", slug="orders-api"),
    environment=ScopeNode(id="env_x", slug="prod"),
)


def _scope(firm: str, engagement: str, system: str, environment: str) -> Scope:
    return Scope(
        firm=ScopeNode(id="firm_x", slug=firm),
        engagement=ScopeNode(id="eng_x", slug=engagement),
        system=ScopeNode(id="sys_x", slug=system),
        environment=ScopeNode(id="env_x", slug=environment),
    )


#: Contracts §4 "Worked examples", verbatim. The last two are the reason
#: `environment` is mandatory: the same field in two environments is two
#: referents, and a URI that could not say so would merge them.
WORKED_EXAMPLES: list[tuple[str, Scope, str, str | None, str | tuple[str, ...], str]] = [
    (
        "endpoint in prod",
        _PROD,
        "endpoint",
        None,
        "POST /v1/orders",
        "onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/POST%20%2Fv1%2Forders",
    ),
    (
        "db field",
        _PROD,
        "db_field",
        "public",
        ("orders", "total_cents"),
        "onboard-v1://northwind/acme-erp/orders-api/prod/db_field/public/orders/total_cents",
    ),
    (
        "symbol path",
        _PROD,
        "symbol",
        "billing",
        ("charges", "refund"),
        "onboard-v1://northwind/acme-erp/orders-api/prod/symbol/billing/charges/refund",
    ),
    (
        "prompt",
        _scope("northwind", "acme-ai", "support-bot", "prod"),
        "prompt",
        None,
        "ans-001",
        "onboard-v1://northwind/acme-ai/support-bot/prod/prompt/-/ans-001",
    ),
    (
        "pinned model",
        _scope("northwind", "acme-ai", "support-bot", "prod"),
        "model_pin",
        "services",
        ("answer", "model"),
        "onboard-v1://northwind/acme-ai/support-bot/prod/model_pin/services/answer/model",
    ),
    (
        "salesforce field",
        _scope("northwind", "acme-crm", "sfdc", "prod"),
        "metadata_component",
        "CustomField",
        "Account.ZFIELD_003",
        "onboard-v1://northwind/acme-crm/sfdc/prod/metadata_component/CustomField/"
        "Account.ZFIELD_003",
    ),
    (
        "same field in staging",
        _scope("northwind", "acme-crm", "sfdc", "staging"),
        "metadata_component",
        "CustomField",
        "Account.ZFIELD_003",
        "onboard-v1://northwind/acme-crm/sfdc/staging/metadata_component/CustomField/"
        "Account.ZFIELD_003",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("scope", "kind", "namespace", "key", "expected"),
    [pytest.param(*row[1:], id=row[0]) for row in WORKED_EXAMPLES],
)
def test_worked_examples_render_exactly_as_the_contract_writes_them(
    scope: Scope, kind: str, namespace: str | None, key: str | tuple[str, ...], expected: str
) -> None:
    assert build_uri(scope, kind, namespace, key) == expected
    validate_uri(expected)


@pytest.mark.unit
def test_the_two_environments_of_one_field_are_two_uris() -> None:
    """Why `environment` is mandatory, asserted rather than asserted-in-prose."""
    prod = build_uri(*_metadata_args("prod"))
    staging = build_uri(*_metadata_args("staging"))

    assert prod != staging


def _metadata_args(environment: str) -> tuple[Scope, str, str | None, str]:
    return (
        _scope("northwind", "acme-crm", "sfdc", environment),
        "metadata_component",
        "CustomField",
        "Account.ZFIELD_003",
    )


# --------------------------------------------------------------------------
# Rejections. One row per normative rule that can be violated.
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("uri", "code", "why"),
    [
        pytest.param(
            "onboard-v2://northwind/acme-erp/orders-api/prod/endpoint/-/x",
            ErrorCode.URI_SCHEME_UNKNOWN,
            "a label the parser does not know may mean anything",
            id="future-scheme-label",
        ),
        pytest.param(
            "adopt://northwind/acme-erp/orders-api/prod/endpoint/-/x",
            ErrorCode.URI_SCHEME_UNKNOWN,
            "the withdrawn proposal's label is not this grammar (CR-06)",
            id="withdrawn-scheme-label",
        ),
        pytest.param(
            "northwind/acme-erp/orders-api/prod/endpoint/-/x",
            ErrorCode.URI_SCHEME_UNKNOWN,
            "no label at all",
            id="no-scheme-label",
        ),
        pytest.param(
            "onboard-v1://northwind/acme-erp/orders-api/endpoint/-/x",
            ErrorCode.URI_MALFORMED,
            "six segments: environment is missing and is mandatory",
            id="environment-missing",
        ),
        pytest.param(
            "onboard-v1://northwind/acme-erp/orders-api/prod//-/x",
            ErrorCode.URI_MALFORMED,
            "an empty segment; the grammar requires 1* characters",
            id="empty-segment",
        ),
        pytest.param(
            "onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/",
            ErrorCode.URI_MALFORMED,
            "an empty key",
            id="empty-key",
        ),
        pytest.param(
            "onboard-v1://northwind/acme-erp/orders-api/prod/webhook/-/x",
            ErrorCode.URI_MALFORMED,
            "an undeclared identity_kind exports as a value no reader can interpret",
            id="unknown-kind",
        ),
        pytest.param(
            "onboard-v1://northwind/acme-erp/ORDERS-API/prod/endpoint/-/x",
            ErrorCode.URI_MALFORMED,
            "a scope segment that is not a slug",
            id="scope-segment-not-a-slug",
        ),
        pytest.param(
            "onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/POST%2520%252Fv1",
            ErrorCode.URI_DOUBLE_ENCODED,
            "decoding once still yields percent escapes",
            id="double-encoded",
        ),
    ],
)
def test_parse_rejects(uri: str, code: ErrorCode, why: str) -> None:
    with pytest.raises(AdoptError) as raised:
        parse_uri(uri)

    assert raised.value.code is code, why


@pytest.mark.unit
def test_an_over_length_uri_is_rejected_and_never_truncated() -> None:
    """Truncation would silently merge two distinct referents (rule 7).

    The over-length case is computed from `URI_MAX_BYTES` rather than written
    out: the scheme label grew from 8 bytes to 13 at CR-06, and a hard-coded
    corpus would have gone on testing the old budget.
    """
    prefix = build_uri(_PROD, "endpoint", None, "x")
    headroom = URI_MAX_BYTES - len(prefix.encode("utf-8")) + 1

    with pytest.raises(AdoptError) as raised:
        build_uri(_PROD, "endpoint", None, "x" * (headroom + 1))

    assert raised.value.code is ErrorCode.URI_TOO_LONG
    assert str(URI_MAX_BYTES) in raised.value.message


@pytest.mark.unit
def test_a_uri_at_exactly_the_maximum_is_accepted() -> None:
    """The boundary is inclusive, and the row proves the rejection above is not off by one."""
    prefix = build_uri(_PROD, "endpoint", None, "x")
    headroom = URI_MAX_BYTES - len(prefix.encode("utf-8")) + 1

    at_limit = build_uri(_PROD, "endpoint", None, "x" * headroom)

    assert len(at_limit.encode("utf-8")) == URI_MAX_BYTES
    validate_uri(at_limit)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("namespace", "key", "code", "why"),
    [
        pytest.param(
            None, "", ErrorCode.URI_MALFORMED, "an empty key names nothing", id="empty-key"
        ),
        pytest.param(
            None,
            ("a", ""),
            ErrorCode.URI_MALFORMED,
            "an empty key segment",
            id="empty-key-segment",
        ),
        pytest.param(
            "-",
            "x",
            ErrorCode.URI_MALFORMED,
            "a literal '-' cannot be told apart from the empty namespace",
            id="literal-dash-namespace",
        ),
        pytest.param(
            None,
            "already%20encoded",
            ErrorCode.URI_DOUBLE_ENCODED,
            "encoding a pre-encoded key twice makes it undecodable",
            id="pre-encoded-key",
        ),
        pytest.param(
            "ns%2Fx",
            "x",
            ErrorCode.URI_DOUBLE_ENCODED,
            "the same, for the namespace",
            id="pre-encoded-namespace",
        ),
    ],
)
def test_build_rejects(
    namespace: str | None, key: str | tuple[str, ...], code: ErrorCode, why: str
) -> None:
    with pytest.raises(AdoptError) as raised:
        build_uri(_PROD, "endpoint", namespace, key)

    assert raised.value.code is code, why


@pytest.mark.unit
@pytest.mark.parametrize(
    ("scope", "why"),
    [
        pytest.param(
            Scope(firm=ScopeNode(id="firm_x", slug="northwind")),
            "firm alone",
            id="firm-only",
        ),
        pytest.param(
            Scope(
                firm=ScopeNode(id="firm_x", slug="northwind"),
                engagement=ScopeNode(id="eng_x", slug="acme-erp"),
                system=ScopeNode(id="sys_x", slug="orders-api"),
            ),
            "environment is mandatory even when the other three resolved",
            id="no-environment",
        ),
    ],
)
def test_build_requires_all_four_scope_levels(scope: Scope, why: str) -> None:
    with pytest.raises(AdoptError) as raised:
        build_uri(scope, "endpoint", None, "x")

    assert raised.value.code is ErrorCode.URI_MALFORMED, why


# --------------------------------------------------------------------------
# Acceptances that are easy to break.
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_a_literal_percent_that_is_not_an_escape_survives() -> None:
    """`100%` is ordinary text; only `%XX` is an escape.

    Treating every `%` as an escape would make an identifier ending in a percent
    sign unaddressable, which is a rule nobody wrote down.
    """
    uri = build_uri(_PROD, "endpoint", None, "GET /discount/100%")

    assert parse_uri(uri).key == ("GET /discount/100%",)


@pytest.mark.unit
def test_case_is_preserved_and_never_folded() -> None:
    """Source identifiers are case-sensitive; folding merges distinct referents."""
    lower = build_uri(_PROD, "symbol", None, "getorder")
    upper = build_uri(_PROD, "symbol", None, "getOrder")

    assert lower != upper
    assert parse_uri(upper).key == ("getOrder",)


@pytest.mark.unit
def test_nfd_input_is_normalized_to_nfc_and_compares_equal() -> None:
    """Rule 6: NFC-normalize, then compare byte-exact.

    The two spellings of `é` are the same referent; without NFC the store would
    hold it twice and `identity.uri`'s uniqueness would not notice.
    """
    composed = build_uri(_PROD, "symbol", None, "caf\u00e9")
    decomposed = build_uri(_PROD, "symbol", None, "cafe\u0301")

    assert composed == decomposed


@pytest.mark.unit
def test_an_empty_namespace_renders_as_a_single_dash_and_parses_back_to_none() -> None:
    uri = build_uri(_PROD, "endpoint", None, "x")

    assert uri.endswith(f"/endpoint/{EMPTY_NAMESPACE}/x")
    assert parse_uri(uri).namespace is None


@pytest.mark.unit
def test_a_one_segment_key_containing_a_slash_is_not_a_two_segment_key() -> None:
    """The distinction the worked examples turn on, asserted directly."""
    data_slash = build_uri(_PROD, "endpoint", None, "a/b")
    structural = build_uri(_PROD, "endpoint", None, ("a", "b"))

    assert data_slash != structural
    assert parse_uri(data_slash).key == ("a/b",)
    assert parse_uri(structural).key == ("a", "b")


@pytest.mark.unit
def test_validate_refuses_a_non_canonical_rendering_that_still_parses() -> None:
    """Over-encoding parses, but is not the URI the builder would produce.

    `identity.uri` is `UNIQUE`, so two spellings of one referent would be two
    rows; refusing the non-canonical spelling at the boundary is what keeps the
    uniqueness constraint meaningful.
    """
    over_encoded = "onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/%61"

    assert parse_uri(over_encoded).key == ("a",)
    with pytest.raises(AdoptError) as raised:
        validate_uri(over_encoded)

    assert raised.value.code is ErrorCode.URI_MALFORMED


@pytest.mark.unit
def test_the_parsed_value_renders_back_to_the_uri_it_came_from() -> None:
    uri = build_uri(_PROD, "db_field", "public", ("orders", "total_cents"))
    parsed = parse_uri(uri)

    assert isinstance(parsed, IdentityUri)
    assert parsed.render() == uri
    assert parsed.key_path == "orders/total_cents"
