"""Turning an answer the store could not give into a question someone can.

**F2 in one sentence: the action is the consent.** Passive question logging is
off by default and stores nothing; *escalating* stores the question text,
because storing the text is the entire purpose of the act. Both halves are here,
and the half that matters is the one that says no -- `consented` returns `False`
for every path that did not involve a human choosing.

**Why consent is a returned value rather than a flag threaded through.** The
failure this module is shaped against is not someone deliberately storing text
without permission. It is a later edit to a prompt helper -- a default answer,
an `input()` that raises `EOFError` in CI and gets wrapped in a
`try/except: return True`, a `--yes` that grew to mean everything -- quietly
turning "we asked and they agreed" into "nobody was there to object". So the
decision is one small pure function with the non-interactive case written first
and asserted by name, and the writer below it takes the text as an ordinary
argument that a caller has to have decided to pass.

**An escalation is not a refusal.** It is recorded *after* the answer is
composed and never instead of one: a STALE answer still serves its prior content
and its cause, and escalating it asks for a fresher answer rather than
withdrawing the one given. That is why `prior_revision_id` is populated for STALE
(and for a disputed KNOWN) and absent for UNKNOWN -- the branch decides which
question is being asked.
"""

from collections.abc import Callable
from typing import Final, Protocol

from adopt_ask.branch import KNOWN, STALE, UNKNOWN, Answer
from adopt_model._enums import EscalationBranch

__all__ = [
    "ESCALATABLE",
    "EscalationWriter",
    "consent_prompt",
    "consented",
    "escalate",
    "escalation_branch",
    "may_dispute",
    "may_escalate",
]

#: The branches an asker is *offered* escalation from. A KNOWN answer is
#: deliberately absent: the store answered, and prompting to record a question it
#: answered would fill the open queue with work nobody has.
ESCALATABLE: Final[frozenset[str]] = frozenset({UNKNOWN, STALE})

#: Plan decision D3, extended 2026-10-05. Total over the three branches, so there
#: is no fallback arm to pick a wrong-but-valid value.
#:
#: **A KNOWN answer can be disputed, never by prompt and only by `--escalate`.**
#: Until 2026-10-05 `adopt ask --escalate` on a KNOWN answer exited 0, recorded
#: nothing and said nothing (independence transcript T2) -- so the receiving team
#: in an exit drill, served a confidently wrong answer, had no way to put that in
#: front of the owner. Saying "this answer is wrong" is a report against the
#: knowledge, which is what `bug_report` (in the enum since v3, never written
#: until now) names. The served revision travels as `prior_revision_id`, so
#: whoever picks it up sees exactly what the asker was told.
_BRANCH_FOR: Final[dict[str, EscalationBranch]] = {
    UNKNOWN: "ungrounded",
    STALE: "stale",
    KNOWN: "bug_report",
}


class EscalationWriter(Protocol):
    """The one method escalation needs from the store, declared here (CR-34/CR-37).

    Structural, like `SearchRecords`: `adopt_ask` never imports `adopt_store`,
    the CLI hands it a `GovernanceFacade`, and this package cannot reach a driver
    even indirectly -- which is what `no-raw-sqlite` requires of it by name.
    """

    def open_escalation(
        self,
        *,
        system_id: str,
        branch: EscalationBranch,
        question: str | None = None,
        prior_revision_id: str | None = None,
        owner_actor_id: str | None = None,
    ) -> str: ...


def may_escalate(answer: Answer) -> bool:
    """Whether this answer is one an asker is offered to turn into an open question."""
    return answer.branch in ESCALATABLE


def may_dispute(answer: Answer) -> bool:
    """Whether this answer is one an asker can explicitly report as wrong (KNOWN)."""
    return answer.branch == KNOWN


def escalation_branch(answer: Answer) -> EscalationBranch:
    """The `escalation_branch` value for `answer`'s branch (plan D3).

    Raises:
        ValueError: For a branch outside the mapping. Not an `AdoptError`: no
            operator input reaches here, so a failure is a caller bug rather than
            a runtime condition, and inventing a schema-valid branch for it is how
            a queue fills with questions nobody asked.
    """
    mapped = _BRANCH_FOR.get(answer.branch)
    if mapped is None:
        raise ValueError(f"a {answer.branch.upper()} answer is not escalatable")
    return mapped


def consent_prompt(answer: Answer) -> str:
    """The sentence a human is shown before their question is stored.

    It names what will be stored, because a consent prompt that says only
    "escalate?" is asking about an outcome while obtaining permission for a
    disclosure.
    """
    if answer.branch == STALE:
        return (
            "Record this as an open question, storing its text, so someone can "
            "refresh the answer above?"
        )
    if answer.branch == KNOWN:
        return (
            "Report the answer above as wrong, storing the question's text, so its "
            "owner can correct it?"
        )
    return "Record this as an open question, storing its text, so someone can answer it?"


def consented(
    answer: Answer,
    *,
    escalate_flag: bool,
    interactive: bool,
    confirm: Callable[[str], bool] | None = None,
) -> bool:
    """Whether the asker consented to storing this question's text.

    Args:
        answer: The composed answer. A KNOWN answer never escalates, whatever
            the flags say -- checked here rather than at the call site so the
            rule holds for the CLI, `adopt serve` and any later caller alike.
        escalate_flag: `--escalate`. The action *is* the consent (F2), so this
            alone is sufficient and no prompt follows it.
        interactive: Whether a human is at the other end. **`False` is the
            answer for `--json`, for `adopt serve` and for any non-TTY**, and it
            returns `False` here before `confirm` is consulted at all.
        confirm: How to ask, injected. Absent in every non-interactive context,
            and a missing `confirm` is a `False` rather than an assumed yes:
            "there was nobody to ask" and "they said yes" are the two states
            this whole module exists to keep apart.

    Returns:
        `True` only when a human chose it -- by flag or by answering the prompt.
        A KNOWN answer is disputed **only by the flag**: nobody is prompted to
        dispute every answer they are given.
    """
    if may_dispute(answer):
        return escalate_flag
    if not may_escalate(answer):
        return False
    if escalate_flag:
        return True
    if not interactive or confirm is None:
        return False
    return confirm(consent_prompt(answer))


def escalate(writer: EscalationWriter, answer: Answer, *, system_id: str) -> str:
    """Record `answer`'s question as open, **with its text**. Returns the id.

    Call this only where `consented` returned `True`. It stores the text
    unconditionally and by design: an escalation whose question is `None` is
    unanswerable, so a "careful" variant that omitted it would produce rows that
    look like work and cannot be worked. The consent decision belongs one level
    up, where the flags and the terminal are.

    `prior_revision_id` is the first citation of a STALE or KNOWN answer -- what
    the asker was actually served, so whoever refreshes or corrects it can see
    what they are replacing. An UNKNOWN carries none: there is nothing prior to
    point at, and pointing at a withheld unverified revision would suggest the
    store served something it refused to.
    """
    prior = answer.citations[0].revision_id if answer.branch in (STALE, KNOWN) else None
    return writer.open_escalation(
        system_id=system_id,
        branch=escalation_branch(answer),
        question=answer.question,
        prior_revision_id=prior,
    )
