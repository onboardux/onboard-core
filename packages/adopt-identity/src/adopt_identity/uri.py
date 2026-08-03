"""Build, parse and validate the identity URI (contracts §4).

The URI is the one name a referent has, and it outlives us: it is written into
every exported bundle a client keeps, and CR-06 makes its version legible in the
label so a reader in five years can tell which grammar produced it. Four
decisions here follow from that and are worth stating before the code.

**Slugs, never ULIDs.** Every scope segment is an immutable slug (CR-05). A URI
built from an internal id stops resolving the moment the store leaves our hands,
which is exactly the degraded-mode promise the rebuild exists to keep.

**The scheme label is `URI_SCHEME` and never a literal.** It appears once, at
the top of this module, read from `adopt_const`. Three literals -- builder,
parser, validator -- is what CR-06 was written to prevent.

**Double encoding is refused at the builder, not repaired at the parser.**
Rule 4 says double-encoded input is rejected and never normalized away, and
rule 8 requires `parse(build(x)) == x`. Those two cannot both hold if the check
lives only in the parser: a key whose literal text is ``a%20b`` and a
double-encoding of the key ``a b`` produce *the same string*, so no parser can
tell them apart. So the builder refuses a key that already carries a
percent-triplet, and the parser refuses a segment whose single decode still
yields one. The two rules are exactly complementary -- no accepted URI is
ambiguous, and round-trip is total over everything the builder accepts. The cost
is deliberate: a referent whose literal name contains ``%20`` is unaddressable,
which is a refusal rather than a silent reinterpretation.

**The empty namespace is `None`, and renders `-`.** A literal ``-`` namespace is
refused for the same reason: accepting both spellings of one thing would make
the round-trip lossy in a way no caller could see.

**A key is a *sequence* of segments, not a string.** The grammar says
`key := segment *( "/" segment )` and the worked examples use both shapes: the
`endpoint` key ``POST /v1/orders`` is one segment whose slash is *data* and is
escaped ``%2F``, while the `symbol` key renders as three segments
``billing/charges/refund`` whose slashes are *structure*. A single string cannot
carry that difference -- ``a/b`` would be one segment or two depending on who
was asked -- so the segments are the value and a bare string is taken as one
segment. That is what makes `build(parse(s)) == s` true rather than approximately
true.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, get_args
from urllib.parse import quote, unquote

from adopt_const import URI_MAX_BYTES, URI_SCHEME
from adopt_model._enums import IdentityKind
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope, is_valid_slug

__all__ = [
    "EMPTY_NAMESPACE",
    "IDENTITY_KINDS",
    "SCHEME_SEPARATOR",
    "SEGMENT_SEPARATOR",
    "IdentityUri",
    "build_uri",
    "parse_uri",
    "validate_uri",
]

#: `onboard-v1` + this is the whole prefix. Kept beside the constant it
#: completes rather than inlined at three call sites.
SCHEME_SEPARATOR: Final[str] = "://"
SEGMENT_SEPARATOR: Final[str] = "/"

#: How an absent namespace renders (contracts §4 rule 5). A referent kind that
#: needs no namespace still occupies the segment, so the grammar stays fixed at
#: seven segments and a parser never has to count to decide what it is reading.
EMPTY_NAMESPACE: Final[str] = "-"

#: The declared `identity_kind` vocabulary, read from the generated enum so that
#: adding a kind to the manifest adds it here with no edit. A new kind stays on
#: `onboard-v1` (CR-06): it is a compatible addition.
IDENTITY_KINDS: Final[frozenset[str]] = frozenset(get_args(IdentityKind))

#: Segments are `1*( unreserved / pct-encoded )`. RFC 3986 unreserved is
#: `ALPHA / DIGIT / "-" / "." / "_" / "~"`, and `quote` already leaves exactly
#: those alone when nothing is declared safe -- so `safe=""` *is* the grammar,
#: not an approximation of it.
_NOTHING_IS_SAFE: Final[str] = ""

_PERCENT: Final[str] = "%"
_HEX_DIGITS: Final[frozenset[str]] = frozenset("0123456789abcdefABCDEF")
#: `%` plus two hex digits. RFC 3986's escape length, not a tunable.
# const-sync: ok -- the length of a percent escape, fixed by RFC 3986.
_TRIPLET_LENGTH: Final[int] = 3

#: firm, engagement, system, environment, kind, namespace, then key.
_SCOPE_SEGMENTS: Final[int] = 4
_FIXED_SEGMENTS: Final[int] = 6
_MIN_SEGMENTS: Final[int] = _FIXED_SEGMENTS + 1


def _malformed(message: str, hint: str) -> AdoptError:
    return AdoptError(ErrorCode.URI_MALFORMED, message=message, hint=hint)


def _has_percent_triplet(value: str) -> bool:
    """Whether ``value`` contains a syntactically valid ``%XX`` escape.

    A bare ``%`` that is not followed by two hex digits is not an escape -- an
    identifier ending in ``100%`` is ordinary text and must stay addressable.
    """
    for index, character in enumerate(value):
        if character != _PERCENT:
            continue
        tail = value[index + 1 : index + _TRIPLET_LENGTH]
        if len(tail) == _TRIPLET_LENGTH - 1 and all(digit in _HEX_DIGITS for digit in tail):
            return True
    return False


def _normalize(value: str) -> str:
    """NFC, per contracts §4 rule 6. Case is never folded.

    Source identifiers are case-sensitive; folding them merges two distinct
    referents into one URI, and the merge is undetectable afterwards.
    """
    return unicodedata.normalize("NFC", value)


@dataclass(frozen=True, slots=True)
class IdentityUri:
    """A parsed identity URI: the seven segments, decoded exactly once.

    The value object `adopt_identity` owns (CR-25). The generated models keep
    `uri` as a `str` constrained by the §4 grammar, because `generated-purity`
    lets `adopt_model` import only `pydantic` and `adopt_const` and so it could
    never name this type.
    """

    firm: str
    engagement: str
    system: str
    environment: str
    kind: str
    namespace: str | None
    key: tuple[str, ...]

    @property
    def key_path(self) -> str:
        """The key segments joined for display. Lossy, and only for display.

        Joining is not reversible -- a one-segment key containing a slash and a
        two-segment key render the same -- so nothing that has to round-trip may
        use this.
        """
        return SEGMENT_SEPARATOR.join(self.key)

    def render(self) -> str:
        """Re-render this value as its canonical URI string."""
        return _assemble(
            firm=self.firm,
            engagement=self.engagement,
            system=self.system,
            environment=self.environment,
            kind=self.kind,
            namespace=self.namespace,
            key=self.key,
        )


def _assemble(
    *,
    firm: str,
    engagement: str,
    system: str,
    environment: str,
    kind: str,
    namespace: str | None,
    key: Sequence[str],
) -> str:
    segments = (
        firm,
        engagement,
        system,
        environment,
        kind,
        EMPTY_NAMESPACE if namespace is None else quote(namespace, safe=_NOTHING_IS_SAFE),
        *(quote(segment, safe=_NOTHING_IS_SAFE) for segment in key),
    )
    return f"{URI_SCHEME}{SCHEME_SEPARATOR}{SEGMENT_SEPARATOR.join(segments)}"


def _require_scope(scope: Scope) -> tuple[str, str, str, str]:
    """The four slugs, or `URI_MALFORMED` naming the missing level.

    `environment` is mandatory (contracts §4 rule 5). Omitting it is what
    previously let a production and a staging observation of the same referent
    collide, which is the failure the mandatory segment exists to make
    impossible.
    """
    levels = (
        ("firm", scope.firm),
        ("engagement", scope.engagement),
        ("system", scope.system),
        ("environment", scope.environment),
    )
    missing = [name for name, node in levels if node is None]
    if missing:
        raise _malformed(
            f"the scope resolves to {scope.depth} of {_SCOPE_SEGMENTS} levels; "
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} absent",
            "A URI names one referent in one environment. Resolve the scope to "
            "`firm/engagement/system/environment` before building.",
        )
    slugs = scope.slugs()
    invalid = [slug for slug in slugs if not is_valid_slug(slug)]
    if invalid:
        raise _malformed(
            f"scope slug(s) {', '.join(repr(slug) for slug in invalid)} are not valid slugs",
            "A URI segment for a scope level is a slug, never a ULID -- an id-built "
            "URI stops resolving the moment the store leaves our hands.",
        )
    # const-sync: ok -- positional indexes into the four scope levels.
    return slugs[0], slugs[1], slugs[2], slugs[3]


def build_uri(scope: Scope, kind: str, namespace: str | None, key: str | Sequence[str]) -> str:
    """Build the canonical URI for a referent.

    Args:
        scope: A scope resolved to all four levels. Slugs are used; ids are not.
        kind: A declared `identity_kind` value.
        namespace: The namespace, or `None` where the kind needs none. `None`
            renders as `-`; a literal `-` is rejected as ambiguous.
        key: The referent's local key, unencoded. A bare string is **one**
            segment, so every `/` and `:` in it is data and is escaped. Pass a
            sequence to render a multi-segment key such as
            `symbol/billing/charges/refund`.

    Returns:
        The canonical URI, NFC-normalized and percent-encoded exactly once.

    Raises:
        AdoptError: ``URI_MALFORMED`` when the scope is incomplete, a slug is
            invalid, the kind is not declared, or the namespace or key is empty
            or is a literal `-` namespace. ``URI_DOUBLE_ENCODED`` when the
            namespace or key already carries a percent escape.
            ``URI_TOO_LONG`` above ``URI_MAX_BYTES``.
    """
    firm, engagement, system, environment = _require_scope(scope)

    normalized_kind = _normalize(kind)
    if normalized_kind not in IDENTITY_KINDS:
        raise _malformed(
            f"identity kind {kind!r} is not a declared identity_kind value",
            "Declare the kind in schema/canonical.yaml and regenerate. A new kind is "
            "a compatible addition and stays on this scheme label (CR-06).",
        )

    segments = (key,) if isinstance(key, str) else tuple(key)
    normalized_key = tuple(_normalize(segment) for segment in segments)
    if not normalized_key or any(not segment for segment in normalized_key):
        raise _malformed(
            f"the key {segments!r} is empty or contains an empty segment",
            "Every segment is at least one character. A referent with no key has no "
            "identity to address.",
        )

    normalized_namespace = None if namespace is None else _normalize(namespace)
    if normalized_namespace is not None and not normalized_namespace:
        # Empty string and `None` mean the same thing, and `None` is the one
        # spelling. Accepting both would make `parse(build(x)) == x` false for
        # one of them, silently.
        normalized_namespace = None
    if normalized_namespace == EMPTY_NAMESPACE:
        raise _malformed(
            f"a literal {EMPTY_NAMESPACE!r} namespace is ambiguous with the empty namespace",
            f"{EMPTY_NAMESPACE!r} is how an absent namespace renders. Pass `None` for "
            "no namespace; a namespace that is genuinely the single character "
            f"{EMPTY_NAMESPACE!r} cannot be distinguished from it and is refused rather "
            "than silently reinterpreted.",
        )

    for label, value in (
        ("namespace", (normalized_namespace,) if normalized_namespace else ()),
        ("key", normalized_key),
    ):
        for segment in value:
            if _has_percent_triplet(segment):
                raise AdoptError(
                    ErrorCode.URI_DOUBLE_ENCODED,
                    message=f"the {label} {segment!r} already contains a percent escape",
                    hint="Encoding is applied exactly once, here. Pass the raw value -- a "
                    "pre-encoded one would be encoded again, and the result could never "
                    "be decoded back to what you meant.",
                )

    uri = _assemble(
        firm=firm,
        engagement=engagement,
        system=system,
        environment=environment,
        kind=normalized_kind,
        namespace=normalized_namespace,
        key=normalized_key,
    )
    _require_within_length(uri)
    return uri


def _require_within_length(uri: str) -> None:
    encoded_length = len(uri.encode("utf-8"))
    if encoded_length > URI_MAX_BYTES:
        raise AdoptError(
            ErrorCode.URI_TOO_LONG,
            message=f"the URI is {encoded_length} bytes after encoding, over the "
            f"{URI_MAX_BYTES}-byte maximum",
            hint="Over-length is rejected and never truncated: a truncated URI is a "
            "different referent that looks like the one you asked for.",
        )


def _decode_once(segment: str, *, label: str) -> str:
    """Decode a segment exactly once, refusing one that was encoded twice."""
    decoded = unquote(segment, encoding="utf-8", errors="strict")
    if _has_percent_triplet(decoded):
        raise AdoptError(
            ErrorCode.URI_DOUBLE_ENCODED,
            message=f"the {label} segment {segment!r} decodes to {decoded!r}, "
            "which is itself percent-encoded",
            hint="Encoding is applied exactly once. A double-encoded URI is rejected "
            "rather than normalized, because normalizing it would silently rename "
            "the referent.",
        )
    return decoded


def parse_uri(value: str) -> IdentityUri:
    """Parse a canonical URI into its seven segments.

    Args:
        value: The URI string.

    Returns:
        The parsed `IdentityUri`, decoded exactly once and NFC-normalized.

    Raises:
        AdoptError: ``URI_SCHEME_UNKNOWN`` for any label but ``URI_SCHEME``.
            ``URI_TOO_LONG`` above ``URI_MAX_BYTES``. ``URI_MALFORMED`` for a
            grammar violation. ``URI_DOUBLE_ENCODED`` for a doubly-encoded
            segment.
    """
    _require_within_length(value)

    label, separator, remainder = value.partition(SCHEME_SEPARATOR)
    if not separator:
        raise AdoptError(
            ErrorCode.URI_SCHEME_UNKNOWN,
            message=f"{value!r} carries no scheme label",
            hint=f"Every identity URI begins {URI_SCHEME}{SCHEME_SEPARATOR}. The label "
            "carries the grammar version and is checked before anything else.",
        )
    if label != URI_SCHEME:
        raise AdoptError(
            ErrorCode.URI_SCHEME_UNKNOWN,
            message=f"scheme label {label!r} is not {URI_SCHEME!r}",
            hint="A parser rejects a label it does not know rather than guessing at the "
            "grammar. A label it has never seen may mean anything.",
        )

    segments = remainder.split(SEGMENT_SEPARATOR)
    if len(segments) < _MIN_SEGMENTS or any(not segment for segment in segments):
        raise _malformed(
            f"{value!r} has {len(segments)} segment(s) after the label, or an empty one; "
            f"the grammar requires at least {_MIN_SEGMENTS}, each non-empty",
            "The grammar is firm/engagement/system/environment/kind/namespace/key. A `/` "
            "inside a key is data and must be escaped as %2F.",
        )

    firm, engagement, system, environment, kind, namespace = segments[:_FIXED_SEGMENTS]

    invalid = [slug for slug in (firm, engagement, system, environment) if not is_valid_slug(slug)]
    if invalid:
        raise _malformed(
            f"scope segment(s) {', '.join(repr(slug) for slug in invalid)} are not valid slugs",
            "The four leading segments are immutable scope slugs, not ULIDs.",
        )
    if kind not in IDENTITY_KINDS:
        raise _malformed(
            f"identity kind {kind!r} is not a declared identity_kind value",
            "An unknown kind is refused rather than carried: it would join to nothing "
            "and would export as a value no reader can interpret.",
        )

    # Everything past the namespace is key. Each piece decodes on its own, so a
    # literal `/` inside one segment arrives as %2F and stays inside it.
    key = tuple(_normalize(_decode_once(part, label="key")) for part in segments[_FIXED_SEGMENTS:])
    parsed_namespace = (
        None if namespace == EMPTY_NAMESPACE else _decode_once(namespace, label="namespace")
    )

    return IdentityUri(
        firm=firm,
        engagement=engagement,
        system=system,
        environment=environment,
        kind=kind,
        namespace=None if parsed_namespace is None else _normalize(parsed_namespace),
        key=key,
    )


def validate_uri(value: str) -> None:
    """Raise unless ``value`` is a canonical identity URI.

    Canonical means more than parseable: re-rendering the parsed value must
    reproduce the input byte for byte. A URI that parses but renders differently
    -- an over-encoded segment, an NFD spelling -- would compare unequal to the
    one the builder produces for the same referent, and `identity.uri` is
    `UNIQUE`, so the store would hold the same referent twice.

    Raises:
        AdoptError: any of the four ``URI_*`` codes.
    """
    parsed = parse_uri(value)
    if parsed.render() != value:
        raise _malformed(
            f"{value!r} is not in canonical form; it renders as {parsed.render()!r}",
            "Compare byte-exact after NFC (contracts §4 rule 6). Build the URI with "
            "`build_uri` rather than assembling it by hand.",
        )
