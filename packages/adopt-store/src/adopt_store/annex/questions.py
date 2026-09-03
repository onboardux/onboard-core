"""SQLite realization of `adopt_ask.QuestionLog`. v6.1 §6 Build 3 (F2), plan D5.

Ten lines of insert, and the reason it is its own module rather than a method on
`SqliteAnnexRecords` is the same reason `search.py` is: the annex file is one
database with several unrelated ports over it, and a records class that grew a
method per consumer would be the thing every consumer then has to be handed.

**This module never opens the canonical store**, and could not usefully: a
passively logged question is not knowledge, has no revision, is cited by nothing
and is never exported. It exists so an operator who deliberately switched the
control on can later ask which questions their store keeps failing to answer.
"""

import datetime as _dt
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path

from adopt_ask.questionlog import QuestionRecord

from adopt_obs import format_timestamp, new_id
from adopt_store.annex.sqlite_annex import connect_annex

__all__ = ["SqliteQuestionLog", "open_question_log"]


@contextmanager
def open_question_log(
    annex: Path, *, repo_root: Path | None = None
) -> Iterator["SqliteQuestionLog"]:
    """Open the annex at `annex` and yield the question log over it."""
    connection = connect_annex(annex, repo_root=repo_root)
    try:
        yield SqliteQuestionLog(connection)
    finally:
        connection.close()


class SqliteQuestionLog:
    """Realizes `adopt_ask.QuestionLog` structurally.

    There is no update and no delete. A passive log entry is a fact about one
    moment, and a row that could be rewritten would make the log unable to
    answer the only question it exists for -- what was actually asked, and when.
    Deleting the annex file discards the whole log, which is the intended and
    only bulk exit.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append_question(self, record: QuestionRecord) -> str:
        row_id = new_id("qlog")
        self._connection.execute(
            "INSERT INTO ask_question_log "
            "(id, scope_ref, question, branch, citations, asked_at) "
            "VALUES (?, ?, ?, ?, ?, ?);",
            (
                row_id,
                record.scope_ref,
                record.question,
                record.branch,
                record.citations,
                format_timestamp(record.asked_at),
            ),
        )
        return row_id

    def questions_for(self, scope_ref: str) -> Sequence[QuestionRecord]:
        """Every logged question for one scope, oldest first.

        Read side of the same table, here rather than on the port because
        `adopt_ask` never reads the log -- answering a question does not consult
        what was asked before it. An operator inspecting their own annex does.
        """
        with closing(
            self._connection.execute(
                "SELECT scope_ref, question, branch, citations, asked_at "
                "FROM ask_question_log WHERE scope_ref = ? ORDER BY asked_at, id;",
                (scope_ref,),
            )
        ) as cursor:
            rows = cursor.fetchall()
        return tuple(
            QuestionRecord(
                scope_ref=str(row["scope_ref"]),
                question=str(row["question"]),
                branch=str(row["branch"]),
                citations=int(row["citations"]),
                asked_at=_parse(str(row["asked_at"])),
            )
            for row in rows
        )


def _parse(value: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
