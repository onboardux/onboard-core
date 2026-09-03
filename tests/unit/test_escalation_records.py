"""The escalation row: what it records, what it refuses to record, and when.

Build 3's F2 is a privacy rule expressed as a nullable column, which is a shape
that reads as an omission unless something asserts otherwise. These tests are
that something: the default records *that* a question was asked and never *what*
it was, and the text arrives only when a caller passes it deliberately.

The guards mirror the review queue's, because they are the same guards over a
different subject -- an id that names nothing, and a row already stamped.
"""

import pytest

from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle


def _revision(store: SqliteStoreHandle, scope: Scope, title: str = "Key rotation") -> str:
    _, revision_id = store.items().record(
        scope=scope,
        kind="procedure",
        title=title,
        body_md="Rotate through the vault.",
        authority_class="human_confirmed",
        verification="verified",
    )
    return revision_id


@pytest.mark.unit
class TestConsentIsTheOnlyPathToTheText:
    def test_an_escalation_opened_without_consent_stores_no_question_text(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* `open_escalation` starts defaulting the question text in.
        *Matters because* v6.1 F2 splits passive logging from explicit
        escalation precisely so that asking a question is not the same as
        consenting to store it -- a default that filled the column would turn
        every UNKNOWN into a stored transcript of what an FDE was unsure about,
        on a client's engagement. *No other instrument catches it because* the
        row is valid either way: `question` is nullable, so nothing downstream
        fails, and the leak is invisible until someone reads the table.
        """
        assert s4_scope.system is not None
        escalation_id = s4_store.governance().open_escalation(
            system_id=str(s4_scope.system.id), branch="ungrounded"
        )

        row = s4_store.governance().get_escalation(escalation_id)
        assert row is not None
        assert row.question is None
        assert row.status == "open"
        assert row.branch == "ungrounded"

    def test_the_text_is_stored_when_it_is_passed(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """The other half of the same rule: consent *does* record the question.

        *Fails when* the text is dropped, redacted or truncated on the way in.
        *Matters because* an escalation with no question is unanswerable -- the
        capture ratchet needs the words a human can answer. *No other instrument
        catches it because* the test above passes just as well over a facade
        that stores nothing at all."""
        assert s4_scope.system is not None
        escalation_id = s4_store.governance().open_escalation(
            system_id=str(s4_scope.system.id),
            branch="ungrounded",
            question="how do I rotate the API key?",
        )

        row = s4_store.governance().get_escalation(escalation_id)
        assert row is not None
        assert row.question == "how do I rotate the API key?"

    def test_channel_is_null_for_a_local_escalation(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* a nearest-fit channel is written for a CLI question.
        *Matters because* `escalation_channel` lists outbound channels only and
        B7's first channel report has to be able to tell a question that arrived
        through Slack from one typed at a prompt; a `portal` written here to
        avoid a NULL would make the two indistinguishable forever, since the row
        is never rewritten. *No other instrument catches it because* every value
        in that enum is schema-valid."""
        assert s4_scope.system is not None
        escalation_id = s4_store.governance().open_escalation(
            system_id=str(s4_scope.system.id), branch="stale", question="why refunds?"
        )

        row = s4_store.governance().get_escalation(escalation_id)
        assert row is not None
        assert row.channel is None


@pytest.mark.unit
class TestTheAnswerStamp:
    def test_answering_points_the_row_at_the_revision_that_answers_it(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* the stamp lands without `candidate_revision_id`.
        *Matters because* an escalation that says it was answered while pointing
        at no knowledge is indistinguishable from an open one to every reader
        but a human comparing timestamps -- and the ratchet's whole claim is
        that the *next* asker gets the answer, not that someone marked a row.
        *No other instrument catches it because* `status` alone is enough to
        drop the question out of every open-question listing."""
        assert s4_scope.system is not None
        revision_id = _revision(s4_store, s4_scope)
        escalation_id = s4_store.governance().open_escalation(
            system_id=str(s4_scope.system.id),
            branch="ungrounded",
            question="how do I rotate the API key?",
        )

        before = s4_store.governance().answer_escalation(
            escalation_id=escalation_id,
            candidate_revision_id=revision_id,
            answered_by="alice",
        )

        assert before.status == "open", "the pre-stamp row is what the caller is handed"
        row = s4_store.governance().get_escalation(escalation_id)
        assert row is not None
        assert row.status == "answered"
        assert row.candidate_revision_id == revision_id
        assert row.answered_by == "alice"
        assert row.answered_at is not None

    def test_a_question_is_answered_once(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* a second answer overwrites the first.
        *Matters because* the row would then point at one of two revisions with
        no record of which the asker was actually given, and the loser lands as
        knowledge nothing links back to -- `REVIEW_ITEM_RESOLVED`'s failure in a
        different costume. *No other instrument catches it because* both writes
        are individually valid and the second leaves a perfectly consistent
        row."""
        assert s4_scope.system is not None
        first = _revision(s4_store, s4_scope, "First")
        second = _revision(s4_store, s4_scope, "Second")
        escalation_id = s4_store.governance().open_escalation(
            system_id=str(s4_scope.system.id), branch="ungrounded", question="q?"
        )
        s4_store.governance().answer_escalation(
            escalation_id=escalation_id, candidate_revision_id=first
        )

        with pytest.raises(AdoptError) as raised:
            s4_store.governance().answer_escalation(
                escalation_id=escalation_id, candidate_revision_id=second
            )

        assert raised.value.code is ErrorCode.ESCALATION_ALREADY_ANSWERED
        row = s4_store.governance().get_escalation(escalation_id)
        assert row is not None
        assert row.candidate_revision_id == first

    def test_an_unknown_escalation_is_refused_with_its_own_code(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """ "There is no such question" and "that one is already answered" send an
        operator to two different places -- check the id you were printed, versus
        append a revision to the item it already points at. CR-38's precedent."""
        revision_id = _revision(s4_store, s4_scope)
        with pytest.raises(AdoptError) as raised:
            s4_store.governance().answer_escalation(
                escalation_id="esc_nope", candidate_revision_id=revision_id
            )

        assert raised.value.code is ErrorCode.ESCALATION_NOT_FOUND


@pytest.mark.unit
class TestListing:
    def test_open_questions_are_listed_newest_first_and_filtered_by_status(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* the status filter stops filtering, or the order inverts.
        *Matters because* the listing is how an FDE finds what to answer: an
        answered question in that list is work someone repeats, and oldest-first
        buries the question just asked under every question ever asked.
        *No other instrument catches it because* an unfiltered listing is a
        superset -- everything the caller wanted is in it."""
        assert s4_scope.system is not None
        system_id = str(s4_scope.system.id)
        governance = s4_store.governance()
        older = governance.open_escalation(
            system_id=system_id, branch="ungrounded", question="first?"
        )
        newer = governance.open_escalation(
            system_id=system_id, branch="ungrounded", question="second?"
        )
        governance.answer_escalation(
            escalation_id=older, candidate_revision_id=_revision(s4_store, s4_scope)
        )

        every = governance.escalations(system_id=system_id)
        assert [row.id for row in every] == [newer, older]

        still_open = governance.escalations(system_id=system_id, status="open")
        assert [row.id for row in still_open] == [newer]
