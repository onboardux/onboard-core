"""What an extractor produces, and the protocol it produces it through.

Three shapes and one rule. The rule: **an extractor is pure over an in-memory
view of the tree and never touches the store.** It yields `Observation`s; the
runner owns every write, in one transaction per pack. That split is what lets an
extractor be tested with a dict of files and no database, and what stops thirty
extractors from each inventing their own idea of when to commit.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from adopt_model._enums import IdentityKind

if TYPE_CHECKING:  # pragma: no cover -- typing only; `tree` imports nothing from here
    from adopt_map.tree import SourceTree

__all__ = ["Extractor", "Observation", "Span"]


@dataclass(frozen=True, slots=True)
class Span:
    """Where in a file an observation was seen. Line numbers are 1-based.

    Rendered as `<path>:<start>-<end>` into `identity_revision.source_ref`
    (decision D3). A single-line observation still renders both bounds, so the
    format has one shape rather than two to parse.
    """

    path: str
    start_line: int
    end_line: int

    def render(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True, slots=True)
class Observation:
    """One referent an extractor saw, ready to become an identity.

    `attributes` is the **digest input** and nothing else goes into it. Whatever
    a pack puts here decides what counts as a semantic change forever after
    (v6.1 §6 H5), so the rule is: attributes are what the referent *is*, never
    where it sits or how it is written. A parameter's name is an attribute; the
    line it starts on is not, which is why `span` is a separate field and why a
    reformatted file cannot move a digest.
    """

    kind: IdentityKind
    #: Local key segments. A single-element sequence is one segment whose
    #: slashes are data (`POST /v1/orders`); several segments are structure
    #: (`billing`, `charges`, `refund`). Build 0's URI grammar, applied.
    key: Sequence[str]
    namespace: str | None
    attributes: Mapping[str, object]
    span: Span
    #: Free-form, never part of the digest, never persisted as an attribute.
    #: Carried so the report can say *why* an extractor thought this was a
    #: referent without that reason changing what a semantic change means.
    note: str | None = field(default=None)


@runtime_checkable
class Extractor(Protocol):
    """`extract(tree) -> Iterator[Observation]`, pure.

    `name` and `version` land on every revision this extractor produces.
    **`version` is load-bearing beyond provenance:** Build 6 compares attribute
    digests only between revisions carrying the same extractor version, so
    changing what an extractor puts in `attributes` without bumping `version`
    would present as a change storm across every identity it has ever seen --
    "we changed how we look" reported as "the system changed".
    """

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def extract(self, tree: "SourceTree") -> Iterator[Observation]: ...
