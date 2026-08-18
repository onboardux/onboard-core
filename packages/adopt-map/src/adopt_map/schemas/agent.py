"""The glue pass's output schemas -- `04` §5, closed and validated before disk.

**These are the only shapes a model's reply may take, and `extra="forbid"` is the
enforcement rather than the documentation.** `04` §5's own heading is *"strict,
closed -- validated before anything touches disk"*, and the ordering in that
sentence is the mechanism: a reply is parsed and validated whole, and only a
model that satisfied the schema reaches step 1 of the quarantine pipeline. There
is no streaming path and no partial-application path, because either would put
unvalidated model output on the filesystem.

**`identity_kinds` is `adopt_model`'s closed enum and not a copy of it.** `04`
§4.1's prompt lists the thirteen kinds in its text, which is what a model reads;
this module reuses `IdentityKind`, which is what a validator enforces. A reply
naming a fourteenth kind fails here even though the prompt is what asked for
thirteen -- and it fails for the same reason `02` §7 obligation 4 exists, that a
component quietly widening the kind vocabulary makes coverage arithmetic
uncheckable.

**What `GlueOutput` cannot carry is the point.** There is no `confidence`, no
`uri`, no scope and no path: the module source is *text* until the static audit
has read it, and the framework owns every one of those four. `04` §4.2's hard
constraint 4 says the same thing to the model in prose; this shape says it to the
program.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from adopt_map.schemas.surface import ExtractorManifest
from adopt_model._enums import IdentityKind

__all__ = [
    "AGENT_OUTPUT_MODELS",
    "GlueOutput",
    "LabelCandidate",
    "LabelOutput",
    "ProseOutput",
    "TriageItem",
    "TriageOutput",
]

_CONFIG: Final[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

# Seven field bounds below are waived rather than promoted to `adopt_const`, and
# the distinction is the one `03` §3 draws: a **tunable** is retuned against
# evidence at a ratification gate, while these are `04` §5's **shape** -- changing
# one changes what a reply may contain, which `00` §9 rule 1 routes through `04`
# and the code together. They collide numerically with unrelated tunables
# (`MAP_DIAGRAM_MAX_NODES` is also 300), which is exactly the case the waiver
# exists for, and every waiver prints on every `constants_sync` run.

#: `04` §5's `extractor_id` pattern, and deliberately the same one
#: `ExtractorManifest.id` carries. A generated extractor is registered through
#: the same registry as a shipped one once approved (`04` §6), so an id shape it
#: could not hold would be a defect discovered at approval time rather than at
#: generation time.
EXTRACTOR_ID_PATTERN: Final[str] = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"

# `04` §5's field bounds, hoisted to module scope. They were inline waivers first
# and `ruff format` wrapped one of the `Field(...)` calls, which moved its comment
# onto the following line and took the waiver with it -- so the gate reported a
# bare literal in a file whose author had waived it. A named bound cannot be
# separated from its reason by a reformat.
_RATIONALE_MAX: Final[int] = 400  # const-sync: ok -- `04` §5 shape bound
_RANK_MAX: Final[int] = 10  # const-sync: ok -- `04` §5 shape bound
_FAMILIES_MAX: Final[int] = 10  # const-sync: ok -- `04` §5 shape bound
_NOTES_MAX: Final[int] = 400  # const-sync: ok -- `04` §5 shape bound
_DECLINE_REASON_MAX: Final[int] = 300  # const-sync: ok -- `04` §5 shape bound
_LABEL_MAX: Final[int] = 80  # const-sync: ok -- `04` §5 shape bound
_EVIDENCE_MAX: Final[int] = 300  # const-sync: ok -- `04` §5 shape bound
_SUMMARY_MAX: Final[int] = 400  # const-sync: ok -- `04` §5 shape bound


class TriageItem(BaseModel):
    """One candidate surface family -- `04` §5."""

    model_config = _CONFIG

    family: str
    identity_kinds: list[IdentityKind]
    rationale: str = Field(max_length=_RATIONALE_MAX)
    value_rank: int = Field(ge=1, le=_RANK_MAX)
    statically_recoverable: bool


class TriageOutput(BaseModel):
    """`map-triage-001`'s reply -- `04` §5."""

    model_config = _CONFIG

    families: list[TriageItem] = Field(max_length=_FAMILIES_MAX)
    notes: str | None = Field(default=None, max_length=_NOTES_MAX)


class GlueOutput(BaseModel):
    """`map-glue-001`'s reply -- `04` §5.

    **`declined` is a first-class outcome and not a failure.** `04` §4.2 tells the
    model that declining is *"a correct and valued outcome"* and `04` §8's E5
    gates on the share that declines correctly, so a shape that made `outcome` implicit -- inferring
    decline from an absent `module_source` -- would make the eval unmeasurable and
    the instruction unfalsifiable. It is stated, and the two arms carry different
    fields.
    """

    model_config = _CONFIG

    outcome: Literal["authored", "declined"]
    decline_reason: str | None = Field(default=None, max_length=_DECLINE_REASON_MAX)
    extractor_id: str | None = Field(default=None, pattern=EXTRACTOR_ID_PATTERN)
    module_source: str | None = None
    test_source: str | None = None
    manifest: ExtractorManifest | None = None


class LabelCandidate(BaseModel):
    """One proposed label for one opaque field -- `04` §5."""

    model_config = _CONFIG

    label: str = Field(max_length=_LABEL_MAX)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(max_length=_EVIDENCE_MAX)


class LabelOutput(BaseModel):
    """`map-label-001`'s reply -- `04` §5.

    The `<= 3 candidates` bound is `04` §4.3 rule 1 and is enforced per field
    rather than in total: a reply proposing four labels for one field and none for
    another satisfies any total bound while breaking the rule that matters.
    """

    model_config = _CONFIG

    fields: dict[str, list[LabelCandidate]] = Field(default_factory=dict)

    def over_cap(self, cap: int) -> tuple[str, ...]:
        """Api names proposing more than `cap` candidates, sorted."""
        return tuple(sorted(name for name, items in self.fields.items() if len(items) > cap))


class ProseOutput(BaseModel):
    """`map-prose-001`'s reply -- `04` §5."""

    model_config = _CONFIG

    summary: str = Field(max_length=_SUMMARY_MAX)


#: Prompt id -> the model its reply must satisfy. One mapping, so a caller cannot
#: pair a prompt with the wrong validator and a test can assert every registered
#: prompt has exactly one.
AGENT_OUTPUT_MODELS: Final[dict[str, type[BaseModel]]] = {
    "map-triage-001": TriageOutput,
    "map-glue-001": GlueOutput,
    "map-label-001": LabelOutput,
    "map-prose-001": ProseOutput,
}
