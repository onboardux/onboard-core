"""RFC 8785 JSON Canonicalization Scheme -- the input to both `source_version` digests.

`02` §4.2: *"Both digests are computed over an RFC 8785 JCS canonical form of a
kind-specific attribute projection."* `03` §5.4 invariant 2 says why it has to be
JCS and not merely "sorted keys, no spaces": **the digests must be stable across
platforms and Python versions**, and two of the three ways that can fail are
invisible to a round-trip test that only ever compares our output with itself.

**Implemented here rather than taken as a dependency (B1-CR-50, OD-8).** `03` §2
lists *"Canonical JSON | RFC 8785 JCS implementation | Linked"* without naming a
package. A third-party runtime dependency would change the SBOM derived from the
cross-platform locked-runtime union and the exact release inventory Build 0's
CR-58 pins, and the licence gate treats an undeclared dependency as `in-binary`
and fails closed. The input types here are ours and closed -- what a validated
attribute model can hold -- so the surface being canonicalized is small and
known. `03` §2's row is repaired to say this.

Three things this module gets right that the obvious `json.dumps(sort_keys=True,
separators=(",", ":"))` gets wrong, each of which would produce a digest that
differs between two correct machines:

1. **Numbers are rendered by ECMAScript's `Number::toString`, not Python's
   `repr`.** Both produce the *shortest round-trip* decimal, so they agree on the
   digits; they disagree on when to switch to exponent notation. Python switches
   at 1e16 and ES6 at 1e21, so `1e16` is ``'1e+16'`` in Python and
   ``'10000000000000000'`` in ECMAScript. A digest over the Python form is a
   digest nobody else can reproduce.
2. **Keys sort by UTF-16 code unit, not by code point.** Python's `sorted` is
   code-point order, and the two orders disagree for every character above
   U+FFFF: as UTF-16 those begin with a surrogate in 0xD800-0xDBFF, which sorts
   *below* the U+E000-U+FFFF range that code-point order puts first.
3. **A value JCS cannot represent is refused rather than approximated.** NaN,
   the infinities and an integer outside IEEE-754's exactly-representable range
   raise, because JCS is defined over doubles and silently rounding an id-shaped
   integer is how two different referents acquire one digest.
"""

import math
from typing import Final

__all__ = ["JcsError", "canonicalize", "serialize_number"]

#: Beyond this an integer is not exactly representable as an IEEE-754 double, so
#: JCS -- which is defined over doubles -- cannot round-trip it. Refused rather
#: than rounded: see the module docstring.
_MAX_EXACT_INT: Final[int] = 2**53

#: `JSON.stringify`'s short escapes. Every other character below U+0020 takes the
#: `\u00xx` form; everything at or above it is emitted literally, including
#: non-ASCII, because JCS output is UTF-8 and escaping it would be a second
#: spelling of the same string.
#:
#: The keys are **Unicode code points fixed by RFC 8785 §3.2.2.2**, not tunables:
#: `0x08` is backspace because Unicode says so, and it would still be backspace
#: if every constant in `adopt_const` were retuned tomorrow. `constants_sync`
#: reads them as bare integers and correctly cannot tell -- which is what the
#: inline waiver is for, and every waiver prints on every run.
_SHORT_ESCAPES: Final[dict[int, str]] = {
    0x08: "\\b",  # const-sync: ok -- U+0008 BACKSPACE, an RFC 8785 code point
    0x09: "\\t",
    0x0A: "\\n",  # const-sync: ok -- U+000A LINE FEED, an RFC 8785 code point
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


class JcsError(ValueError):
    """A value JCS cannot represent.

    A `ValueError` rather than an `AdoptError`, on the precedent
    `adopt_store.revisions.UnknownFamilyError` set: this is a programming error
    about a value's type, not a runtime condition an operator can act on, and
    giving it a registry code would put a second meaning on a code that has one.
    The caller that can act on it -- the writer -- catches it and raises
    ``MAP_EXTRACTOR_FAILED``, which is the code for "the extractor emitted
    something the schema does not admit".
    """


def serialize_number(value: float | int) -> str:
    """One number, per ECMAScript `Number::toString` (RFC 8785 §3.2.2.3).

    Args:
        value: A finite number. `bool` is rejected by the caller before it
            reaches here, because `bool` is a subclass of `int` in Python and
            `True` would otherwise serialize as `1`.

    Returns:
        The canonical rendering.

    Raises:
        JcsError: `value` is NaN, infinite, or an integer outside the
            exactly-representable range.
    """
    if isinstance(value, int):
        if -_MAX_EXACT_INT <= value <= _MAX_EXACT_INT:
            # Inside the exact range the ES6 rendering of the double *is* the
            # decimal integer, so there is nothing to convert and no float to
            # round-trip through.
            return str(value)
        raise JcsError(
            f"the integer {value} is outside IEEE-754's exactly-representable range "
            f"(±2^53), and JCS is defined over doubles. Canonicalizing it would round "
            "it, and two referents whose ids differ only past the 53rd bit would then "
            "share one digest."
        )

    if math.isnan(value) or math.isinf(value):
        raise JcsError(
            f"{value!r} has no JSON representation (RFC 8785 §3.2.2.3). A projection "
            "field holding one is an extractor defect, not a value to canonicalize."
        )
    if value == 0.0:
        # Covers -0.0, which RFC 8785 renders as "0": the two are equal as
        # numbers, and a digest that distinguished them would make the sign of a
        # zero a semantic change.
        return "0"
    if value < 0:
        return "-" + serialize_number(-value)

    digits, point = _shortest_digits(value)
    return _render(digits, point)


def _shortest_digits(value: float) -> tuple[str, int]:
    """Split a positive float into its shortest digits and a decimal exponent.

    Returns `(digits, point)` such that the value is ``0.<digits> * 10**point``.
    Python's `repr` is the shortest round-trip decimal -- the same digits ES6
    computes -- so only the *placement* of the point has to be recovered here.
    The two implementations disagree about how to spell that placement, and
    `_render` is where the ES6 spelling is applied.
    """
    text = repr(value)
    mantissa, _, exponent_text = text.partition("e")
    exponent = int(exponent_text) if exponent_text else 0

    integer_part, _, fraction_part = mantissa.partition(".")
    raw = integer_part + fraction_part
    stripped = raw.lstrip("0")
    leading_zeros = len(raw) - len(stripped)

    digits = stripped.rstrip("0") or "0"
    point = len(integer_part) + exponent - leading_zeros
    return digits, point


def _render(digits: str, point: int) -> str:
    """The ECMAScript `Number::toString` case analysis, verbatim.

    The four branches are the specification's, in its order. The thresholds --
    21 above and -6 below -- are ECMAScript's own and are the whole reason this
    function exists rather than a call to `repr`.
    """
    count = len(digits)
    if count <= point <= 21:
        return digits + "0" * (point - count)
    if 0 < point <= 21:
        return digits[:point] + "." + digits[point:]
    if -6 < point <= 0:
        return "0." + "0" * -point + digits

    exponent = point - 1
    sign = "+" if exponent >= 0 else "-"
    lead = digits if count == 1 else digits[0] + "." + digits[1:]
    return f"{lead}e{sign}{abs(exponent)}"


def _serialize_string(value: str) -> str:
    """One string, per `JSON.stringify` (RFC 8785 §3.2.2.2)."""
    out = ['"']
    for character in value:
        code = ord(character)
        escape = _SHORT_ESCAPES.get(code)
        if escape is not None:
            out.append(escape)
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(character)
    out.append('"')
    return "".join(out)


def _sort_key(key: str) -> bytes:
    """UTF-16 code units, big-endian -- RFC 8785 §3.2.3.

    Comparing the big-endian UTF-16 bytes lexicographically **is** comparing the
    code-unit sequence, because every unit is two bytes and the high byte leads.
    Python's own `sorted` compares code points, which orders U+E000-U+FFFF before
    every astral character; UTF-16 orders the astral ones first, by their leading
    surrogate. Sorting the wrong way is invisible until a key carries an emoji.
    """
    return key.encode("utf-16-be", errors="surrogatepass")


def _serialize(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        # Before `int`: `bool` is a subclass of it, and `True` would otherwise
        # canonicalize as `1`, making a boolean flag and a count indistinguishable.
        return "true" if value else "false"
    if isinstance(value, str):
        return _serialize_string(value)
    if isinstance(value, int | float):
        return serialize_number(value)
    if isinstance(value, list | tuple):
        # Array order is data, never sorted: `status_codes: [200, 404]` and
        # `[404, 200]` are two different declarations and `02` §4.2 puts the
        # field in the semantic projection precisely so the difference lands.
        return "[" + ",".join(_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda pair: _sort_key(str(pair[0])))
        return (
            "{" + ",".join(f"{_serialize_string(str(k))}:{_serialize(v)}" for k, v in items) + "}"
        )
    raise JcsError(
        f"{type(value).__name__} has no JCS representation. The projection may hold "
        "only the JSON types: object, array, string, number, boolean and null."
    )


def canonicalize(value: object) -> bytes:
    """The RFC 8785 canonical form of `value`, as UTF-8 bytes.

    Bytes rather than `str` because the only caller hands the result straight to
    a digest, and a `str` would make the encoding a decision at the call site --
    which is one more place for two machines to differ.

    Args:
        value: A JSON-shaped value: `dict`, `list`, `str`, `int`, `float`,
            `bool` or `None`, nested arbitrarily.

    Returns:
        The canonical UTF-8 encoding.

    Raises:
        JcsError: any value JCS cannot represent -- a non-JSON type, NaN, an
            infinity, or an integer outside ±2^53.
    """
    return _serialize(value).encode("utf-8")
