"""The verification checklist: what the receiving team was asked to do, and how it went.

v6.1 §6 Build 9 step 4: *"the receiving team completes a recorded task checklist
against the pack; failures become gaps, not shame."*

Both halves of that sentence are decisions this module encodes.

**A checklist is data an FDE authors and a room fills in**, not a form the
product generates. The tasks are the realistic things the new owners must be
able to do -- rotate a key, run a backfill, find out why refunds need approval --
and only a human knows which those are for this system. So this module parses
and validates; it never invents a task.

**A failure is a question, not a verdict.** `question_for` renders a failed task
as the open question it actually is: *the pack could not carry somebody through
this*. The caller records it as an `escalation` -- the same row `adopt ask
--escalate` writes -- so `adopt answer` banks the answer in the room, and any
question still open at close transfers with a named owner. Nothing here writes a
score, and there is deliberately no place to record who failed a task: the
product's finding is about the pack, and a checklist that graded people would be
a checklist nobody volunteered to sit.

`performed_by` is recorded per task because a handover's audit answer to *who
verified this* is a real question -- but it is optional, and it names the person
who **did** the task rather than the person who failed it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "OUTCOMES",
    "OUTCOME_FAIL",
    "OUTCOME_PASS",
    "OUTCOME_SKIPPED",
    "Checklist",
    "Task",
    "counts",
    "failures",
    "parse_checklist",
    "question_for",
]

# `S105` reads the literal `"pass"` as a hardcoded credential. It is the
# outcome a receiving team records against a verification task, and the word
# is the one they write in the YAML -- renaming the constant does not move the
# rule, which looks at the value. Waived here, with the reason, rather than
# spelling the outcome something nobody would type.
OUTCOME_PASS: Final[str] = "pass"  # noqa: S105
OUTCOME_FAIL: Final[str] = "fail"
OUTCOME_SKIPPED: Final[str] = "skipped"

#: The three outcomes a task can carry. Closed on purpose: "partly" and
#: "with help" are the readings that turn a verification into a negotiation, and
#: a task somebody needed help with is a task the pack did not carry -- which is
#: `fail`, and which is a finding rather than a judgement.
OUTCOMES: Final[frozenset[str]] = frozenset({OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_SKIPPED})


@dataclass(frozen=True, slots=True)
class Task:
    """One thing the receiving team was asked to do, and what happened."""

    id: str
    task: str
    outcome: str
    #: The identity the task is about, when the FDE named one. Carried into the
    #: question so the answer binds to the right referent in one step.
    uri: str | None = None
    note: str | None = None
    performed_by: str | None = None
    audience: str | None = None


@dataclass(frozen=True, slots=True)
class Checklist:
    """A verification round, as authored and filled in."""

    tasks: tuple[Task, ...]
    default_audience: str | None = None


def _invalid(message: str, hint: str) -> AdoptError:
    return AdoptError(ErrorCode.HANDOVER_CHECKLIST_INVALID, message=message, hint=hint)


def parse_checklist(document: Mapping[str, Any]) -> Checklist:
    """Validate a parsed checklist document and return it as a `Checklist`.

    The caller reads the YAML (the CLI owns that boundary, as it does for probe
    manifests); this owns what a valid checklist *is*.

    Raises:
        AdoptError: ``HANDOVER_CHECKLIST_INVALID`` for a document with no
            `tasks` list, an empty one, a task missing `id` or `task`, a
            duplicate id, an unknown `outcome`, or a non-string `uri`. Each
            message names the offending task, because a validation error that
            says only "invalid" sends somebody reading a forty-task file by eye.
    """
    raw_tasks = document.get("tasks")
    if not isinstance(raw_tasks, Sequence) or isinstance(raw_tasks, str | bytes):
        raise _invalid(
            "the checklist has no `tasks` list",
            "A checklist is a mapping with a `tasks:` list. See "
            "`docs/handover-checklist.example.yaml` for the shape.",
        )
    if not raw_tasks:
        raise _invalid(
            "the checklist lists no tasks",
            "A verification round with no tasks would record that the receiving "
            "team was asked nothing, which is not a round. Add the realistic "
            "tasks they must be able to perform from the pack alone.",
        )

    default_audience = document.get("audience")
    if default_audience is not None and not isinstance(default_audience, str):
        raise _invalid(
            "the checklist's `audience` is not a string",
            "`audience` names which pack the tasks were performed against, e.g. "
            "`client_ops`. Omit it to leave the tasks unattributed.",
        )

    tasks: list[Task] = []
    seen: set[str] = set()
    for position, entry in enumerate(raw_tasks, start=1):
        tasks.append(_parse_task(entry, position=position, seen=seen, audience=default_audience))
    return Checklist(tasks=tuple(tasks), default_audience=default_audience)


def _parse_task(entry: object, *, position: int, seen: set[str], audience: str | None) -> Task:
    if not isinstance(entry, Mapping):
        raise _invalid(
            f"task {position} is not a mapping",
            "Every entry under `tasks:` is a mapping with at least `id`, `task` and `outcome`.",
        )

    task_id = entry.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise _invalid(
            f"task {position} has no `id`",
            "Every task needs a short stable id -- it is what the recorded "
            "outcome and any question raised from it are keyed on.",
        )
    task_id = task_id.strip()
    if task_id in seen:
        raise _invalid(
            f"task id {task_id!r} appears twice",
            "Ids key the recorded outcomes, so two tasks sharing one would "
            "leave the record showing whichever was read last.",
        )
    seen.add(task_id)

    text = entry.get("task")
    if not isinstance(text, str) or not text.strip():
        raise _invalid(
            f"task {task_id!r} has no `task` text",
            "The text is what the receiving team was asked to do, and it "
            "becomes the open question if they could not do it.",
        )

    outcome = entry.get("outcome")
    if not isinstance(outcome, str) or outcome not in OUTCOMES:
        raise _invalid(
            f"task {task_id!r} has outcome {outcome!r}",
            f"An outcome is one of {sorted(OUTCOMES)}. A task somebody needed "
            "help with is `fail`: the finding is about the pack, not the person.",
        )

    uri = entry.get("uri")
    if uri is not None and not isinstance(uri, str):
        raise _invalid(
            f"task {task_id!r} has a non-string `uri`",
            "`uri` is one canonical identity URI, so an answer captured from "
            "this task binds to the right referent.",
        )

    note = entry.get("note")
    performed_by = entry.get("performed_by")
    return Task(
        id=task_id,
        task=text.strip(),
        outcome=outcome,
        uri=uri.strip() if isinstance(uri, str) and uri.strip() else None,
        note=str(note) if note is not None else None,
        performed_by=str(performed_by) if performed_by is not None else None,
        audience=str(entry["audience"]) if isinstance(entry.get("audience"), str) else audience,
    )


def failures(checklist: Checklist) -> tuple[Task, ...]:
    """The failed tasks, in the order they were authored."""
    return tuple(task for task in checklist.tasks if task.outcome == OUTCOME_FAIL)


def counts(checklist: Checklist) -> tuple[int, int, int]:
    """`(passed, failed, skipped)`."""
    outcomes = [task.outcome for task in checklist.tasks]
    return (
        outcomes.count(OUTCOME_PASS),
        outcomes.count(OUTCOME_FAIL),
        outcomes.count(OUTCOME_SKIPPED),
    )


def question_for(task: Task) -> str:
    """The open question a failed task becomes.

    Phrased as what it is -- something the pack could not carry a competent
    stranger through -- and carrying the identity URI when the FDE named one, so
    whoever answers it knows which referent to bind the answer to.
    """
    question = f"Verification task {task.id} failed: {task.task}"
    if task.uri:
        question = f"{question} (identity: {task.uri})"
    if task.note:
        question = f"{question} -- {task.note}"
    return question
