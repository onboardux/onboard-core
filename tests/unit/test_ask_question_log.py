"""Passive logging: the default is the feature, so the default is what is tested.

`ADOPT_ASK_LOG_QUESTIONS` is off, and a control that is off is invisible --
every downstream assertion passes identically whether it is honoured or ignored.
So the tests here assert the *absence*: nothing written at the default, nothing
written for an unrecognised value, and -- the one that would actually have
happened -- nothing written by a truthiness check that reads the string `"0"` as
`True`, which is what `bool("0")` does in Python.

The second half is the canonical store staying untouched however the key is set:
a question is not knowledge, and the annex is where plan D5 put it.
"""

import datetime as _dt
from pathlib import Path

import pytest
from adopt_ask.branch import KNOWN, UNKNOWN, Answer, Citation
from adopt_ask.questionlog import log_question, should_log

from adopt_obs import ManualClock
from adopt_store.annex.questions import SqliteQuestionLog, open_question_log
from adopt_store.annex.sqlite_annex import annex_path
from adopt_store.api import SqliteStoreHandle

KEY = "ADOPT_ASK_LOG_QUESTIONS"
SCOPE = "northwind/acme-erp/orders-api/prod"


def _unknown() -> Answer:
    return Answer(question="how do I rotate the API key?", branch=UNKNOWN, citations=())


def _known() -> Answer:
    return Answer(
        question="why does the approval step exist on refunds?",
        branch=KNOWN,
        citations=(
            Citation(
                revision_id="krev_01AAA",
                item_id="ki_01AAA",
                title="Refunds",
                body_md="body",
                identity_uris=(),
                origin="ranked",
                freshness_state="unverified",
                deciding_rule="no_rule_fired",
            ),
        ),
    )


@pytest.mark.unit
class TestOffIsTheDefaultAndOffMeansOff:
    def test_the_registry_default_does_not_switch_logging_on(self) -> None:
        """*Fails when* the shipped default starts enabling passive logging.
        *Matters because* every store that never opted in would accumulate a
        transcript of what its FDEs did not know on a client engagement -- the
        precise disclosure v6.1 F2 keeps behind an explicit act. *No other
        instrument catches it because* the rows are in the annex, are never
        exported and break nothing: the store works perfectly while collecting
        them."""
        from adopt_cli.config import REGISTRY

        declared = {key.name: key.default for key in REGISTRY}
        assert declared[KEY] == "0"
        assert should_log({KEY: declared[KEY]}, key=KEY) is False

    def test_the_string_zero_is_false_rather_than_truthy(self) -> None:
        """*Fails when* the value is coerced with `bool(value)`.
        *Matters because* `bool("0")` is `True` in Python, and the resolved
        config value is always a string -- so the obvious one-line reading of
        this key turns its own default into "on", silently, on every store.
        *No other instrument catches it because* the inversion produces no
        error, no warning and no visible behaviour change; the annex just fills
        up."""
        assert should_log({KEY: "0"}, key=KEY) is False

    @pytest.mark.parametrize("value", ["", "  ", "no", "off", "false", "2", "maybe", "O1"])
    def test_anything_unrecognised_is_off(self, value: str) -> None:
        """A typo'd value fails closed. Setting `ADOPT_ASK_LOG_QUESTIONS=ture`
        must not enable a disclosure control."""
        assert should_log({KEY: value}, key=KEY) is False

    def test_an_absent_key_is_off(self) -> None:
        assert should_log({}, key=KEY) is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
    def test_an_explicit_affirmative_switches_it_on(self, value: str) -> None:
        """The other half: an operator who sets it deliberately gets it.

        *Fails when* the allow-list stops recognising the documented value.
        *Matters because* a control that cannot be turned on is a control whose
        off-by-default tests prove nothing."""
        assert should_log({KEY: value}, key=KEY) is True


@pytest.mark.unit
class TestWhereTheRowsGo:
    def test_a_logged_question_lands_in_the_annex_and_not_in_canon(
        self, s4_store: SqliteStoreHandle, tmp_path: Path
    ) -> None:
        """*Fails when* the log is written to the canonical store instead.
        *Matters because* canonical tables are exportable by construction
        (`Manifest.exportable_tables`), so a question log in canon would put a
        transcript of the delivery team's uncertainty into the client's bundle
        -- and plan D5 put it in the annex for exactly that reason. *No other
        instrument catches it because* a new canonical table would need a
        manifest row, which would pass every generated check; nothing else
        asserts that this data is *not* there."""
        clock = ManualClock(_dt.datetime(2026, 8, 22, 9, 0, tzinfo=_dt.UTC))
        annex = annex_path(tmp_path / "store.db")

        with open_question_log(annex) as log:
            row_id = log_question(log, _unknown(), scope_ref=SCOPE, asked_at=clock.now())

        assert row_id.startswith("qlog_")

        with open_question_log(annex) as log:
            stored = log.questions_for(SCOPE)
        assert [row.question for row in stored] == ["how do I rotate the API key?"]
        assert stored[0].branch == "unknown"
        assert stored[0].citations == 0

        tables = {
            str(row["name"])
            for row in s4_store.backend.query("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "ask_question_log" not in tables, (
            "the passive log is annex-only; a canonical table would be exportable"
        )

    def test_the_row_carries_a_citation_count_and_no_revision_ids(self, tmp_path: Path) -> None:
        """*Fails when* revision ids start being copied into the log.
        *Matters because* that would make the annex a second, unexported,
        uncited index into canon -- and an index whose rows outlive the
        revisions they name answers questions about knowledge that has since
        been superseded. The count is what the log is for: which questions keep
        coming back thin."""
        clock = ManualClock(_dt.datetime(2026, 8, 22, 9, 0, tzinfo=_dt.UTC))
        annex = annex_path(tmp_path / "store.db")

        with open_question_log(annex) as log:
            log_question(log, _known(), scope_ref=SCOPE, asked_at=clock.now())
            stored = log.questions_for(SCOPE)
            columns = {
                str(row[1])
                for row in log._connection.execute(
                    "PRAGMA table_info(ask_question_log);"
                ).fetchall()
            }

        assert stored[0].citations == 1
        assert "revision_id" not in columns
        assert "citations" in columns

    def test_the_log_is_scoped_and_ordered_oldest_first(self, tmp_path: Path) -> None:
        """*Fails when* the scope filter drops, or the order inverts.
        *Matters because* one annex serves one store, but a store spans four
        scope levels: an unfiltered read would mix one engagement's questions
        into another's report. *No other instrument catches it because* an
        unfiltered listing is a superset and looks complete."""
        clock = ManualClock(_dt.datetime(2026, 8, 22, 9, 0, tzinfo=_dt.UTC))
        annex = annex_path(tmp_path / "store.db")
        other = "northwind/acme-erp/billing-api/prod"

        with open_question_log(annex) as log:
            log_question(log, _unknown(), scope_ref=SCOPE, asked_at=clock.now())
            clock.advance(_dt.timedelta(minutes=1))
            log_question(log, _known(), scope_ref=SCOPE, asked_at=clock.now())
            clock.advance(_dt.timedelta(minutes=1))
            log_question(log, _unknown(), scope_ref=other, asked_at=clock.now())

            here = log.questions_for(SCOPE)
            there = log.questions_for(other)

        assert [row.branch for row in here] == ["unknown", "known"]
        assert len(there) == 1

    def test_the_realization_satisfies_the_declared_port(self) -> None:
        """The structural check the CR-34 pattern rests on: `adopt_ask` declares
        `QuestionLog` and never imports the class below, so nothing but this
        assertion notices when the two drift apart."""
        from adopt_ask.questionlog import QuestionLog

        def _accepts(log: QuestionLog) -> QuestionLog:
            return log

        assert _accepts(SqliteQuestionLog.__new__(SqliteQuestionLog)) is not None
