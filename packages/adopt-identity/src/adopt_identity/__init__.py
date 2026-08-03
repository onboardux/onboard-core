"""The identity URI: build, parse, validate (implementation spec §4.6).

One referent, one canonical name, built from immutable scope slugs and never
from a ULID -- which is what keeps an exported bundle resolvable after the store
leaves our hands. The grammar, its nine normative rules and seven worked
examples are contracts §4.

Invariants this package holds:

* **Deterministic** across runs and machines -- no clock, no randomness, no I/O.
* **NFC, then one pass of percent-encoding.** Double-encoded input is rejected,
  never normalized away.
* **Byte-exact comparison, no case folding.** Source identifiers are
  case-sensitive; folding them merges distinct referents.
* **A URI is never rewritten in place.** A move appends an `identity_revision`
  with `status='moved'` and `alias_of_identity_id`; the old URI stands.
"""

from adopt_identity.uri import (
    EMPTY_NAMESPACE,
    IDENTITY_KINDS,
    SCHEME_SEPARATOR,
    SEGMENT_SEPARATOR,
    IdentityUri,
    build_uri,
    parse_uri,
    validate_uri,
)

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
