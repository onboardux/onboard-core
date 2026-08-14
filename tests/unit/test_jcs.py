"""RFC 8785 canonicalization -- `02` §4.2, `03` §5.4 invariant 2.

**Every test here exists because our own round-trip would agree with itself.**
The digest this module feeds has to match one computed by a different
implementation on a different platform, so the instrument has to be an external
authority rather than a second call to our own code. The authority is RFC 8785's
published example plus the two places ECMAScript and Python are known to
disagree.

| Behavior | Tier | Defect it catches |
|---|---|---|
| ES6 number rendering, all four branches | **T1** | A digest no other implementation reproduces |
| The Python/ES6 exponent thresholds | **T1** | The same, at exactly the values where `repr` is wrong |
| Key order by UTF-16 code unit | **T1** | A digest that changes when a key gains an astral character |
| RFC 8785's worked example | **T1** | Any of the above, caught against the spec's own bytes |
| Unrepresentable values refused | T2 | A silently rounded integer giving two referents one digest |
"""

import math

import pytest
from adopt_map.jcs import JcsError, canonicalize, serialize_number

pytestmark = pytest.mark.unit


#: `(value, expected)` -- the ECMAScript `Number::toString` cases.
#:
#: The four marked **divergent** are the reason this module exists: Python's
#: `repr` switches to exponent notation at 1e16 and below 1e-4, ECMAScript at
#: 1e21 and below 1e-6. `json.dumps` would emit Python's spelling, and a digest
#: over it is one nobody else can reproduce.
_NUMBERS = [
    pytest.param(0.0, "0", id="zero"),
    pytest.param(-0.0, "0", id="negative-zero-renders-as-zero"),
    pytest.param(1, "1", id="int"),
    pytest.param(-1, "-1", id="negative-int"),
    pytest.param(4.50, "4.5", id="trailing-zero-dropped"),
    pytest.param(-1.5, "-1.5", id="negative-fraction"),
    pytest.param(100.0, "100", id="integral-float-has-no-point"),
    pytest.param(0.002, "0.002", id="leading-zeros"),
    pytest.param(333333333.33333329, "333333333.3333333", id="shortest-round-trip"),
    pytest.param(1e15, "1000000000000000", id="below-python-threshold"),
    pytest.param(1e16, "10000000000000000", id="divergent-python-says-1e+16"),
    pytest.param(1e20, "100000000000000000000", id="divergent-python-says-1e+20"),
    pytest.param(1e21, "1e+21", id="es6-upper-threshold"),
    pytest.param(1e30, "1e+30", id="large-exponent"),
    pytest.param(1e-5, "0.00001", id="divergent-python-says-1e-05"),
    pytest.param(1e-6, "0.000001", id="es6-lower-threshold"),
    pytest.param(1e-7, "1e-7", id="below-es6-lower-threshold"),
    pytest.param(1e-27, "1e-27", id="small-exponent"),
]


@pytest.mark.parametrize(("value", "expected"), _NUMBERS)
def test_number_renders_as_ecmascript_does(value: float, expected: str) -> None:
    assert serialize_number(value) == expected


def test_rfc8785_worked_example() -> None:
    """The specification's own input and output, byte for byte.

    The single highest-value test in the file: it exercises number rendering,
    key ordering, string escaping and literal spelling together, against bytes
    this repository did not choose.
    """
    document = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
        "string": '€$\x0f\nA\'B"\\\\"/',
        "literals": [None, True, False],
    }
    expected = (
        '{"literals":[null,true,false],'
        '"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$' + "\\u000f" + "\\n" + "A'B" + '\\"' + "\\\\\\\\" + '\\"' + '/"}'
    )
    assert canonicalize(document).decode("utf-8") == expected


def test_keys_sort_by_utf16_code_unit_not_code_point() -> None:
    """An astral key sorts **before** U+FFFF, which code-point order reverses.

    Fails when the sort is Python's default; matters because a projection key
    carrying an emoji or a rare CJK character would then digest differently from
    every conforming implementation; no other instrument catches it because
    every key we ship today is ASCII, so the bug is dormant rather than absent.
    """
    canonical = canonicalize({"￿": 1, "\U0001f600": 2, "a": 3}).decode("utf-8")
    assert canonical.index('"a"') < canonical.index("\U0001f600")
    assert canonical.index("\U0001f600") < canonical.index("￿")
    assert sorted(["￿", "\U0001f600"]) == ["￿", "\U0001f600"], (
        "code-point order puts U+FFFF first -- if this ever changes, the test above "
        "has stopped distinguishing the two orders"
    )


def test_control_characters_escape_and_non_ascii_does_not() -> None:
    assert canonicalize("\x00\x1f\t€").decode("utf-8") == '"\\u0000\\u001f\\t€"'


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(math.nan, id="nan"),
        pytest.param(math.inf, id="infinity"),
        pytest.param(-math.inf, id="negative-infinity"),
        pytest.param(2**53 + 1, id="integer-past-exact-range"),
        pytest.param({1, 2}, id="set-is-not-a-json-type"),
    ],
)
def test_unrepresentable_values_are_refused(value: object) -> None:
    """Refused, never approximated.

    Fails when a value JCS cannot hold is silently coerced; matters because a
    rounded integer gives two distinct referents one semantic digest, which
    reads downstream as "nothing changed"; no other instrument catches it
    because the run completes and the digest looks well-formed.
    """
    with pytest.raises(JcsError):
        canonicalize(value)


def test_true_is_not_one() -> None:
    """`bool` is a subclass of `int`; the check order is what keeps them apart."""
    assert canonicalize({"a": True, "b": 1}).decode("utf-8") == '{"a":true,"b":1}'
