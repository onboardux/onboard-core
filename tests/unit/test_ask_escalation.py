"""Consent, and the four ways a prompt helper could quietly manufacture it.

F2's rule is short -- passive logging stores nothing, escalation stores the text,
and the act of escalating *is* the consent -- and the rule is easy to hold on the
day it is written. What these tests defend is the day after: a `confirm` that
raises `EOFError` in CI and gets wrapped in a permissive `except`, a `--yes` that
grows to mean everything, a prompt that defaults to yes because a reviewer found
the extra keystroke annoying. Each of those is a one-line change that leaves
every other test in the suite green.

So the non-interactive cases are asserted by name, `confirm` is asserted **not
called** rather than merely returning nothing, and the writer is a spy that
records what it was handed -- because a facade double that ignored `question`
would let a "we stored nothing" claim pass over code that stored everything.
"""

from typing import Final

import pytest
from adopt_ask.branch import KNOWN, STALE, UNKNOWN, Answer, Citation
from adopt_ask.escalate import (
    consent_prompt,
    consented,
    escalate,
    escalation_branch,
    may_escalate,
)

from adopt_model._enums import EscalationBranch

QUESTION: Final[str] = "how do I rotate the API key?"


class _SpyWriter:
    """Records every call rather than answering one, so a test can see the text."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def open_escalation(
        self,
        *,
        system_id: str,
        branch: EscalationBranch,
        question: str | None = None,
        prior_revision_id: str | None = None,
        owner_actor_id: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "system_id": system_id,
                "branch": branch,
                "question": question,
                "prior_revision_id": prior_revision_id,
                "owner_actor_id": owner_actor_id,
            }
        )
        return "esc_01SPY"


def _citation(revision_id: str = "krev_01AAA") -> Citation:
    return Citation(
        revision_id=revision_id,
        item_id="ki_01AAA",
        title="Refund approvals",
        body_md="Approval exists because chargebacks were disputed.",
        identity_uris=(
            "onboard-v1://acme/platform/orders-api/prod/endpoint/-/POST %2Fv1%2Frefunds",
        ),
        origin="ranked",
        freshness_state="stale",
        deciding_rule="load_bearing_identity_moved",
    )


def _unknown() -> Answer:
    return Answer(question=QUESTION, branch=UNKNOWN, citations=())


def _stale() -> Answer:
    return Answer(
        question="why does the approval step exist on refunds?",
        branch=STALE,
        citations=(_citation(),),
        cause="load_bearing_identity_moved",
    )


def _known() -> Answer:
    fresh = Citation(
        revision_id="krev_01BBB",
        item_id="ki_01BBB",
        title="Refund approvals",
        body_md="body",
        identity_uris=(),
        origin="ranked",
        freshness_state="unverified",
        deciding_rule="no_rule_fired",
    )
    return Answer(question=QUESTION, branch=KNOWN, citations=(fresh,))


def _never_called(prompt: str) -> bool:
    raise AssertionError(f"confirm must not be consulted here; it was asked {prompt!r}")


@pytest.mark.unit
class TestNobodyChoseIsNotYes:
    def test_a_non_interactive_run_never_prompts_and_never_consents(self) -> None:
        """*Fails when* a non-TTY path reaches the prompt at all.
        *Matters because* `--json` and `adopt serve` have no human on the other
        end: a prompt there either blocks a pipeline forever or -- far worse --
        gets an `EOFError` that a later "fix" swallows into a default yes, which
        stores a client's question text with nobody having agreed. *No other
        instrument catches it because* a swallowed prompt looks exactly like a
        successful one from the outside, and the row it writes is schema-valid.
        """
        assert (
            consented(_unknown(), escalate_flag=False, interactive=False, confirm=_never_called)
            is False
        )

    def test_a_missing_confirm_is_a_no_rather_than_an_assumed_yes(self) -> None:
        """*Fails when* absent `confirm` starts meaning "go ahead".
        *Matters because* "there was nobody to ask" and "they said yes" are the
        two states this module exists to keep apart, and a caller that forgot to
        wire a prompt is precisely the first state. *No other instrument catches
        it because* the omission is invisible at the call site -- the parameter
        has a default."""
        assert consented(_unknown(), escalate_flag=False, interactive=True, confirm=None) is False

    def test_declining_the_prompt_stores_nothing(self) -> None:
        """The prompt is real: a `no` is honoured, not merely offered."""
        assert (
            consented(_unknown(), escalate_flag=False, interactive=True, confirm=lambda _: False)
            is False
        )

    def test_a_known_answer_never_escalates_however_the_flags_read(self) -> None:
        """*Fails when* `--escalate` on a KNOWN answer opens a question.
        *Matters because* the store answered: the row would be work nobody has,
        and disagreeing with a KNOWN answer is a correction (Build 6's review
        path), not an unanswered question. *No other instrument catches it
        because* the flag was passed deliberately, so nothing looks wrong."""
        assert may_escalate(_known()) is False
        assert (
            consented(_known(), escalate_flag=True, interactive=True, confirm=_never_called)
            is False
        )


@pytest.mark.unit
class TestConsentIsTheAction:
    def test_the_flag_alone_consents_and_asks_nothing_further(self) -> None:
        """F2: the action *is* the consent, so `--escalate` never prompts.

        *Fails when* a confirmation is added on top of the flag. *Matters
        because* the e2e and every scripted use pass the flag precisely to avoid
        a prompt, so a second gate would hang CI. *No other instrument catches
        it because* an interactive developer would just answer it."""
        assert (
            consented(_unknown(), escalate_flag=True, interactive=False, confirm=_never_called)
            is True
        )

    def test_confirming_the_prompt_consents(self) -> None:
        assert (
            consented(_unknown(), escalate_flag=False, interactive=True, confirm=lambda _: True)
            is True
        )

    def test_the_prompt_names_what_will_be_stored(self) -> None:
        """*Fails when* the prompt shrinks to "escalate? [y/N]".
        *Matters because* consent obtained for an outcome is not consent for a
        disclosure: the human is agreeing to their question text being written
        into the client's store, and a prompt that does not say so is not asking
        the question it is recording an answer to."""
        for answer in (_unknown(), _stale()):
            assert "storing its text" in consent_prompt(answer)


@pytest.mark.unit
class TestWhatTheRowCarries:
    def test_an_unknown_escalates_as_ungrounded_carrying_its_text_and_no_prior(self) -> None:
        """*Fails when* the branch mapping (plan D3) drifts, or the text is
        dropped. *Matters because* an escalation whose `question` is NULL is
        unanswerable -- it looks like work and cannot be worked -- and a
        `prior_revision_id` on an UNKNOWN would claim the store served something
        it explicitly refused to. *No other instrument catches it because* every
        value involved is schema-valid, including the wrong ones."""
        writer = _SpyWriter()

        escalate(writer, _unknown(), system_id="sys_01AAA")

        assert writer.calls == [
            {
                "system_id": "sys_01AAA",
                "branch": "ungrounded",
                "question": QUESTION,
                "prior_revision_id": None,
                "owner_actor_id": None,
            }
        ]

    def test_a_stale_escalation_points_at_what_the_asker_was_served(self) -> None:
        """*Fails when* `prior_revision_id` is dropped from the STALE path.
        *Matters because* whoever refreshes the answer needs to see the revision
        being replaced; without it they are re-answering from scratch a question
        the store partly knows. *No other instrument catches it because* the
        escalation is otherwise complete and the queue looks healthy."""
        writer = _SpyWriter()

        escalate(writer, _stale(), system_id="sys_01AAA")

        assert writer.calls[0]["branch"] == "stale"
        assert writer.calls[0]["prior_revision_id"] == "krev_01AAA"

    def test_a_known_answer_has_no_branch_to_map(self) -> None:
        """The mapping is total over what may escalate and refuses the rest,
        rather than falling through to a schema-valid `bug_report`."""
        with pytest.raises(ValueError, match="not escalatable"):
            escalation_branch(_known())

    def test_channel_is_never_supplied_by_this_layer(self) -> None:
        """*Fails when* `escalate` starts passing a channel. *Matters because*
        plan D3 leaves it NULL locally: the enum lists outbound channels and a
        terminal is not one, so a nearest-fit value would make B7's first channel
        report unable to tell a Slack question from a typed one -- permanently,
        since the row is never rewritten."""
        writer = _SpyWriter()
        escalate(writer, _unknown(), system_id="sys_01AAA")
        assert "channel" not in writer.calls[0]
