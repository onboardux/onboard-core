"""The surface attribute front-matter -- contracts §5.

Structured attributes travel in `knowledge_revision.body_md` as an
`attrs_version: 1` YAML block followed by human-readable prose. **No schema
change**: the column already exists, the block is parseable, and it survives
Build 0's JSON export unchanged, which is what makes PRD Q3's default
(*"front-matter, no schema change"*) worth taking rather than asking Build 0 for
a column.

Three rules are structural here rather than remembered:

1. **The block is re-validated on the way out and on the way in.** `02` §5.1
   rule 1 rejects an unknown attribute key at emit time; this module also
   rejects one at parse time, because a body written by a future version and
   read by this one is exactly the case where a silent widening would let an
   undeclared field through.
2. **Relation targets are identity URIs, never database ids** (`02` §5.1 rule 2).
   They arrive already minted -- `build_uri()` is the only construction site in
   the codebase (`03` §5.3 invariant 1) and this module is not it. That is also
   what keeps a relation target inside the run's environment: it was minted from
   the same `ResolvedScope` as everything else.
3. **The rendering is deterministic.** Attribute keys and relations are sorted
   before they are dumped, and the top-level keys keep `02` §5's documented
   order. A body whose bytes depend on a dict's construction order is a body
   that differs between two runs over one unchanged tree.

**Prose is optional and an empty prose block is valid** (`02` §5 rule 3), and
preferable to invention -- which is the whole degrade-ladder argument applied to
a sentence.
"""

from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from adopt_const import SURFACE_ATTRS_VERSION
from adopt_map.schemas.attributes import validate_attributes
from adopt_map.schemas.relations import RelationPredicate
from adopt_map.schemas.surface import EvidenceMethod
from adopt_model._enums import IdentityKind
from adopt_obs import AdoptError, ErrorCode

__all__ = ["FrontMatter", "RenderedRelation", "SurfaceBody", "parse_body", "render_body"]

#: The fence `02` §5's example uses, on its own line, top and bottom.
_FENCE: Final[str] = "---"


class RenderedRelation(BaseModel):
    """One edge, with its target already minted -- `02` §5's `relations` list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    predicate: RelationPredicate
    target: str


class FrontMatter(BaseModel):
    """The `attrs_version: 1` block -- contracts §5, verbatim.

    `extra="forbid"` for `02` §1.1's reason: strict-closed is an egress
    allowlist, not a nicety. A field that does not exist here cannot reach the
    store, an artifact or a log line.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    attrs_version: int = Field(default=SURFACE_ATTRS_VERSION)
    identity_uri: str
    identity_kind: IdentityKind
    method: EvidenceMethod
    confidence: float
    outside_vcs: bool = False
    opaque: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)
    relations: list[RenderedRelation] = Field(default_factory=list)


class SurfaceBody(BaseModel):
    """A parsed `body_md`: its block and its prose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    front_matter: FrontMatter
    prose: str


def render_body(front_matter: FrontMatter, prose: str | None) -> str:
    """Render one `knowledge_revision.body_md` -- contracts §5.

    Args:
        front_matter: The validated block. Its `attributes` are re-validated
            against the kind's closed model here, so a caller that assembled
            them by hand cannot bypass the allowlist.
        prose: The human sentence, or `None`. An empty block is valid.

    Returns:
        The fenced block followed by the prose.

    Raises:
        AdoptError: ``MAP_EXTRACTOR_FAILED`` when the attributes do not validate
            against the kind's closed model.
    """
    validate_attributes(
        front_matter.identity_kind, _namespace_of(front_matter), front_matter.attributes
    )

    block: dict[str, Any] = {
        "attrs_version": front_matter.attrs_version,
        "identity_uri": front_matter.identity_uri,
        "identity_kind": front_matter.identity_kind,
        "method": front_matter.method,
        "confidence": front_matter.confidence,
        "outside_vcs": front_matter.outside_vcs,
        "opaque": front_matter.opaque,
        # Sorted, so the bytes are a function of the content and not of the
        # order an extractor's parser happened to walk its source.
        "attributes": {
            key: front_matter.attributes[key] for key in sorted(front_matter.attributes)
        },
        "relations": [
            {"predicate": relation.predicate, "target": relation.target}
            for relation in sorted(
                front_matter.relations, key=lambda item: (item.predicate, item.target)
            )
        ],
    }
    rendered = yaml.safe_dump(block, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return f"{_FENCE}\n{rendered}{_FENCE}\n\n{prose or ''}".rstrip() + "\n"


def parse_body(body_md: str) -> SurfaceBody:
    """Parse a `body_md` back into its block and its prose.

    Args:
        body_md: A rendered body.

    Returns:
        The parsed block and the prose that followed it.

    Raises:
        AdoptError: ``MAP_EXTRACTOR_FAILED`` when the body carries no block, the
            block is not a mapping, or it carries a key the closed model does
            not admit.
    """
    lines = body_md.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise _malformed("the body does not open with a `---` fence")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == _FENCE)
    except StopIteration:
        raise _malformed("the front-matter block is not closed by a `---` fence") from None

    loaded = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(loaded, dict):
        raise _malformed("the front-matter block is not a mapping")

    try:
        front_matter = FrontMatter.model_validate(loaded)
    except ValidationError as exc:
        raise _malformed(
            f"{exc.error_count()} invalid field(s): {exc.errors(include_url=False)}"
        ) from exc

    validate_attributes(
        front_matter.identity_kind, _namespace_of(front_matter), front_matter.attributes
    )
    return SurfaceBody(front_matter=front_matter, prose="\n".join(lines[end + 1 :]).strip())


def _namespace_of(front_matter: FrontMatter) -> str | None:
    """The namespace, recovered from the URI so the `secret:*` model is selected.

    `02` §5.1 rule 4 gives a `config_key` under a `secret:*` namespace a model
    with **no value field**, and the namespace is what selects it. Recovering it
    from `identity_uri` rather than carrying it as a second field means the block
    cannot claim one namespace while its URI says another.
    """
    from adopt_identity import parse_uri

    return parse_uri(front_matter.identity_uri).namespace


def _malformed(reason: str) -> AdoptError:
    return AdoptError(
        ErrorCode.MAP_EXTRACTOR_FAILED,
        message=f"the surface front-matter is malformed: {reason}",
        hint="The block is `02` §5: a `---`-fenced YAML mapping carrying "
        "`attrs_version`, `identity_uri`, `identity_kind`, `method`, `confidence`, "
        "`outside_vcs`, `opaque`, `attributes` and `relations`, followed by optional "
        "prose. Widening the model to accept a body is a contracts change, not a fix.",
    )
