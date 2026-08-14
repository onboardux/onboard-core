"""Confidence bands and the degrade ladder -- `01` F9, `03` §3, §5.8.

*Defect sentence.* Fails when a method's band drifts from `03` §3, when the ladder
stops terminating in decline, or when a transition goes unrecorded; matters
because `01` F9.4 requires every degradation to appear in three places and a
degradation nobody recorded is a family a client believes was covered; no other
instrument catches it because a degraded run produces *facts*, just at a
confidence nobody was told about.
"""

import pytest
from adopt_map.confidence import (
    LADDER,
    Degradation,
    LadderPolicy,
    confidence_for,
    emits,
    with_counts,
)

from adopt_const import (
    MAP_CONF_AGENT_REVIEWED,
    MAP_CONF_CTAGS,
    MAP_CONF_DECLARED,
    MAP_CONF_GRAMMAR,
    MAP_CONF_REFLECTION,
    MAP_CONF_REGEX,
    MAP_MIN_EMIT_CONFIDENCE,
)

pytestmark = pytest.mark.unit

#: `01` F9.1's table, read from the constants module rather than restated. A test
#: carrying its own numbers would pass a retune the code did not receive.
_BANDS = [
    ("grammar", MAP_CONF_GRAMMAR),
    ("reflection", MAP_CONF_REFLECTION),
    ("declared", MAP_CONF_DECLARED),
    ("ctags", MAP_CONF_CTAGS),
    ("regex", MAP_CONF_REGEX),
    ("agent", MAP_CONF_AGENT_REVIEWED),
]


@pytest.mark.parametrize(("method", "band"), _BANDS)
def test_the_framework_assigns_each_methods_band(method: str, band: float) -> None:
    """`01` F9.1 and `02` §7 obligation 5: the framework assigns, not the extractor."""
    assert confidence_for(method) == band  # type: ignore[arg-type]


def test_every_shipped_method_clears_the_emit_floor_except_none() -> None:
    """`01` F9.3's floor, asserted against the bands rather than assumed.

    `MAP_CONF_REGEX` (0.45) clears `MAP_MIN_EMIT_CONFIDENCE` (0.40) by 0.05. That
    gap is narrow on purpose and it is worth an assertion: one retune of either
    constant turns the whole regex rung into gaps, and this is where that shows
    up rather than in a client's coverage ratio.
    """
    for method, band in _BANDS:
        assert emits(method) is (band >= MAP_MIN_EMIT_CONFIDENCE)  # type: ignore[arg-type]
    assert MAP_CONF_REGEX >= MAP_MIN_EMIT_CONFIDENCE


def test_the_ladder_is_grammar_then_ctags_then_regex() -> None:
    """`01` F9.2, *strictly*. Order is the rule, not an implementation detail."""
    assert LADDER == ("grammar", "ctags", "regex")


def _policy(available: set[str]) -> LadderPolicy:
    return LadderPolicy(lambda method, language: method in available)


def test_the_top_rung_is_taken_with_no_transitions_recorded() -> None:
    """An undegraded family records nothing.

    A ladder that logged a transition on every resolution would put a
    "degradation" on the first screen of a run that degraded nothing, and the
    first screen would stop meaning anything.
    """
    outcome = _policy({"grammar", "ctags", "regex"}).resolve("symbol", "python")
    assert outcome.method == "grammar"
    assert outcome.transitions == ()
    assert outcome.declined is False


def test_a_missing_grammar_degrades_to_ctags_and_records_the_reason() -> None:
    outcome = _policy({"ctags", "regex"}).resolve("symbol", "kotlin")
    assert outcome.method == "ctags"
    assert outcome.confidence == MAP_CONF_CTAGS
    assert [(t.from_method, t.to_method, t.reason) for t in outcome.transitions] == [
        ("grammar", "ctags", "grammar_unavailable")
    ]


def test_the_tool_absent_arm_degrades_past_ctags_to_regex() -> None:
    """The arm `05` S1.3 names explicitly, and the common case on a laptop."""
    outcome = _policy({"regex"}).resolve("symbol", "kotlin")
    assert outcome.method == "regex"
    reasons = [t.reason for t in outcome.transitions]
    assert reasons == ["grammar_unavailable", "tool_unavailable"]


def test_the_ladder_terminates_in_decline_and_never_in_a_guess() -> None:
    """`01` §1.6: silence beats guessing.

    The decline is a **first-class outcome with its own recorded transition**, not
    an empty result: a caller treating `None` as "nothing to do" would drop the
    gap the decline exists to produce.
    """
    outcome = _policy(set()).resolve("symbol", "kotlin")
    assert outcome.method is None
    assert outcome.declined is True
    assert outcome.confidence is None
    assert outcome.transitions[-1].to_method is None
    assert outcome.transitions[-1].reason == "no_method_available"


def test_a_degradation_renders_the_02_field_names_not_the_dataclasss() -> None:
    """`from` and `to` are Python keywords; the rename happens once, at the edge."""
    row = Degradation(
        kind="symbol",
        language="kotlin",
        from_method="grammar",
        to_method="regex",
        reason="grammar_unavailable",
        affected=142,
    ).as_report_row()
    assert row["from"] == "grammar"
    assert row["to"] == "regex"
    assert row["affected"] == 142
    assert "from_method" not in row


def test_a_declining_headline_says_gaps_and_a_degrading_one_says_confidence() -> None:
    """The first screen has to distinguish *"we read it worse"* from *"we could
    not read it"*, because they lead a reader to different actions."""
    degraded = Degradation(
        kind="symbol",
        language="kotlin",
        from_method="grammar",
        to_method="regex",
        reason="grammar_unavailable",
        affected=12,
    ).headline()
    declined = Degradation(
        kind="symbol",
        language="kotlin",
        from_method="regex",
        to_method=None,
        reason="no_method_available",
        affected=12,
    ).headline()
    assert "regex" in degraded and f"{MAP_CONF_REGEX:.2f}" in degraded
    assert "declined" in declined and "gaps" in declined


def test_counts_are_stamped_on_afterwards_without_mutating_a_transition() -> None:
    """The ladder resolves before extraction; the count is only known after.

    Joined rather than mutated, because a mutable degradation is one a later
    stage could quietly re-point at a different family.
    """
    original = Degradation(
        kind="symbol",
        language="go",
        from_method="grammar",
        to_method="regex",
        reason="grammar_unavailable",
    )
    stamped = with_counts([original], {("symbol", "go"): 7})
    assert stamped[0].affected == 7
    assert original.affected == 0


def test_a_language_with_no_declarations_never_reports_a_degradation() -> None:
    """A README is not a family the ladder can degrade.

    Found by reading the first screen of a real run, which said *"symbol/markdown:
    grammar_unavailable"* -- true, meaningless, and on the honest headline. A
    degradations section a reader learns to skip is worse than none, which is the
    whole argument behind `02` §9.1's ordering rule.
    """
    from adopt_map.fileindex import is_code

    assert is_code("python") is True
    assert is_code("typescript") is True
    assert is_code("markdown") is False
    assert is_code("json") is False
    assert is_code("dotenv") is False
    assert is_code(None) is False
