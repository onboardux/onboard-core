"""Normalization invariants -- implementation spec §5.3 test focus.

One property, and it retires a category of example rather than a handful:
**normalization is idempotent**. `normalize(normalize(x)) == normalize(x)` for
every input, which is what lets the writer normalize without having to know
whether an extractor already did.

*Fails when* a rule rewrites its own output -- for example if the parameter pass
ran after itself and turned `{id}` into `{{id}}`, or if the trailing-slash rule
stripped one slash per invocation. *Matters because* the pipeline normalizes at
the mint and the move rule re-normalizes when comparing a prior URI to a current
one; a non-idempotent rule makes those two answers differ and every rename then
reads as a delete plus an add. *No other instrument catches it because* the
table-driven cases each call `normalize` exactly once, which is precisely the
call count under which a non-idempotent rule looks correct.
"""

from typing import get_args

import pytest
from adopt_map.minting import normalize_local_key
from hypothesis import given
from hypothesis import strategies as st

from adopt_model._enums import IdentityKind

#: Text that exercises the rules rather than random Unicode: the separators, the
#: five parameter syntaxes, the excluded tail characters and ordinary segments.
_KEY_PIECES = st.sampled_from(
    [
        "/",
        "//",
        "orders",
        "v1",
        ":id",
        "<int:id>",
        "<id>",
        "{id}",
        "[id]",
        "$id",
        "?x=1",
        "#frag",
        ";matrix=2",
        "GET ",
        "post ",
        ".",
        "-",
        "_",
        "é",
        "é",
    ]
)
_KEYS = st.lists(_KEY_PIECES, min_size=1, max_size=8).map("".join).filter(bool)
_KINDS = st.sampled_from(sorted(get_args(IdentityKind)))


@pytest.mark.property
@given(kind=_KINDS, key=_KEYS)
def test_normalization_is_idempotent(kind: str, key: str) -> None:
    once = normalize_local_key(kind, key)
    assert normalize_local_key(kind, once) == once


@pytest.mark.property
@given(kind=_KINDS, key=_KEYS)
def test_normalization_never_introduces_a_percent_escape(kind: str, key: str) -> None:
    """Encoding happens once, at `build_uri()`, and never here (`02` §3.2).

    *Fails when* normalization starts encoding. *Matters because* the builder
    **refuses** pre-encoded input rather than encoding it twice, so a
    normalization step that encoded would make every affected key unmintable --
    and the obvious "fix" would be to relax the builder's refusal, which is the
    check that makes `a%20b` and an encoding of `a b` distinguishable at all.
    """
    assert "%" not in normalize_local_key(kind, key) or "%" in key
