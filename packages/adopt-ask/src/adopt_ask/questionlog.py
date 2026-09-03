"""Passive question logging: off by default, and the default is the feature.

F2 splits two things that look alike and are not. **Escalation** stores a
question because a human asked for it to be stored. **Passive logging** stores
every question anybody typed, and v6.1 turns it off -- so the interesting code
here is `should_log`, which answers `False` for everything except an explicit,
affirmative, operator-set value.

**Why the truthiness is spelled out rather than delegated.** The resolved config
value arrives as a string, and `bool("0")` is `True` in Python. A key whose whole
purpose is being off by default, read through a coercion that turns its default
into on, is the single most likely way this control silently inverts -- and it
would invert quietly, on stores that had never opted in, writing a transcript of
what a client's delivery team did not know. `_ENABLED` is therefore an
allow-list of affirmative spellings, and anything unrecognised is off.

**The record carries a count, not the citations.** The log answers "what do
people keep asking that we cannot answer", which needs the words and the branch.
Revision ids would make it a second, unexported, uncited index into canon.
"""

import datetime as _dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from adopt_ask.branch import Answer

__all__ = ["QuestionLog", "QuestionRecord", "log_question", "should_log"]

#: Affirmative spellings. Everything else -- including `0`, an empty value, a
#: typo and an absent key -- is off.
_ENABLED: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class QuestionRecord:
    """One passively logged question. Never canon, never exported."""

    scope_ref: str
    question: str
    branch: str
    citations: int
    asked_at: _dt.datetime


class QuestionLog(Protocol):
    """The annex write port, declared here and realized in `adopt_store.annex`.

    Structural, like `SearchRecords` and `EscalationWriter`: `adopt_ask` names no
    dialect and imports no driver.
    """

    def append_question(self, record: QuestionRecord) -> str:
        """Store one question. Returns the row id."""
        ...


def should_log(resolved: Mapping[str, str | None], *, key: str) -> bool:
    """Whether passive logging is switched on, given resolved configuration.

    Args:
        resolved: Config key -> resolved value, from `adopt_cli.config`. A
            mapping rather than the config objects themselves so this package
            stays free of `adopt_cli`, which depends on `adopt_store`.
        key: The config key to read. Passed rather than hard-coded here because
            the key's name is `adopt_cli`'s registry entry and the constant
            behind it is `adopt_const`'s -- this module is the rule, not the
            registry.

    Returns:
        `True` only for an explicit affirmative value. A missing key, an empty
        value, `0`, and anything unrecognised are all `False`.
    """
    value = resolved.get(key)
    return value is not None and value.strip().lower() in _ENABLED


def log_question(
    log: QuestionLog, answer: Answer, *, scope_ref: str, asked_at: _dt.datetime
) -> str:
    """Append `answer`'s question to the annex log. Returns the row id.

    Call this only where `should_log` returned `True`. Like `escalate`, it
    stores the text unconditionally once called: a logger that decided for
    itself whether to record would put the switch in two places, and the second
    one is the one nobody finds when asking why a store has questions in it.
    """
    return log.append_question(
        QuestionRecord(
            scope_ref=scope_ref,
            question=answer.question,
            branch=answer.branch,
            citations=len(answer.citations),
            asked_at=asked_at,
        )
    )
