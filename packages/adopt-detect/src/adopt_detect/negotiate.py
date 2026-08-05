"""Tier negotiation: three qualification questions to `T0`-`T4`.

PRD F10.4 and **CR-38**, which ratifies the ladder. The three questions and the
mapping were referenced by `01`, `03` and `05` as though defined and were stated
in none of them; CR-38 closes that, and this module is the only implementation of
it.

**The ladder, in one table.**

| `artifact_access` | `deploy_signal` | `safe_interaction` | tier |
|---|---|---|---|
| no  | -   | -   | `T0` -- decline the engagement |
| yes | no  | no  | `T1` -- artifacts only |
| yes | yes | no  | `T2` -- artifacts and deploy signal |
| yes | no  | yes | `T3` -- artifacts and a behavioural probe |
| yes | yes | yes | `T4` -- full |

**Why `artifact_access` alone decides `T0`.** Without it there is nothing to
extract, nothing to bind to and nothing to observe changing; deploy events
against a system whose contents we cannot read are notifications about an opaque
box. `T0` is therefore not "the lowest tier of service" but "there is no
engagement here", which is why F10.6 makes it a decline recommendation rather
than a degraded mode.

**Why `safe_interaction` outranks `deploy_signal`.** Both add one capability, so
the ordering had to be argued rather than assumed. A deploy signal tells us
*that* something changed; a safe interaction tells us *what the system now does*.
Only the second can confirm or refute a claim about behaviour, which is what
makes `T3` the floor for the `ai` archetype (F10.7): a prompt change with no way
to run the system is a change we can detect and cannot evaluate.

**No answer defaults.** Every question must be answered explicitly. An
unanswered question quietly reading as "yes" is how a tier gets claimed that was
never negotiated -- and the tier is what a client signs against.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError

from adopt_model._enums import Archetype, Tier
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "AI_MINIMUM_TIER",
    "QUESTIONS",
    "Answers",
    "TierDecision",
    "negotiate",
    "parse_answers",
    "unavailable_capabilities",
]

#: The three questions, in the order they are asked and reported. Text lives here
#: because it is what an operator is shown and what the boundary artefact
#: reproduces -- two copies would let the recorded question drift from the asked
#: one, which makes an answer unauditable.
QUESTIONS: Final[tuple[tuple[str, str], ...]] = (
    (
        "artifact_access",
        "Are prompts, model configuration and source readable by us "
        "(in version control, or through an API we can call)?",
    ),
    (
        "deploy_signal",
        "Do we receive what fires on deploy (CI/CD events, or an audit trail)?",
    ),
    (
        "safe_interaction",
        "Can a safe test interaction run (a mock, sandbox or shadow path exists)?",
    ),
)

#: PRD F10.7. An `ai` system below this tier is recorded as a boundary violation.
AI_MINIMUM_TIER: Final[Tier] = "T3"

#: Tier order, for comparisons. Declared rather than derived from the enum's
#: declaration order, because a comparison that silently depended on how the
#: manifest happened to list the values would be a comparison nobody could
#: review.
_TIER_ORDER: Final[tuple[Tier, ...]] = ("T0", "T1", "T2", "T3", "T4")

#: The CR-38 ladder as data: (deploy_signal, safe_interaction) -> tier, applied
#: only once `artifact_access` is true.
_LADDER: Final[dict[tuple[bool, bool], Tier]] = {
    (False, False): "T1",
    (True, False): "T2",
    (False, True): "T3",
    (True, True): "T4",
}

#: What each capability needs, named so that a violation can say what is missing
#: rather than only that something is (F10.7 requires the capabilities be named).
_CAPABILITY_MINIMUM: Final[tuple[tuple[str, Tier], ...]] = (
    ("artifact_extraction", "T1"),
    ("change_detection_on_deploy", "T2"),
    ("behavioural_probing", "T3"),
    ("continuous_behavioural_baselines", "T4"),
)


class Answers(BaseModel):
    """The three answers. Closed, strict, and no field carries a default.

    **`strict=True` is load-bearing, not decoration.** Pydantic's default lax
    mode coerces `"yes"`, `"on"` and `1` to `True` -- so an answers file that
    said `"artifact_access": "no"` would coerce to... a validation error on
    `"no"`, but `"yes"` would sail through as `True`. A tier is not a place to
    accept a string that *looks* affirmative: the answer has to be the boolean
    the operator meant, or the file is refused and re-read by a human.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact_access: bool
    deploy_signal: bool
    safe_interaction: bool


@dataclass(frozen=True, slots=True)
class TierDecision:
    """The negotiated tier and everything that follows from it deterministically."""

    tier: Tier
    answers: Answers
    decline_recommended: bool
    unavailable: tuple[str, ...]

    @property
    def rationale(self) -> str:
        """One operator-facing sentence. No client content, ever."""
        if self.decline_recommended:
            return (
                "No artifact access: there is nothing to extract, bind or observe. "
                "Declining is the recommendation."
            )
        granted = [name for name, minimum in _CAPABILITY_MINIMUM if not _below(self.tier, minimum)]
        return f"{self.tier} grants {', '.join(granted)}."


def _below(tier: Tier, minimum: Tier) -> bool:
    return _TIER_ORDER.index(tier) < _TIER_ORDER.index(minimum)


def parse_answers(document: Mapping[str, object]) -> Answers:
    """Validate an answers document.

    Args:
        document: The parsed `--answers` file.

    Returns:
        The validated answers.

    Raises:
        AdoptError: ``TIER_ANSWERS_INVALID`` when a question is unanswered, an
            unknown key is present, or an answer is not a boolean. All three are
            refusals rather than repairs: a missing answer cannot be defaulted
            without claiming a capability nobody confirmed, and an unknown key is
            almost always a misspelling of a question that then went unanswered.
    """
    try:
        return Answers.model_validate(document)
    except ValidationError as error:
        expected = ", ".join(name for name, _ in QUESTIONS)
        raise AdoptError(
            ErrorCode.TIER_ANSWERS_INVALID,
            message=f"the answers document is not the three qualification answers: {error.errors()}",
            hint=f"Answer exactly {expected}, each true or false. Nothing defaults: an "
            "unanswered question reading as 'yes' would claim a capability that was "
            "never negotiated, and the tier is what the engagement is signed against.",
        ) from error


def unavailable_capabilities(tier: Tier) -> tuple[str, ...]:
    """The capabilities this tier does not grant, in ladder order."""
    return tuple(name for name, minimum in _CAPABILITY_MINIMUM if _below(tier, minimum))


def negotiate(answers: Answers) -> TierDecision:
    """Derive the tier from the three answers. Total, deterministic, no I/O.

    Returns:
        A `TierDecision`. `decline_recommended` is true exactly at `T0`.
    """
    if not answers.artifact_access:
        return TierDecision(
            tier="T0",
            answers=answers,
            decline_recommended=True,
            unavailable=unavailable_capabilities("T0"),
        )
    tier = _LADDER[(answers.deploy_signal, answers.safe_interaction)]
    return TierDecision(
        tier=tier,
        answers=answers,
        decline_recommended=False,
        unavailable=unavailable_capabilities(tier),
    )


def violates_archetype_floor(archetype: Archetype | None, tier: Tier) -> bool:
    """Whether this archetype at this tier is a recorded boundary violation.

    PRD F10.7 names exactly one: `ai` below `T3`. An unknown archetype -- the
    ambiguous case -- violates nothing, because a floor cannot be applied to a
    classification that was explicitly declined.
    """
    return archetype == "ai" and _below(tier, AI_MINIMUM_TIER)
