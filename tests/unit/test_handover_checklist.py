"""The verification checklist: what a valid round is, and what a failure becomes.

*Fails when* an invalid checklist is accepted, or a failed task stops carrying
the identity the FDE named. *Matters because* step 4's whole promise is that
"failures become gaps, not shame" -- a failure that reached the store without its
text is an unanswerable row, and one that reached it without its URI makes
`adopt answer` bind to nothing. *No other instrument catches it because* the file
is authored by hand in a room, so every mistake in it is a human's, made once,
under time pressure, and the only thing standing between that and a corrupted
record is this validation.
"""

from typing import Any

import pytest
from adopt_handover import Checklist, Task, counts, failures, parse_checklist, question_for

from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit

_URI = "onboard-v1://northwind/acme-erp/orders-api/prod/config_key/-/ORDERS_API_KEY"


def _document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "audience": "client_ops",
        "tasks": [
            {"id": "rotate-key", "task": "Rotate the orders API key", "outcome": "pass"},
            {
                "id": "find-approval",
                "task": "Explain why refunds need approval",
                "outcome": "fail",
                "uri": _URI,
                "note": "The pack names the key but not where it is stored",
                "performed_by": "bob",
            },
            {"id": "run-backfill", "task": "Run the nightly backfill", "outcome": "skipped"},
        ],
    }
    document.update(overrides)
    return document


def test_a_valid_checklist_parses_with_the_default_audience_applied() -> None:
    """Per-task fields win; the document's audience fills in the rest."""
    checklist = parse_checklist(_document())

    assert [task.id for task in checklist.tasks] == ["rotate-key", "find-approval", "run-backfill"]
    assert checklist.default_audience == "client_ops"
    assert all(task.audience == "client_ops" for task in checklist.tasks)
    assert counts(checklist) == (1, 1, 1)

    failed = checklist.tasks[1]
    assert failed.uri == _URI
    assert failed.performed_by == "bob"
    assert failed.note is not None


def test_a_per_task_audience_overrides_the_documents() -> None:
    """A round that verified two packs records which pack each task was against."""
    document = _document()
    document["tasks"][0]["audience"] = "technical"

    checklist = parse_checklist(document)

    assert checklist.tasks[0].audience == "technical"
    assert checklist.tasks[1].audience == "client_ops"


@pytest.mark.parametrize(
    ("mutate", "because"),
    [
        (lambda d: d.pop("tasks"), "no tasks list at all"),
        (lambda d: d.update(tasks=[]), "an empty tasks list"),
        (lambda d: d.update(tasks="rotate the key"), "a string where the list should be"),
        (lambda d: d["tasks"][0].pop("id"), "a task with no id"),
        (lambda d: d["tasks"][0].update(id="   "), "a blank id"),
        (lambda d: d["tasks"][0].pop("task"), "a task with no text"),
        (lambda d: d["tasks"][0].update(outcome="partly"), "an outcome outside the three"),
        (lambda d: d["tasks"][0].pop("outcome"), "a task with no outcome"),
        (lambda d: d["tasks"][1].update(id="rotate-key"), "a duplicate id"),
        (lambda d: d["tasks"][1].update(uri=["a", "b"]), "a non-string uri"),
        (lambda d: d.update(audience=7), "a non-string audience"),
        (lambda d: d.update(tasks=["rotate the key"]), "a task that is not a mapping"),
    ],
)
def test_an_invalid_checklist_is_refused_by_name(mutate: Any, because: str) -> None:
    """Every refusal is the one code, and every message names what to fix."""
    document = _document()
    mutate(document)

    with pytest.raises(AdoptError) as raised:
        parse_checklist(document)

    assert raised.value.code is ErrorCode.HANDOVER_CHECKLIST_INVALID, because
    assert raised.value.message, because
    assert raised.value.hint, because


def test_failures_lists_only_failed_tasks_in_authored_order() -> None:
    """Passed and skipped tasks raise no question; a skipped task is not a failure."""
    checklist = parse_checklist(_document())

    failed = failures(checklist)

    assert [task.id for task in failed] == ["find-approval"]


def test_a_failed_task_becomes_a_question_carrying_its_identity_and_note() -> None:
    """The question is what `adopt answer` binds an answer to."""
    checklist = parse_checklist(_document())
    task = failures(checklist)[0]

    question = question_for(task)

    assert "find-approval" in question
    assert "Explain why refunds need approval" in question
    assert _URI in question, "the answer would bind to nothing without it"
    assert "not where it is stored" in question


def test_a_question_without_an_identity_is_still_answerable() -> None:
    """The negative control: a URI is optional, and its absence is not an error."""
    task = Task(id="t1", task="Find the runbook", outcome="fail")

    question = question_for(task)

    assert "Find the runbook" in question
    assert "identity:" not in question


def test_an_all_passing_round_raises_nothing() -> None:
    """The whole point of a green round: no escalation is opened."""
    checklist = Checklist(
        tasks=(
            Task(id="a", task="one", outcome="pass"),
            Task(id="b", task="two", outcome="pass"),
        )
    )

    assert failures(checklist) == ()
    assert counts(checklist) == (2, 0, 0)
