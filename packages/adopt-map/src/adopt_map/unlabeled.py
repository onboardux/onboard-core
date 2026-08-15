"""The unlabelled bucket -- counted, listed, never guessed. `01` F12.6, `05` S1.6.

Design Appendix B, on packaged platforms: *"the metadata is retrievable but
meaningless alone -- a field called `ZFIELD_003` tells you nothing -- so day-one
competence is genuinely worse until a human does a labelling pass. **The design
says so out loud**, because a confidently wrong answer in week one costs the
account."* This module is where that sentence becomes a data structure.

**What lands in the bucket.** A `metadata_component` whose bundle states no human
label. Nothing else: an endpoint has a path, a symbol has a name, a config key
has a key -- all of them name themselves. A platform component is the one kind
whose API name can be `ZFIELD_003`, and the one kind where an inventory without a
labelling pass is an inventory nobody can read.

**What the bucket structurally cannot do is label anything.** `01` §8's autonomy
matrix puts *"label opaque platform fields"* in the **Human / required /
auto-promotion never** row, and `UnlabeledComponent` has no `label` field and no
`candidate` field for one to arrive in. That is the same argument
`SecretReferenceAttributes` makes by having no `value`, and
`UiComponentAttributes` by having no `selector`: a field that does not exist
cannot be filled in by somebody in a hurry. **S1.7's agentic pass may propose
candidates into its own queue; it may not write one here**, and there is nowhere
for it to write.

**Unlabelled is not opaque** (B1-CR-77). An unlabelled component is fully
readable and keeps a real semantic digest, so a later change to its type still
writes a revision; an *opaque* one is a component the bundle references without
defining, which `01` F8.7 gives a null digest. Both need a human, and only one of
them has nothing to compare.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

__all__ = ["BUCKETED_KIND", "UnlabeledComponent", "unlabeled_components"]

if TYPE_CHECKING:  # pragma: no cover - typing only
    from adopt_map.report import RunResult

#: The one kind whose members can be unreadable to a human. See the docstring.
BUCKETED_KIND: Final[str] = "metadata_component"

#: The attribute a bundle sets when it labels a component. Read from the
#: attribute model's own field name rather than restated as a bare string
#: elsewhere.
_LABEL_ATTRIBUTE: Final[str] = "label"


@dataclass(frozen=True, slots=True)
class UnlabeledComponent:
    """One component awaiting a human label.

    **There is deliberately no `label` field, and no `candidate` field.** This
    record is the *question*; the answer is a human's and arrives as a
    superseding revision through the ordinary write path, never by something
    filling in a blank here (`01` §8).
    """

    uri: str
    namespace: str | None
    api_name: str
    component_type: str | None
    opaque: bool
    source_path: str | None

    def evidence(self) -> str:
        """One line of what the bundle *did* say, for the human doing the pass.

        Everything here was read from the export. Nothing is inferred, which is
        why an opaque component's evidence is honestly thin.
        """
        parts = [part for part in (self.component_type, self.namespace) if part]
        if self.opaque:
            parts.append("referenced but not defined in the bundle")
        return " · ".join(parts) if parts else "no further detail in the bundle"


def unlabeled_components(result: "RunResult") -> tuple[UnlabeledComponent, ...]:
    """Every component this run found that the bundle did not label.

    Ordered by URI, because `result.minted()` already is: one order for the
    artifact, the first screen and the count, so a reader comparing two runs is
    comparing the same list (`01` N4).
    """
    found: list[UnlabeledComponent] = []
    for entry in result.minted():
        fact = entry.fact
        if fact.identity_kind != BUCKETED_KIND:
            continue
        if fact.attributes.get(_LABEL_ATTRIBUTE):
            continue
        api_name = fact.attributes.get("api_name")
        component_type = fact.attributes.get("component_type")
        found.append(
            UnlabeledComponent(
                uri=entry.uri,
                namespace=fact.namespace,
                # The local key is the fallback, not an invention: it is the
                # platform's own API name in the form `02` §3.1 states, and it is
                # what a human will search the platform for.
                api_name=str(api_name) if isinstance(api_name, str) else fact.local_key,
                component_type=str(component_type) if isinstance(component_type, str) else None,
                opaque=fact.opaque,
                source_path=fact.source_refs[0].path if fact.source_refs else None,
            )
        )
    return tuple(found)
