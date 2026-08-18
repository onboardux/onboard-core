"""A `GlueRunner` that answers from a script. No socket, no credential.

**What this can and cannot prove, stated once so no reader has to infer it.** It
can prove the driver reaches every golden case, routes each reply through the
real quarantine pipeline, and produces one score per eval. It can prove **nothing
about any `04` §8 threshold**, because a recorded or scripted fake replays its
script whatever it is sent -- Build 0's CR-51, which cost that build four CI
rounds to learn at a different seam.

So the plumbing test that uses this asserts the *shape* of the result and the two
scores that are properties of the script itself, and `05` S1.7's Final Output
Validation lines 2 and 3 stay unchecked until a real adapter has run.
"""

from typing import Any, Final

from adopt_map.schemas.agent import (
    ExtractorManifest,
    GlueOutput,
    LabelOutput,
    ProseOutput,
    TriageItem,
    TriageOutput,
)

__all__ = ["CLEAN_MODULE", "StubRunner"]

#: A module that names nothing the `04` §6 audit forbids. Audit-clean by
#: construction, which is why the plumbing test may assert E1 == 1.0 about the
#: stub and nothing about a model.
CLEAN_MODULE: Final[str] = '''"""A minimal, audit-clean extractor."""


class Extractor:
    def manifest(self):
        return None

    def applies_to(self, root):
        return False

    def extract(self, ctx):
        return iter(())


EXTRACTOR = Extractor
'''

#: Phrases in a golden task's description that mark it genuinely non-static.
#: The stub declines exactly these, so E5 reads 1.0 for the stub -- a property of
#: this list, not of any model.
_NON_STATIC: Final[tuple[str, ...]] = (
    "runtime",
    "remote state",
    "eval()",
    "JVM",
    "Cloudflare",
)


class StubRunner:
    """Answers each prompt position from a fixed script."""

    adapter = "stub"

    def run(
        self, prompt_ref: str, inputs: dict[str, Any], *, model: type[Any]
    ) -> tuple[Any, float, float]:
        if model is TriageOutput:
            return (
                TriageOutput(
                    families=[
                        TriageItem(
                            family="routes",
                            identity_kinds=["endpoint"],
                            rationale="declared in source",
                            value_rank=9,
                            statically_recoverable=True,
                        )
                    ]
                ),
                0.0,
                0.0,
            )
        if model is GlueOutput:
            return (self._glue(inputs), 0.0, 0.0)
        if model is LabelOutput:
            # An empty candidate list for every field. `04` §4.3 rule 2 makes that
            # the correct answer whenever evidence does not support one, so the
            # stub takes the restrained arm rather than inventing labels a scorer
            # would then grade.
            return (LabelOutput(fields={}), 0.0, 0.0)
        return (ProseOutput(summary=""), 0.0, 0.0)

    def _glue(self, inputs: dict[str, Any]) -> GlueOutput:
        description = str(inputs.get("family_description", ""))
        if any(marker in description for marker in _NON_STATIC):
            return GlueOutput(
                outcome="declined", decline_reason="not recoverable by static analysis"
            )
        return GlueOutput(
            outcome="authored",
            extractor_id="agent.stub.routes",
            module_source=CLEAN_MODULE,
            test_source="def test_nothing() -> None:\n    assert True\n",
            manifest=ExtractorManifest(
                id="agent.stub.routes",
                version="0.1.0",
                pack="common",
                archetypes=["web"],
                kinds=["endpoint"],
                method="regex",
            ),
        )
