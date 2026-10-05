"""Is each exit test the receiving team passed still true? -- independence status.

**The drill proves something on a date, and the system keeps moving.** `adopt
handover verify` records that the receiving team performed a task without the
builder. Until 2026-10-05 that record never changed again: rename the endpoint a
passed task was performed against, and the runbook went STALE while `handover
status` and `acceptance.json` still said "2 passed" (independence transcript
T8). An exit drill whose result cannot go stale is a certificate, and the one
thing this product sells is proof that knows when it stopped being true.

**What a task is anchored to.** A checklist task may name one canonical `uri`
(`docs/handover-checklist.example.yaml`), and `verify` now records it beside the
outcome. That referent is what the drill exercised, so it is what can invalidate
the result. A task with no `uri` is reported **unanchored** -- never valid --
because a result nothing can contradict is not one this product can vouch for.

**The rules, in the order a reader would want the reason** (first match wins):

1. no `uri` recorded -> `unanchored`;
2. the referent no longer resolves, is `dead` or `moved` -> `invalidated`;
3. a non-cosmetic classification hit it after the round that passed it ->
   `invalidated`, naming the class (render-only changes never count -- H5);
4. a drifted probe contradicts knowledge bound to it -> `invalidated`;
5. load-bearing confirmed knowledge bound to it is `stale` -> `invalidated`
   (the runbook the team followed has changed underneath them);
6. that knowledge is `observation_stale` -> `degraded`: nothing says it broke,
   but the sensor that would say so has gone quiet;
7. otherwise `valid`.

**Pure, like the rest of this package.** The CLI reads the store and hands in
`ReferentFacts` per URI; nothing here opens a store, reads a clock or writes.
Only the latest outcome per task counts, across every verification round: a
task failed then passed after a runbook was written was passed then, and a task
passed then failed was failed then -- the later sitting is the newer evidence.
"""

import datetime as _dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from adopt_handover.event import HANDOVER_VERIFIED, HandoverRecord, decode_detail

__all__ = [
    "DEGRADED",
    "INVALIDATED",
    "UNANCHORED",
    "VALID",
    "Independence",
    "PassedTask",
    "ReferentFacts",
    "TaskValidity",
    "assess",
    "passed_tasks",
]

VALID: Final[str] = "valid"
INVALIDATED: Final[str] = "invalidated"
DEGRADED: Final[str] = "degraded"
UNANCHORED: Final[str] = "unanchored"

#: Classes that are not a change to what a drilled referent does: a render-only
#: edit (H5), and a referent's first appearance, which cannot be a change to a
#: referent the drill already exercised.
_COSMETIC: Final[frozenset[str]] = frozenset({"BINDING_INTACT_RENDER_ONLY", "UNBOUND_NEW"})


@dataclass(frozen=True, slots=True)
class PassedTask:
    """One task whose latest recorded outcome is `pass`."""

    task_id: str
    uri: str | None
    verified_at: _dt.datetime


@dataclass(frozen=True, slots=True)
class ReferentFacts:
    """What is true of one drilled referent now, as the caller read it."""

    #: Whether the URI names an identity in this store at all.
    found: bool
    #: Head status of that identity: `active`, `moved` or `dead`.
    status: str | None = None
    #: Every classification of that identity, `(impact_class, created_at)`.
    changes: tuple[tuple[str, _dt.datetime], ...] = ()
    #: Whether a drifted probe currently contradicts knowledge bound to it.
    contradicted: bool = False
    #: The deciding rule of stale, load-bearing, confirmed knowledge bound to it.
    stale_rule: str | None = None
    #: The deciding rule when that knowledge is only `observation_stale`.
    degraded_rule: str | None = None


@dataclass(frozen=True, slots=True)
class TaskValidity:
    """One passed task, and whether its result still holds."""

    task_id: str
    uri: str | None
    status: str
    cause: str | None
    verified_at: _dt.datetime
    #: When the invalidating change was recorded, where one is known.
    since: _dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class Independence:
    """Every passed task's current validity, in task order."""

    tasks: tuple[TaskValidity, ...]

    def count(self, status: str) -> int:
        return sum(1 for task in self.tasks if task.status == status)

    @property
    def passed(self) -> int:
        return len(self.tasks)

    @property
    def summary(self) -> str:
        """The line `adopt handover status` leads with."""
        if not self.tasks:
            return "no exit test has been passed by the receiving team yet"
        line = (
            f"{self.count(VALID)} of {self.passed} exit tests performed without the builder "
            "still valid"
        )
        extras = [
            f"{self.count(status)} {status}"
            for status in (INVALIDATED, DEGRADED, UNANCHORED)
            if self.count(status)
        ]
        return line + (f" ({', '.join(extras)})" if extras else "")


def passed_tasks(record: HandoverRecord) -> tuple[PassedTask, ...]:
    """The tasks whose latest outcome, across every round, is `pass`."""
    latest: dict[str, tuple[str, str | None, _dt.datetime]] = {}
    order: list[str] = []
    for row in record.all_of(HANDOVER_VERIFIED):
        outcomes = decode_detail(row.detail).get("outcomes")
        if not isinstance(outcomes, list):
            continue
        for outcome in outcomes:
            if not isinstance(outcome, dict) or not isinstance(outcome.get("id"), str):
                continue
            task_id = outcome["id"]
            uri = outcome.get("uri")
            if task_id not in latest:
                order.append(task_id)
            latest[task_id] = (
                str(outcome.get("outcome")),
                uri if isinstance(uri, str) else None,
                row.occurred_at,
            )
    return tuple(
        PassedTask(task_id=task_id, uri=latest[task_id][1], verified_at=latest[task_id][2])
        for task_id in order
        if latest[task_id][0] == "pass"
    )


def assess(record: HandoverRecord, facts: Mapping[str, ReferentFacts]) -> Independence:
    """Each passed task's validity now. `facts` is keyed by the tasks' URIs."""
    return Independence(tasks=tuple(_judge(task, facts) for task in passed_tasks(record)))


def _judge(task: PassedTask, facts: Mapping[str, ReferentFacts]) -> TaskValidity:
    def verdict(status: str, cause: str | None, since: _dt.datetime | None = None) -> TaskValidity:
        return TaskValidity(
            task_id=task.task_id,
            uri=task.uri,
            status=status,
            cause=cause,
            verified_at=task.verified_at,
            since=since,
        )

    if task.uri is None:
        return verdict(UNANCHORED, "no_uri_recorded")
    fact = facts.get(task.uri)
    if fact is None or not fact.found:
        return verdict(INVALIDATED, "identity_not_found")

    later = sorted(
        (at, impact)
        for impact, at in fact.changes
        if at > task.verified_at and impact not in _COSMETIC
    )
    first_change = later[0][0] if later else None

    if fact.status in ("dead", "moved"):
        return verdict(INVALIDATED, f"identity_{fact.status}", first_change)
    if later:
        return verdict(INVALIDATED, later[0][1], first_change)
    if fact.contradicted:
        return verdict(INVALIDATED, "contradicted_by_observation")
    if fact.stale_rule is not None:
        return verdict(INVALIDATED, fact.stale_rule)
    if fact.degraded_rule is not None:
        return verdict(DEGRADED, fact.degraded_rule)
    return verdict(VALID, None)
