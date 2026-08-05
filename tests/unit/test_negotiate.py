"""The tier ladder -- CR-38's truth table, and what it refuses to assume.

*Fails when* an answer combination starts mapping to a different tier, when a
question starts carrying a default, or when the `ai` floor stops being applied.
*Matters because* the tier is persisted on `observability_boundary` and
hard-limits every downstream claim: a tier one step too high is a capability the
engagement was never qualified for, claimed in writing. *No other instrument
catches it because* the boundary tests assert what a decision *renders as*, not
what the answers *mean*, and nothing else exercises the mapping at all.

The table below **is** CR-38. If it and the register row ever disagree, the
register row wins and this file is the defect.
"""

import pytest

from adopt_detect import (
    AI_MINIMUM_TIER,
    QUESTIONS,
    Answers,
    negotiate,
    parse_answers,
    unavailable_capabilities,
    violates_archetype_floor,
)
from adopt_obs import AdoptError, ErrorCode

#: (artifact_access, deploy_signal, safe_interaction) -> tier. All eight
#: combinations, so a change to the ladder cannot pass by being untested.
LADDER: list[tuple[bool, bool, bool, str]] = [
    (False, False, False, "T0"),
    (False, False, True, "T0"),
    (False, True, False, "T0"),
    (False, True, True, "T0"),
    (True, False, False, "T1"),
    (True, True, False, "T2"),
    (True, False, True, "T3"),
    (True, True, True, "T4"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("artifact", "deploy", "interaction", "expected"), LADDER)
def test_the_ladder(artifact: bool, deploy: bool, interaction: bool, expected: str) -> None:
    decision = negotiate(
        Answers(artifact_access=artifact, deploy_signal=deploy, safe_interaction=interaction)
    )
    assert decision.tier == expected


@pytest.mark.unit
def test_no_artifact_access_is_t0_whatever_else_is_true() -> None:
    """The four `T0` rows above, stated as the claim they encode.

    Deploy events and a sandbox against a system whose contents we cannot read
    are notifications about an opaque box. `T0` is "there is no engagement here",
    which is why it is a decline rather than a degraded mode.
    """
    declining = [row for row in LADDER if row[3] == "T0"]
    assert len(declining) == 4
    assert all(not row[0] for row in declining)


@pytest.mark.unit
def test_decline_is_recommended_exactly_at_t0() -> None:
    for artifact, deploy, interaction, expected in LADDER:
        decision = negotiate(
            Answers(artifact_access=artifact, deploy_signal=deploy, safe_interaction=interaction)
        )
        assert decision.decline_recommended is (expected == "T0")


@pytest.mark.unit
def test_a_safe_interaction_outranks_a_deploy_signal() -> None:
    """The one ordering in the ladder that had to be argued rather than assumed.

    A deploy signal says *that* something changed; a safe interaction says *what
    the system now does*. Only the second can refute a claim about behaviour,
    which is what makes T3 the `ai` floor.
    """
    interaction_only = negotiate(
        Answers(artifact_access=True, deploy_signal=False, safe_interaction=True)
    )
    deploy_only = negotiate(
        Answers(artifact_access=True, deploy_signal=True, safe_interaction=False)
    )
    assert interaction_only.tier == "T3"
    assert deploy_only.tier == "T2"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tier", "violated"),
    [("T0", True), ("T1", True), ("T2", True), ("T3", False), ("T4", False)],
)
def test_an_ai_system_below_its_floor_is_a_violation(tier: str, violated: bool) -> None:
    """PRD F10.7. A prompt change with no way to run the system is a change we
    can detect and cannot evaluate."""
    assert violates_archetype_floor("ai", tier) is violated  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("archetype", ["web", "platform", "lowcode", "data"])
def test_no_other_archetype_has_a_floor(archetype: str) -> None:
    assert violates_archetype_floor(archetype, "T1") is False  # type: ignore[arg-type]


@pytest.mark.unit
def test_an_ambiguous_archetype_violates_no_floor() -> None:
    """A floor cannot be applied to a classification that was explicitly declined."""
    assert violates_archetype_floor(None, "T0") is False


@pytest.mark.unit
def test_unavailable_capabilities_shrink_as_the_tier_rises() -> None:
    """*Fails when* a tier stops naming what it does not grant.

    F10.7 requires the capabilities be **named**, and a monotone ladder is the
    only shape in which "this tier is above that one" and "this tier grants more"
    are the same statement.
    """
    counts = [len(unavailable_capabilities(tier)) for tier in ("T0", "T1", "T2", "T3", "T4")]  # type: ignore[arg-type]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] > 0
    assert counts[-1] == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("document", "reason"),
    [
        ({"artifact_access": True, "deploy_signal": True}, "an unanswered question"),
        (
            {
                "artifact_access": True,
                "deploy_signal": True,
                "safe_interaction": True,
                "network_access": True,
            },
            "an unknown key, which is usually a misspelling of a real question",
        ),
        (
            {"artifact_access": "yes", "deploy_signal": True, "safe_interaction": True},
            "a non-boolean answer",
        ),
        ({}, "no answers at all"),
    ],
)
def test_an_incomplete_or_unknown_answer_set_is_refused(
    document: dict[str, object], reason: str
) -> None:
    """*Fails when* a question starts defaulting.

    *Matters because* an unanswered question reading as "yes" claims a capability
    nobody negotiated, on the artifact the engagement is signed against. There is
    no safe default here -- defaulting to "no" would silently decline engagements
    that qualify.
    """
    with pytest.raises(AdoptError) as caught:
        parse_answers(document)
    assert caught.value.code is ErrorCode.TIER_ANSWERS_INVALID, reason


@pytest.mark.unit
def test_every_question_is_asked_and_recorded_under_one_name() -> None:
    """*Fails when* the asked question and the recorded answer key drift apart,
    which makes a recorded answer unauditable."""
    assert tuple(name for name, _ in QUESTIONS) == tuple(Answers.model_fields)
    assert all(text.endswith("?") for _, text in QUESTIONS)


@pytest.mark.unit
def test_the_ai_floor_is_stated_once() -> None:
    assert AI_MINIMUM_TIER == "T3"
