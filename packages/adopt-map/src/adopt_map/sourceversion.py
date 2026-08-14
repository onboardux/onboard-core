"""The `source_version` composite -- contracts §4, implementation spec §5.4.

`03` §5.4 calls the per-kind table this module drives **the highest-value table
in the build**, and the reason is that everything downstream of Build 1 asks one
question of it: *did the semantics change, or only the labels?* Getting a
deterministic answer here is what lets Build 10's cascade answer step 3 **without
a model call**.

Two comparisons live here and they are **not the same comparison**, which is the
single easiest thing to get wrong in this module:

* **`compares_equal` -- idempotence** (`02` §4.3 row 1). Two composites are equal
  when their `sem`, `ren` and `flags` are equal, *including* when both `sem` are
  null. That is what row 6 means by *"write a revision only if other fields
  changed"*: an opaque fact seen twice unchanged is unchanged.
* **`matches_semantically` -- the move rule** (`02` §4.3 rows 4-5). A null `sem`
  **never** matches, not even another null. Two opaque identities are not
  evidence that one moved to the other; declaring them one referent is precisely
  the guess B1-CR-08 forbids.

Collapsing the two into one equality is how *"null never compares equal to null"*
either breaks idempotence for every opaque fact or licenses a fabricated move.

**Three repairs to `02` §4, each recorded rather than worked around.**

*`src` is recorded and excluded from the comparison (B1-CR-43).* §4.3 row 1 says
the *"whole composite"* decides equality and §4.1 makes `src` the tree's VCS
revision. Together those say that **committing anything, anywhere, changes every
fact's composite** -- so the next run writes one revision per identity and F4's
acceptance criterion fails on the second commit rather than on any real change.
`src` is provenance: it records which commit an observation came from, and it
takes no part in deciding whether something changed.

*`sem` gains a null form (B1-CR-42).* §4.1 gave `ren` the null spelling `r-` and
gave `sem` none, while §4.3 row 6 and PRD F8.7 both require a null semantic
digest for an opaque referent. The grammar could not express the case its own
table depends on. `s-` is the repair.

*PRD F4.4's `change_scope='render_only'` names no segment of this grammar
(B1-CR-45).* Contracts outrank the PRD, and §4.3 row 2 already states the real
mechanism: equal `sem` with differing `ren` **is** the render-only signal.
"""

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from adopt_const import MAP_DIGEST_ALGO
from adopt_map.jcs import JcsError, canonicalize
from adopt_map.schemas.attributes import PromptAttributes
from adopt_map.schemas.projections import Projection, projection_for
from adopt_map.schemas.surface import SurfaceFact
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "SOURCE_VERSION_SCHEME",
    "SourceVersion",
    "build_source_version",
    "matches_semantically",
    "parse_source_version",
]

#: `02` §1.3: the composite's scheme prefix is Build 1's and starts at `sv1`.
#: A module-level `Final` rather than an `adopt_const` row, on the precedent
#: `adopt_map.writer.AUDIT_EVENT_TYPE` set in S1.1: it is a format marker owned
#: by the one module that reads and writes it, not a tunable anybody retunes.
SOURCE_VERSION_SCHEME: Final[str] = "sv1"

#: `02` §4.1's null spellings. `s-` is B1-CR-42's repair; `r-` was already there.
_NULL_DIGEST: Final[str] = "-"

#: The two flag letters, **in this order**. §4.1 gives the alphabet and not the
#: sequence, so it is fixed here: an unordered rendering would make two runs over
#: one unchanged fact produce two different composites, which is idempotence
#: failing for a reason nobody would look for.
_FLAG_LETTERS: Final[tuple[tuple[str, str], ...]] = (("outside_vcs", "o"), ("opaque", "q"))

_SEM_RE: Final[re.Pattern[str]] = re.compile(r"^s(?:-|[0-9a-f]{32})$")
_REN_RE: Final[re.Pattern[str]] = re.compile(r"^r(?:-|[0-9a-f]{32})$")
_SRC_RE: Final[re.Pattern[str]] = re.compile(r"^v[0-9a-f]{1,40}$")
_FLAGS_RE: Final[re.Pattern[str]] = re.compile(r"^f[oq]{1,2}$")

#: `MAP_DIGEST_ALGO` -> the constructor that realizes it. A mapping rather than a
#: hard-coded `blake2b` call, so retuning the constant to an algorithm nobody
#: implemented fails loudly at the first digest instead of being ignored.
_DIGESTS: Final[dict[str, Callable[[bytes], str]]] = {
    "blake2b-128": lambda payload: hashlib.blake2b(payload, digest_size=16).hexdigest(),
}

#: Per-field normalization applied **before** a field enters a projection.
#: `02` §4.2 asks for exactly one: a prompt's *"template body with whitespace
#: normalized"*, so that reindenting a prompt is not a semantic change.
_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
_NORMALIZERS: Final[dict[tuple[type, str], Callable[[object], object]]] = {
    (PromptAttributes, "template_body"): lambda value: (
        _WHITESPACE_RE.sub(" ", value).strip() if isinstance(value, str) else value
    ),
}


@dataclass(frozen=True, slots=True)
class SourceVersion:
    """One `sv1:` composite -- contracts §4.1.

    `sem` and `ren` are the 32-hex digests or `None` for their null forms; `src`
    is the VCS revision where one resolved; `flags` is the recorded subset of
    `{"outside_vcs", "opaque"}`.
    """

    sem: str | None
    ren: str | None
    src: str | None = None
    flags: frozenset[str] = frozenset()

    def encode(self) -> str:
        """Render per `02` §4.1. The inverse of `parse_source_version`."""
        parts = [
            f"s{self.sem if self.sem is not None else _NULL_DIGEST}",
            f"r{self.ren if self.ren is not None else _NULL_DIGEST}",
        ]
        if self.src is not None:
            parts.append(f"v{self.src}")
        letters = "".join(letter for name, letter in _FLAG_LETTERS if name in self.flags)
        if letters:
            parts.append(f"f{letters}")
        return f"{SOURCE_VERSION_SCHEME}:" + ".".join(parts)

    def compares_equal(self, other: "SourceVersion | None") -> bool:
        """Whether this observation changed anything -- `02` §4.3 row 1.

        **`src` takes no part** (B1-CR-43): it records which commit an
        observation came from, and including it would make every commit anywhere
        in the tree a change to every fact in it.

        A null `sem` on both sides *is* equal here, and deliberately: row 6 says
        an opaque fact gets a revision only when its other fields changed. The
        move rule's opposite reading lives in `matches_semantically`.
        """
        if other is None:
            return False
        return self.sem == other.sem and self.ren == other.ren and self.flags == other.flags


def matches_semantically(left: SourceVersion, right: SourceVersion) -> bool:
    """Whether two composites describe the same referent -- `02` §4.3 rows 4-5.

    **Null never matches null.** An opaque identity's meaning is not recoverable
    from artifacts, so two of them carry no evidence that one became the other,
    and emitting a move on that basis is the guess B1-CR-08 exists to forbid.
    """
    return left.sem is not None and left.sem == right.sem


def parse_source_version(value: str) -> SourceVersion:
    """Parse a `sv1:` composite.

    Args:
        value: The stored `source_version` string.

    Returns:
        The parsed composite.

    Raises:
        AdoptError: ``MAP_EXTRACTOR_FAILED`` when `value` is not a well-formed
            `sv1:` composite. The segments are told apart by their one-letter
            prefixes rather than by position, because `src` and `flags` are both
            optional and position alone cannot distinguish them.
    """
    scheme, separator, body = value.partition(":")
    if separator != ":" or scheme != SOURCE_VERSION_SCHEME:
        raise _malformed(value, f"the scheme must be {SOURCE_VERSION_SCHEME!r}")

    segments = body.split(".")
    if not 2 <= len(segments) <= 4:
        raise _malformed(value, "expected two to four dot-separated segments")

    sem_text, ren_text, *rest = segments
    if not _SEM_RE.match(sem_text) or not _REN_RE.match(ren_text):
        raise _malformed(value, "the sem and ren segments are malformed")

    src: str | None = None
    flags: set[str] = set()
    for segment in rest:
        if _SRC_RE.match(segment) and src is None:
            src = segment[1:]
        elif _FLAGS_RE.match(segment) and not flags:
            letters = set(segment[1:])
            flags = {name for name, letter in _FLAG_LETTERS if letter in letters}
        else:
            raise _malformed(value, f"{segment!r} is not a valid src or flags segment")

    return SourceVersion(
        sem=None if sem_text[1:] == _NULL_DIGEST else sem_text[1:],
        ren=None if ren_text[1:] == _NULL_DIGEST else ren_text[1:],
        src=src,
        flags=frozenset(flags),
    )


def build_source_version(
    fact: SurfaceFact, attributes: Mapping[str, object], *, vcs_revision: str | None
) -> SourceVersion:
    """Compute the composite for one fact -- contracts §4.1, §4.2.

    Args:
        fact: The extractor's fact. Supplies the flags, the relations and the
            prose; supplies **no** digest, because an extractor that could set
            one could declare a change to be no change.
        attributes: The fact's attributes as validated JSON-shaped values --
            `validated_attributes().model_dump(mode="json")`. Taken validated
            rather than raw so a field the closed model rejects can never reach
            a digest.
        vcs_revision: The tree's commit sha, or `None` when it is not a
            checkout. Recorded as `src`; excluded from every comparison.

    Returns:
        The composite.

    Raises:
        AdoptError: ``MAP_EXTRACTOR_FAILED`` when a projection value is a type
            JCS cannot represent.
    """
    projection = projection_for(fact.identity_kind, fact.namespace)
    model = type(fact.validated_attributes())

    sem = None if fact.opaque else _digest(_semantic_payload(fact, attributes, projection, model))
    ren = (
        _digest(_presentation_payload(fact, attributes, projection, model))
        if (projection.has_presentation)
        else None
    )

    flags = {name for name, _ in _FLAG_LETTERS if getattr(fact, name)}
    return SourceVersion(sem=sem, ren=ren, src=vcs_revision, flags=frozenset(flags))


def _semantic_payload(
    fact: SurfaceFact, attributes: Mapping[str, object], projection: Projection, model: type
) -> dict[str, object]:
    """The semantic projection, plus the relations.

    **`relations` is here and not in the projection table** because it is a
    `SurfaceFact` field rather than an attribute field -- but it has to be in
    *a* digest, and it is structural rather than cosmetic: an endpoint that
    stops being handled by a symbol has changed. Leaving it out would mean a
    relation change writes no revision while the front-matter this build writes
    (`02` §5) carries the new edge, so the stored body and the digest that
    claims to describe it would disagree.

    Relations are **sorted**, because they are a set of observed edges rather
    than a sequence: two extractors seeing the same edges in a different order
    describe the same referent, and `02` §5.2 gives their order no meaning.
    """
    payload = _project(attributes, projection.semantic, model)
    payload["relations"] = sorted(
        [
            relation.predicate,
            relation.target_kind,
            relation.target_namespace or "",
            relation.target_local_key,
        ]
        for relation in fact.relations
    )
    return payload


def _presentation_payload(
    fact: SurfaceFact, attributes: Mapping[str, object], projection: Projection, model: type
) -> dict[str, object]:
    """The presentation projection, plus the prose.

    Prose is the human sentence in `knowledge_revision.body_md` (`02` §5 rule 3),
    so it is presentation by construction -- rewording it is a change to the body
    and not to the referent, which is exactly what §4.3 row 2 writes one revision
    for. A kind with no presentation projection has no `ren` at all (`02` §4.2's
    `r-` for `secret:*`), and prose does not resurrect one.
    """
    payload = _project(attributes, projection.presentation, model)
    payload["prose"] = fact.prose
    return payload


def _project(
    attributes: Mapping[str, object], fields: frozenset[str], model: type
) -> dict[str, object]:
    """The named fields, normalized where `02` §4.2 asks for it."""
    payload: dict[str, object] = {}
    for field in fields:
        value = attributes.get(field)
        normalizer = _NORMALIZERS.get((model, field))
        payload[field] = normalizer(value) if normalizer is not None else value
    return payload


def _digest(payload: Mapping[str, object]) -> str:
    """`MAP_DIGEST_ALGO` over the RFC 8785 canonical form of `payload`."""
    algorithm = _DIGESTS.get(MAP_DIGEST_ALGO)
    if algorithm is None:  # pragma: no cover -- reachable only by retuning the constant
        raise AdoptError(
            ErrorCode.MAP_EXTRACTOR_FAILED,
            message=f"MAP_DIGEST_ALGO is {MAP_DIGEST_ALGO!r}, which this build cannot compute",
            hint=f"Implemented: {sorted(_DIGESTS)}. Retuning the constant to an algorithm "
            "nobody implemented would otherwise change every digest in the store "
            "silently, which is a schema change wearing a tunable's clothes.",
        )
    try:
        return algorithm(canonicalize(dict(payload)))
    except JcsError as exc:
        raise AdoptError(
            ErrorCode.MAP_EXTRACTOR_FAILED,
            message=f"a projection value cannot be canonicalized: {exc}",
            hint="Every attribute value must be a JSON type that RFC 8785 can render. "
            "A value that cannot be canonicalized has no stable digest, so the fact "
            "it belongs to could never be compared against its own next observation.",
        ) from exc


def _malformed(value: str, reason: str) -> AdoptError:
    return AdoptError(
        ErrorCode.MAP_EXTRACTOR_FAILED,
        message=f"{value!r} is not a valid source_version composite: {reason}",
        hint="The grammar is `02` §4.1: sv1:sem.ren[.src][.flags], where sem is "
        "`s-` or `s` plus 32 hex digits, ren is `r-` or `r` plus 32, src is `v` "
        "plus up to 40, and flags is `f` plus one or two of `o` and `q`.",
    )
