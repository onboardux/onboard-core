"""Archetype detection, tier negotiation, the observability boundary.

Implementation spec §4.11, PRD F10, sprint S6.

Four invariants hold across this package and each is a refusal rather than a
preference:

1. **No model call on the deterministic path.** `04` §4 runs the walk to
   completion; below `DETECT_CONFIDENCE_MIN` the answer is *ambiguous with
   ranked scores*, never a guess. There is no call site for a model here.
2. **No code execution in the target tree.** Files are read, at most
   `DETECT_MAX_SNIFF_BYTES` each, and matched against literal substrings.
3. **No network.** Detection is pure filesystem, and
   `tests/property/test_offline.py` measures that rather than asserting it.
4. **Archetype values are exactly `web|platform|lowcode|data|ai`**, read from
   the generated enum rather than retyped.

The tier ladder is **CR-38**, ratified 2026-08-05: three qualification questions
to `T0`-`T4`, with `T0` a decline recommendation and `T3` the floor the `ai`
archetype requires.
"""

from adopt_detect.boundary import (
    DEFAULT_OUTBOUND_CATEGORIES,
    METADATA_ONLY,
    BoundaryView,
    declare_boundary,
)
from adopt_detect.detect import DetectionResult, RuleHit, detect, walk_files
from adopt_detect.negotiate import (
    AI_MINIMUM_TIER,
    QUESTIONS,
    Answers,
    TierDecision,
    negotiate,
    parse_answers,
    unavailable_capabilities,
    violates_archetype_floor,
)
from adopt_detect.records import BoundaryRecords
from adopt_detect.render import render_json, render_markdown
from adopt_detect.rules import ARCHETYPES, ArchetypeRules, Rule, load_rule_sets

__all__ = [
    "AI_MINIMUM_TIER",
    "ARCHETYPES",
    "DEFAULT_OUTBOUND_CATEGORIES",
    "METADATA_ONLY",
    "QUESTIONS",
    "Answers",
    "ArchetypeRules",
    "BoundaryRecords",
    "BoundaryView",
    "DetectionResult",
    "Rule",
    "RuleHit",
    "TierDecision",
    "declare_boundary",
    "detect",
    "load_rule_sets",
    "negotiate",
    "parse_answers",
    "render_json",
    "render_markdown",
    "unavailable_capabilities",
    "violates_archetype_floor",
    "walk_files",
]
