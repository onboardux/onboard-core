"""The surface attribute front-matter -- contracts §5, `02` §10 C6.

| Behavior | Tier | Defect it catches |
|---|---|---|
| Round-trip through render and parse | **T1** | A body this build writes and cannot read back |
| An unknown attribute key is rejected, both ways | **T1** | The closed schema widening at the one seam that reads text |
| `secret:*` admits `source` and `name` and nothing else | **T1** | A secret value acquiring somewhere to live |
| Relation targets are URIs, never database ids | T2 | An export that stops resolving off this machine |
| The rendering is byte-stable under key reordering | T2 | Two runs over one unchanged tree writing two bodies |
| Prose is optional | T2 | A refusal to write the honest empty case |
"""

import pytest
from adopt_map.frontmatter import FrontMatter, RenderedRelation, parse_body, render_body

from adopt_obs import AdoptError

pytestmark = pytest.mark.unit

_URI = (
    "onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/http/"
    "GET%20%2Fapi%2Fv1%2Forders%2F%7Bid%7D"
)
_SECRET_URI = "onboard-v1://northwind/acme-erp/orders-api/prod/config_key/secret%3Aenv/DB_PASSWORD"


def _endpoint(**overrides: object) -> FrontMatter:
    payload: dict[str, object] = {
        "identity_uri": _URI,
        "identity_kind": "endpoint",
        "method": "grammar",
        "confidence": 0.95,
        "attributes": {"http_method": "GET", "path": "/api/v1/orders/{id}", "framework": "django"},
        "relations": [
            RenderedRelation(
                predicate="handled_by",
                target="onboard-v1://northwind/acme-erp/orders-api/prod/symbol/python/orders.views.get",
            )
        ],
    }
    payload.update(overrides)
    return FrontMatter.model_validate(payload)


def test_round_trip_preserves_the_block_and_the_prose() -> None:
    front_matter = _endpoint()
    parsed = parse_body(render_body(front_matter, "Returns a single order."))

    assert parsed.front_matter == front_matter
    assert parsed.prose == "Returns a single order."
    assert parsed.front_matter.attrs_version == 1


def test_prose_is_optional() -> None:
    """`02` §5 rule 3: an empty prose block is valid and preferable to invention."""
    assert parse_body(render_body(_endpoint(), None)).prose == ""


def test_rendering_does_not_depend_on_attribute_order() -> None:
    ordered = _endpoint(attributes={"framework": "django", "http_method": "GET", "path": "/x"})
    reversed_ = _endpoint(attributes={"path": "/x", "http_method": "GET", "framework": "django"})
    assert render_body(ordered, None) == render_body(reversed_, None)


def test_an_unknown_attribute_key_is_refused_at_emit() -> None:
    """`02` §5.1 rule 1 -- the allowlist holds on the way out."""
    with pytest.raises(AdoptError):
        render_body(_endpoint(attributes={"http_method": "GET", "sniffed": "x"}), None)


def test_an_unknown_attribute_key_is_refused_at_parse() -> None:
    """And on the way in.

    Fails when a body written by a future version is trusted; matters because
    this is the one seam in the build that reads structured text back out of the
    store, so a permissive parse is where an undeclared field re-enters a system
    whose whole secret guarantee is that no such field exists; no other
    instrument catches it because the emit-side check passes on our own bodies.
    """
    body = render_body(_endpoint(), None).replace(
        "http_method: GET", "http_method: GET\n  leaked: x"
    )
    with pytest.raises(AdoptError):
        parse_body(body)


def test_an_unknown_block_key_is_refused() -> None:
    body = render_body(_endpoint(), None).replace("attrs_version: 1", "attrs_version: 1\nsecret: x")
    with pytest.raises(AdoptError):
        parse_body(body)


def test_a_secret_reference_block_admits_no_value_field() -> None:
    """`02` §5.1 rule 4 -- there is nowhere to put a secret.

    The namespace is recovered from the URI, so the block cannot claim an
    ordinary `config_key` model while addressing a `secret:*` identity.
    """
    secret = FrontMatter.model_validate(
        {
            "identity_uri": _SECRET_URI,
            "identity_kind": "config_key",
            "method": "grammar",
            "confidence": 0.95,
            "attributes": {"source": "env", "name": "DB_PASSWORD"},
        }
    )
    assert parse_body(render_body(secret, None)).front_matter == secret

    with pytest.raises(AdoptError):
        render_body(
            secret.model_copy(update={"attributes": {"source": "env", "value": "hunter2"}}), None
        )


def test_relation_targets_are_uris_not_database_ids() -> None:
    rendered = render_body(_endpoint(), None)
    assert "onboard-v1://" in rendered
    assert "idn_" not in rendered
    assert "ki_" not in rendered


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("no fence at all", id="no-fence"),
        pytest.param("---\nattrs_version: 1\n", id="unclosed-fence"),
        pytest.param("---\n- a\n- b\n---\n", id="not-a-mapping"),
        pytest.param("---\nattrs_version: 1\n---\n", id="missing-required-fields"),
    ],
)
def test_malformed_bodies_are_refused(body: str) -> None:
    with pytest.raises(AdoptError):
        parse_body(body)
