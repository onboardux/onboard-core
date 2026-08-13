"""`SurfaceFact`, `FactRelation`, `SourceRef` -- contracts §7.

**What is absent here is the design.** A `SurfaceFact` carries no `uri`, no
`confidence`, no `source_version` and no scope field. All four are framework-owned,
and that is exactly why a fuzzed extractor cannot emit a production URI from a
staging run (PRD F6.1-F6.2): the environment segment comes from `ResolvedScope`
and an extractor has no field through which to influence it. Environment
isolation is *structural* rather than procedural, and the absence of four fields
is the whole mechanism.

The same argument covers `confidence`: the framework assigns it from the
extractor's declared `method` (`02` §7 obligation 5). An extractor that could
set its own confidence could promote a regex guess to grammar-level certainty,
and the degrade ladder would then be advisory.
"""

from collections.abc import Iterator
from typing import Final, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from adopt_map.schemas.attributes import validate_attributes
from adopt_map.schemas.relations import RelationPredicate
from adopt_model._enums import Archetype, IdentityKind, SourceType

__all__ = [
    "EvidenceMethod",
    "Extractor",
    "ExtractorManifest",
    "FactRelation",
    "SourceRef",
    "SurfaceFact",
]

_CONFIG: Final[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

#: How a fact was recovered. The framework maps this to a confidence band
#: (`MAP_CONF_*`); `agent` is `02` §5's front-matter vocabulary and reaches the
#: deterministic pass only after a human approves the extractor (04 §6).
EvidenceMethod = Literal["grammar", "reflection", "declared", "ctags", "regex", "agent"]


class SourceRef(BaseModel):
    """Where a fact was observed -- one `provenance` row's worth.

    `path` is **repo-relative** and stays that way: `02` §6 forbids an absolute
    path in `provenance`, and `03` §5.9 strips them at emission. Holding a
    relative path from the start means there is nothing to strip and nothing to
    forget to strip.
    """

    model_config = _CONFIG

    path: str
    start_line: int | None = None
    end_line: int | None = None
    blob_sha: str | None = None
    source_type: SourceType | None = None


class FactRelation(BaseModel):
    """A directional edge to another referent.

    `target_local_key` and `target_kind` rather than a URI, because **an
    extractor never constructs a URI** (`02` §7 obligation 6). The writer mints
    the target's URI from the same scope it mints everything else from, which is
    what keeps a relation target inside the run's environment by construction.
    """

    model_config = _CONFIG

    predicate: RelationPredicate
    target_kind: IdentityKind
    target_namespace: str | None = None
    target_local_key: str


class SurfaceFact(BaseModel):
    """One observed referent, as an extractor reports it -- contracts §7.

    Note what is **absent**: `uri`, `confidence`, `source_version`, and any scope
    field. See the module docstring.
    """

    model_config = _CONFIG

    identity_kind: IdentityKind
    namespace: str | None = None
    local_key: str = Field(min_length=1)
    title: str
    attributes: dict[str, object] = Field(default_factory=dict)
    relations: list[FactRelation] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    outside_vcs: bool = False
    opaque: bool = False
    prose: str | None = None

    def validated_attributes(self) -> BaseModel:
        """Validate `attributes` against this kind's closed model (`02` §5.1).

        Deliberately a method rather than a validator on the field: the model is
        selected by `(identity_kind, namespace)`, and a field validator cannot
        see a sibling field's value at the time the field is validated.

        Raises:
            AdoptError: ``MAP_EXTRACTOR_FAILED`` on an unknown key or kind.
        """
        return validate_attributes(self.identity_kind, self.namespace, self.attributes)


class ExtractorManifest(BaseModel):
    """What an extractor declares about itself -- contracts §7.

    `kinds` is enforced at runtime, not merely declared: emitting a kind outside
    this list is a hard error (obligation 4), because an extractor that quietly
    widens its own vocabulary is one whose coverage arithmetic nobody can check.
    """

    model_config = _CONFIG

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: str
    pack: Literal["common", "web", "ai", "data", "lowcode", "platform"]
    archetypes: list[Archetype] = Field(default_factory=list)
    kinds: list[IdentityKind] = Field(min_length=1)
    method: EvidenceMethod
    heavy: bool = False
    requires_capability: list[str] = Field(default_factory=list)
    fallback: str | None = None
    determinism: Literal["strict"] = "strict"


@runtime_checkable
class Extractor(Protocol):
    """`manifest()`, `applies_to()`, `extract()` -- contracts §7.

    **This protocol lives in `adopt_map`, and `02` §7 says Build 0 owns it.**
    Build 0 does not: no `Extractor` and no `ExtractorManifest` exist anywhere in
    `packages/`, which is B1-CR-22's finding one document later. It is here for
    exactly B1-CR-28's reason -- no Build 0 code reads a `SurfaceFact` and no
    Build 0 code runs an extractor -- and the divergence is recorded as B1-CR-37
    rather than left in a commit message. Build 8's probe runner is a separate,
    heavier mechanism (`00` §7 X-5) and is not this.

    Sprint S1.1 needs only enough of the protocol to prove the write path against
    `common.stub`. The framework that registers, audits, schedules and budgets
    extractors is S1.3's, and `ctx` gains its budget and file index there.
    """

    def manifest(self) -> ExtractorManifest:
        """This extractor's declaration. Pure; called before anything runs."""
        ...

    def applies_to(self, root: str) -> bool:
        """Whether this extractor has anything to say about the tree at `root`.

        Reads the tree. **Never imports or executes anything in it** -- `02` §7
        obligation 1, proven by the `poisoned-import` fixture in S1.3.
        """
        ...

    def extract(self, root: str) -> Iterator[SurfaceFact]:
        """Yield facts one at a time.

        An iterator rather than a list so that staged emission and the budget
        watchdog can act between facts rather than after all of them (`02` §7
        obligation 7).
        """
        ...
