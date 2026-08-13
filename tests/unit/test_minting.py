"""URI minting -- contracts §3, §10 C1-C3; implementation spec §5.3.

The manifest, and what earned each row:

| Behavior | Tier | Instrument |
|---|---|---|
| Every kind mints through `build_uri` with its §3.1 convention | T2 | 13-row table |
| Five parameter syntaxes collapse to one form | T2 | matrix rows |
| Two frameworks describing one endpoint mint one URI | **T1** | dedicated case |
| Normalization order matters | T2 | order case |
| A selector-shaped `ui_component` key is refused | **T1** | rejection rows |
| Normalization never encodes | T3 | double-encode case |
| Normalization is idempotent | T2 | property (separate file) |

The cross-framework case is T1 rather than T2 because it is the one whose failure
is *silent*: two URIs for one referent inflate the coverage denominator while the
system stands still, and every ratio downstream is then wrong in a direction
nobody would question.
"""

import pytest
from adopt_map.minting import mint, normalize_local_key
from adopt_map.schemas import SurfaceFact

from adopt_identity import parse_uri, validate_uri
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope, ScopeNode

#: `02` §3.1's worked conventions, one row per kind. `expected_key` is what the
#: URI's key segment decodes back to -- asserted through `parse_uri` rather than
#: against an encoded literal, because an encoded literal in a test is a second
#: implementation of the escaping rules.
_KIND_CONVENTIONS = [
    ("endpoint", "http", "GET /api/v1/orders/{id}", "GET /api/v1/orders/{id}"),
    ("endpoint", "grpc", "orders.OrderService.Get", "orders.OrderService.Get"),
    ("endpoint", "graphql", "Query.orders", "Query.orders"),
    ("endpoint", "event", "orders.created", "orders.created"),
    ("db_field", "pg:public.orders", "status", "status"),
    ("db_field", "pg:public.orders", "*", "*"),
    ("symbol", "python", "orders.views.OrderDetailView.get", "orders.views.OrderDetailView.get"),
    ("job", "celery", "orders.tasks.reconcile_payments", "orders.tasks.reconcile_payments"),
    ("config_key", "django", "DATABASES.default.CONN_MAX_AGE", "DATABASES.default.CONN_MAX_AGE"),
    ("config_key", "secret:vault", "orders/db_password", "orders/db_password"),
    ("flag", "local", "orders.new_checkout", "orders.new_checkout"),
    ("prompt", "file", "prompts/answer_grounded.md", "prompts/answer_grounded.md"),
    ("tool_schema", "langgraph", "lookup_order", "lookup_order"),
    ("model_pin", "anthropic", "claude-sonnet-4-5@answer_chain", "claude-sonnet-4-5@answer_chain"),
    ("retrieval_config", "pgvector", "orders_kb.top_k", "orders_kb.top_k"),
    (
        "metadata_component",
        "salesforce",
        "CustomField.Account.ZFIELD_003",
        "CustomField.Account.ZFIELD_003",
    ),
    ("state_transition", "orders", "pending->paid", "pending->paid"),
    ("ui_component", "testid", "orders-submit", "orders-submit"),
    ("ui_component", "aria", "button:Submit order", "button:Submit order"),
]

#: `02` §3.2 rule 2. Five syntaxes, one rendering, the name preserved.
_PARAMETER_MATRIX = [
    ("GET /orders/:id", "GET /orders/{id}"),
    ("GET /orders/<int:id>", "GET /orders/{id}"),
    ("GET /orders/<id>", "GET /orders/{id}"),
    ("GET /orders/{id}", "GET /orders/{id}"),
    ("GET /orders/[id]", "GET /orders/{id}"),
    ("GET /orders/$id", "GET /orders/{id}"),
    ("GET /orders/<slug:order_ref>/items/:item_id", "GET /orders/{order_ref}/items/{item_id}"),
]


def _scope() -> Scope:
    return Scope(
        firm=ScopeNode(id="firm_01", slug="northwind"),
        engagement=ScopeNode(id="eng_01", slug="acme-erp"),
        system=ScopeNode(id="sys_01", slug="orders-api"),
        environment=ScopeNode(id="env_01", slug="prod"),
    )


def _fact(kind: str, namespace: str | None, key: str) -> SurfaceFact:
    return SurfaceFact(
        identity_kind=kind,  # type: ignore[arg-type]
        namespace=namespace,
        local_key=key,
        title=key,
    )


@pytest.mark.unit
@pytest.mark.parametrize(("kind", "namespace", "key", "expected_key"), _KIND_CONVENTIONS)
def test_every_kind_mints_a_canonical_uri(
    kind: str, namespace: str | None, key: str, expected_key: str
) -> None:
    """*Fails when* a kind's convention drifts from `02` §3.1.

    *Matters because* two extractors must agree byte-for-byte on one referent's
    URI or the identity forks. *No other instrument catches it because* both
    forks are individually well-formed and both validate.
    """
    uri = mint(_scope(), _fact(kind, namespace, key))
    validate_uri(uri)

    parsed = parse_uri(uri)
    assert (parsed.firm, parsed.engagement, parsed.system, parsed.environment) == (
        "northwind",
        "acme-erp",
        "orders-api",
        "prod",
    )
    assert parsed.kind == kind
    assert parsed.namespace == namespace
    # One segment, whose punctuation is data (`02` §3.1 rule 6).
    assert parsed.key == (expected_key,)


@pytest.mark.unit
@pytest.mark.parametrize(("raw", "normalized"), _PARAMETER_MATRIX)
def test_every_parameter_syntax_collapses_to_one_form(raw: str, normalized: str) -> None:
    """*Fails when* a router's syntax is added to the matrix and not to the pattern.

    *Matters because* rule 2 is what makes a Django route and its OpenAPI
    document one identity. *No other instrument catches it because* an
    un-normalized `:id` mints a perfectly valid URI for a referent that already
    has one.
    """
    assert normalize_local_key("endpoint", raw) == normalized


@pytest.mark.unit
def test_two_frameworks_describing_one_endpoint_mint_one_uri() -> None:
    """*Fails when* any §3.2 rule stops applying to one of the two spellings.

    *Matters because* this is `02` §10 C1's named case and the mechanism behind
    PRD F2's acceptance signal. A fork here inflates the coverage denominator
    silently -- the ratio falls while the system has not changed, and nobody
    questions a falling coverage number. *No other instrument catches it because*
    both URIs are individually canonical.
    """
    django = _fact("endpoint", "http", "get //api/v1//orders/<int:id>/")
    openapi = _fact("endpoint", "http", "GET /api/v1/orders/{id}?expand=lines#detail")

    assert mint(_scope(), django) == mint(_scope(), openapi)


@pytest.mark.unit
def test_normalization_order_is_load_bearing() -> None:
    """*Fails when* the §3.2 rules are reordered.

    *Matters because* rule 5 must strip the query string **before** rule 4
    considers the trailing slash: reversed, `/orders/?x=1` keeps its slash and
    mints a different URI from `/orders`. *No other instrument catches it
    because* each rule in isolation is implemented correctly.
    """
    assert normalize_local_key("endpoint", "GET /orders/?x=1") == "GET /orders"
    assert normalize_local_key("endpoint", "GET /orders/") == "GET /orders"
    # A bare root keeps its slash -- rule 4's stated exception.
    assert normalize_local_key("endpoint", "GET /") == "GET /"


@pytest.mark.unit
def test_a_non_path_kind_is_not_path_normalized() -> None:
    """*Fails when* the path rules are applied to every kind.

    *Matters because* a `symbol` key's punctuation is **data**: collapsing a
    `//` inside `namespace//member` or stripping a trailing `/` renames the
    referent. *No other instrument catches it because* the renamed URI is still
    canonical and still unique -- it simply names something that does not exist.
    """
    assert normalize_local_key("symbol", "pkg//mod.fn/") == "pkg//mod.fn/"
    assert normalize_local_key("config_key", "a.b:id") == "a.b:id"


@pytest.mark.unit
@pytest.mark.parametrize(
    "selector",
    [
        ".btn-primary",
        "#submit-order",
        "//div[@id='x']",
        "div[2]",
        "ul li:nth-child(3)",
        "button::before",
        "xpath=//button",
        "(120, 480)",
    ],
)
def test_a_selector_shaped_ui_component_key_is_refused(selector: str) -> None:
    """*Fails when* rungs 3-5 become a minting source.

    *Matters because* Bet 1 is "bind to identity, never to rendering", and a
    selector reaching a URI is a **P0** (PRD §1.6, B1-CR-11): the binding then
    breaks when the page is restyled, which is not a change to the referent.
    *No other instrument catches it because* a selector is a perfectly legal
    string and `build_uri` would encode it happily.
    """
    with pytest.raises(AdoptError) as caught:
        mint(_scope(), _fact("ui_component", "testid", selector))
    assert caught.value.code is ErrorCode.MAP_URI_CONSTRUCTION_BYPASS


@pytest.mark.unit
def test_a_stable_id_is_still_accepted() -> None:
    """The rejection above is narrow: rungs 1-2 must still mint."""
    assert mint(_scope(), _fact("ui_component", "testid", "orders-submit"))
    assert mint(_scope(), _fact("ui_component", "aria", "button:Submit order"))


@pytest.mark.unit
def test_normalization_never_percent_encodes() -> None:
    """*Fails when* normalization starts encoding.

    *Matters because* `build_uri()` encodes exactly once and **refuses** input
    that already carries an escape -- a pre-encoded key raises
    ``URI_DOUBLE_ENCODED`` rather than being silently encoded twice. *No other
    instrument catches it because* a doubly-encoded URI is still well-formed and
    would simply name a different referent forever.
    """
    assert "%" not in normalize_local_key("endpoint", "GET /api/v1/orders/{id}")

    with pytest.raises(AdoptError) as caught:
        mint(_scope(), _fact("endpoint", "http", "GET%20%2Fapi%2Fv1%2Forders"))
    assert caught.value.code is ErrorCode.URI_DOUBLE_ENCODED


@pytest.mark.unit
def test_the_environment_segment_comes_only_from_scope() -> None:
    """*Fails when* a fact gains any influence over the environment segment.

    *Matters because* PRD F6.1 is structural: a staging run cannot emit a
    production URI because the segment is not the extractor's to supply. *No
    other instrument catches it at this level* -- the fuzz suite proves it end to
    end, and this proves it at the one function that could break it.
    """
    staging = Scope(
        firm=ScopeNode(id="firm_01", slug="northwind"),
        engagement=ScopeNode(id="eng_01", slug="acme-erp"),
        system=ScopeNode(id="sys_01", slug="orders-api"),
        environment=ScopeNode(id="env_02", slug="staging"),
    )
    hostile = _fact("endpoint", "http", "GET /prod/../orders")
    assert parse_uri(mint(staging, hostile)).environment == "staging"
    assert parse_uri(mint(_scope(), hostile)).environment == "prod"
