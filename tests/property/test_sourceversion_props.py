"""Digest-stability invariants -- implementation spec §5.4 test focus.

One property, and it retires a whole category of example: **attribute insertion
order never changes a digest**.

*Fails when* the canonical form depends on the order a `dict` was built in --
which it would the moment anything reaches for `json.dumps` without `sort_keys`,
or iterates a `set`, or trusts Pydantic's field order to be the extractor's.
*Matters because* two extractors describing one referent build their attribute
dicts in whatever order their parsers walk the source, and a digest sensitive to
that order makes idempotence a coin flip: the same unchanged tree yields a
different composite on the next run and every downstream delta becomes noise.
*No other instrument catches it because* the table-driven cases in
`tests/unit/test_source_version.py` build every dict the same way, which is
exactly the condition under which an order-sensitive digest looks correct.
"""

from typing import Any

import pytest
from adopt_map.schemas.surface import SurfaceFact
from adopt_map.sourceversion import build_source_version
from hypothesis import given
from hypothesis import strategies as st

pytestmark = pytest.mark.property

#: A field of `EndpointAttributes` and a value for it, drawn from both
#: projections so the property covers `sem` and `ren` in one pass.
_FIELDS: dict[str, st.SearchStrategy[Any]] = {
    "http_method": st.sampled_from(["GET", "POST", "DELETE"]),
    "path": st.sampled_from(["/v1/orders", "/v1/orders/{id}", "/health"]),
    "parameters": st.lists(st.sampled_from(["id", "cursor"]), max_size=2),
    "status_codes": st.lists(st.sampled_from([200, 404, 500]), max_size=3),
    "auth": st.sampled_from(["session", "bearer", None]),
    "framework": st.sampled_from(["django", "fastapi", None]),
    "summary": st.sampled_from(["Orders", "", None]),
    "tags": st.lists(st.sampled_from(["public", "internal"]), max_size=2),
    "declaration_order": st.integers(min_value=0, max_value=50),
}


def _digests(attributes: dict[str, Any]) -> tuple[str | None, str | None]:
    fact = SurfaceFact(
        identity_kind="endpoint", namespace="http", local_key="k", title="t", attributes=attributes
    )
    version = build_source_version(
        fact, fact.validated_attributes().model_dump(mode="json"), vcs_revision=None
    )
    return version.sem, version.ren


@given(
    values=st.fixed_dictionaries(dict(_FIELDS)),
    order=st.permutations(sorted(_FIELDS)),
)
def test_attribute_order_never_changes_a_digest(values: dict[str, Any], order: list[str]) -> None:
    canonical = _digests(values)
    shuffled = _digests({name: values[name] for name in order})
    assert shuffled == canonical
