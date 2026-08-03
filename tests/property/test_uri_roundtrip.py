"""The two URI round-trip properties, over the adversarial corpus (contracts §4 rule 8).

*Fails when* `build` and `parse` stop being inverses -- an encoder that escapes a
character the decoder does not unescape, a normalization applied on one side
only, a key whose segment structure survives one direction and not the other.
*Matters because* `identity.uri` is `UNIQUE` and is the only durable name a
referent has: a lossy round-trip means one referent held twice, or two referents
collapsed into one, and neither is recoverable after the bundle ships. *No other
instrument catches it because* the boundary table asserts named cases, and the
failures here live in value *classes* -- the astral-plane character, the
percent-adjacent text, the segment that is empty only after normalization -- that
nobody enumerates in advance.

Two directions, because rule 8 states two:

* `parse(build(x)) == x` -- the builder loses nothing.
* `build(parse(s)) == s` -- the parser invents nothing, for canonical `s`.

The corpus is data, not code (`tests/fixtures/uris/adversarial.jsonl`), so a new
adversarial case is a line rather than a commit to this file -- and each line
carries the outcome it expects, so a case cannot be added without stating what it
proves.
"""

import json
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from adopt_const import URI_MAX_BYTES
from adopt_identity import IDENTITY_KINDS, IdentityUri, build_uri, parse_uri, validate_uri
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope, ScopeNode

CORPUS_PATH: Final[Path] = (
    Path(__file__).resolve().parents[1] / "fixtures" / "uris" / "adversarial.jsonl"
)

#: Placeholders resolved from `URI_MAX_BYTES` at load time. Written as tokens in
#: the corpus rather than as literal padding because the payload budget moved
#: when CR-06 made the scheme label 13 bytes instead of 8, and a corpus carrying
#: the old padding would have gone on testing a budget that no longer exists.
_MAX_TOKEN: Final[str] = "@URI_MAX_BYTES_FILLER@"
_OVER_TOKEN: Final[str] = "@URI_MAX_BYTES_FILLER+1@"

_KINDS: Final[tuple[str, ...]] = tuple(sorted(IDENTITY_KINDS))


def _scope(slugs: list[str]) -> Scope:
    return Scope(
        firm=ScopeNode(id="firm_x", slug=slugs[0]),
        engagement=ScopeNode(id="eng_x", slug=slugs[1]),
        system=ScopeNode(id="sys_x", slug=slugs[2]),
        environment=ScopeNode(id="env_x", slug=slugs[3]),
    )


def _headroom(scope: Scope, kind: str) -> int:
    """How many single-byte key characters fit before `URI_MAX_BYTES` is reached."""
    shortest = build_uri(scope, kind, None, "x")
    return URI_MAX_BYTES - len(shortest.encode("utf-8")) + 1


def _resolve_key(raw: list[str], scope: Scope, kind: str) -> tuple[str, ...]:
    if raw == [_MAX_TOKEN]:
        return ("x" * _headroom(scope, kind),)
    if raw == [_OVER_TOKEN]:
        return ("x" * (_headroom(scope, kind) + 1),)
    return tuple(raw)


def _nfc(value: str) -> str:
    """Rule 6 applied to an expectation.

    Rule 8's `parse(build(x)) == x` is stated over *canonical* values, and rule 6
    says the canonical form is NFC. Asserting against the raw input instead would
    require the builder to preserve a decomposed spelling -- which is precisely
    the behaviour that lets one referent occupy two `identity.uri` rows.
    """
    return unicodedata.normalize("NFC", value)


def _corpus() -> Iterator[dict[str, Any]]:
    with CORPUS_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


CORPUS: Final[list[dict[str, Any]]] = list(_corpus())
ACCEPTED: Final[list[dict[str, Any]]] = [row for row in CORPUS if row["outcome"] == "ok"]
REJECTED: Final[list[dict[str, Any]]] = [row for row in CORPUS if row["outcome"] != "ok"]


@pytest.mark.property
@pytest.mark.parametrize("row", ACCEPTED, ids=[row["case"] for row in ACCEPTED])
def test_uri_roundtrip_parse_of_build_is_identity_over_the_corpus(row: dict[str, Any]) -> None:
    """`parse(build(x)) == x` -- the builder loses nothing."""
    scope = _scope(row["scope"])
    key = _resolve_key(row["key"], scope, row["kind"])

    uri = build_uri(scope, row["kind"], row["namespace"], key)
    parsed = parse_uri(uri)

    assert parsed.key == tuple(_nfc(segment) for segment in key), row["why"]
    assert parsed.namespace == (None if row["namespace"] is None else _nfc(row["namespace"])), row[
        "why"
    ]
    assert parsed.kind == row["kind"], row["why"]
    assert (parsed.firm, parsed.engagement, parsed.system, parsed.environment) == tuple(
        row["scope"]
    ), row["why"]


@pytest.mark.property
@pytest.mark.parametrize("row", ACCEPTED, ids=[row["case"] for row in ACCEPTED])
def test_uri_roundtrip_build_of_parse_is_identity_over_the_corpus(row: dict[str, Any]) -> None:
    """`build(parse(s)) == s` for canonical `s` -- the parser invents nothing."""
    scope = _scope(row["scope"])
    key = _resolve_key(row["key"], scope, row["kind"])

    uri = build_uri(scope, row["kind"], row["namespace"], key)
    parsed = parse_uri(uri)
    rebuilt = build_uri(_scope(list(row["scope"])), parsed.kind, parsed.namespace, parsed.key)

    assert rebuilt == uri, row["why"]
    validate_uri(rebuilt)


@pytest.mark.property
@pytest.mark.parametrize("row", REJECTED, ids=[row["case"] for row in REJECTED])
def test_the_corpus_rejections_raise_the_code_they_claim(row: dict[str, Any]) -> None:
    """A corpus line that says it is rejected must be rejected, with that code.

    Without this the rejection half of the corpus would be decoration: a builder
    that accepted everything would still pass the two round-trip properties.
    """
    scope = _scope(row["scope"])
    key = (
        _resolve_key(row["key"], scope, row["kind"])
        if row["kind"] in IDENTITY_KINDS
        else tuple(row["key"])
    )

    with pytest.raises(AdoptError) as raised:
        build_uri(scope, row["kind"], row["namespace"], key)

    assert raised.value.code is ErrorCode[row["outcome"]], row["why"]


# --------------------------------------------------------------------------
# Generated inputs. The corpus covers the classes we thought of; hypothesis
# covers the ones we did not.
# --------------------------------------------------------------------------

#: Excludes surrogates (not encodable at all) and control characters, neither of
#: which is a referent name in any source system -- a store that cannot hold
#: them is not the defect this property looks for.
_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")),
    min_size=1,
    max_size=24,
)
_SLUGS = st.sampled_from(["northwind", "acme-erp", "orders-api", "prod", "a1", "x-y-z"])
_SCOPES = st.builds(lambda a, b, c, d: _scope([a, b, c, d]), _SLUGS, _SLUGS, _SLUGS, _SLUGS)


@st.composite
def _buildable(draw: st.DrawFn) -> tuple[Scope, str, str | None, tuple[str, ...]]:
    """Inputs the builder accepts: NFC-stable, not pre-encoded, within budget."""
    scope = draw(_SCOPES)
    kind = draw(st.sampled_from(_KINDS))
    namespace = draw(st.one_of(st.none(), _TEXT))
    key = draw(st.lists(_TEXT, min_size=1, max_size=4).map(tuple))

    def usable(value: str) -> bool:
        return bool(_nfc(value)) and "%" not in _nfc(value)

    assume(namespace is None or (usable(namespace) and _nfc(namespace) != "-"))
    assume(all(usable(segment) for segment in key))
    return scope, kind, namespace, key


@pytest.mark.property
@settings(max_examples=1000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(inputs=_buildable())
def test_uri_roundtrip_holds_for_generated_inputs(
    inputs: tuple[Scope, str, str | None, tuple[str, ...]],
) -> None:
    """Both directions at once, over ≥ 1000 generated referents."""
    scope, kind, namespace, key = inputs
    try:
        uri = build_uri(scope, kind, namespace, key)
    except AdoptError as error:
        # The only refusal a well-formed generated input can earn is the length
        # budget, and it is asserted as a boundary case elsewhere.
        assert error.code is ErrorCode.URI_TOO_LONG
        return

    parsed = parse_uri(uri)

    assert parsed.key == tuple(_nfc(part) for part in key)
    assert parsed.namespace == (None if namespace is None else _nfc(namespace))
    assert isinstance(parsed, IdentityUri)
    assert parsed.render() == uri
    validate_uri(uri)


@pytest.mark.property
@settings(max_examples=200, deadline=None)
@given(inputs=_buildable())
def test_uri_build_is_deterministic_across_repeated_runs(
    inputs: tuple[Scope, str, str | None, tuple[str, ...]],
) -> None:
    """N15: the same referent yields the same URI every time, on every machine.

    Same-process repetition cannot prove machine independence, but it does prove
    the one way it is usually lost -- a set, a dict ordering or a hash seed
    reaching the output.
    """
    scope, kind, namespace, key = inputs
    try:
        first = build_uri(scope, kind, namespace, key)
    except AdoptError:
        return

    assert build_uri(scope, kind, namespace, key) == first
