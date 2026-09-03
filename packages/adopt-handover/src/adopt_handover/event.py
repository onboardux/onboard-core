"""The handover's recorded state: six audit events, folded into one record.

v6.1 §6 Build 9 asks for *"an orchestrated checklist with recorded state"* that
`adopt handover status` can render as *"every step recorded, resumable,
auditable"*. This module is that record, and the decision worth stating once is
where it lives: **the state is the store's own `audit_event` rows, and nothing
else.**

Why that table and not a new one:

* **The schema budget for Builds 1-10 is spent** (v6.1 §8: one table, and Build
  4 spent it on `coverage_gap`). A `handover_event` table would be re-litigating
  Build 0.
* **`audit_event` is append-only, exportable and firm-scoped**, and it is
  realized on both stores. So the handover's own history travels inside the
  client's acceptance bundle -- the snapshot carries the record of the steps
  that produced it -- and a plane-side handover later reads exactly these rows.
* **A sidecar state file beside the store would be a second record**, free to
  disagree with the store about what happened. The one thing this build must
  never do is let two answers exist to *what was handed over*.

**The handover id is the id of its own `handover_started` event.** No new ULID
prefix, nothing to register in contracts §1.1, and the id sorts in time like
every other id in the product. Every later row carries it in `subject_ref`,
including the start row, which therefore has `id == subject_ref` -- so grouping
by `subject_ref` needs no special case for the row that opens the group.

**Nothing here writes.** The module folds rows and answers questions about them;
the CLI's composition root performs the writes inside its own transaction. That
is the same split `assemble`/`render` keep, for the same reason: every rule that
can be got wrong is a pure function somebody can test without a database.
"""

import datetime as _dt
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from adopt_model import AuditEvent
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "HANDOVER_CLOSED",
    "HANDOVER_ELICITED",
    "HANDOVER_EVENT_TYPES",
    "HANDOVER_PACK_EMITTED",
    "HANDOVER_SNAPSHOT_TAKEN",
    "HANDOVER_STARTED",
    "HANDOVER_VERIFIED",
    "PREREQUISITE",
    "STEP_ORDER",
    "VERB_FOR",
    "HandoverRecord",
    "current",
    "decode_detail",
    "encode_detail",
    "fold",
    "require",
]

#: The six recorded steps, in the order v6.1 §6 Build 9 numbers them. The value
#: of each is its `audit_event.event_type`, spelled once here so no caller types
#: the string: `event_type` is free TEXT in the manifest (no CHECK is emitted),
#: which makes a typo a row that reads as a different step forever.
HANDOVER_STARTED: Final[str] = "handover_started"
HANDOVER_ELICITED: Final[str] = "handover_elicited"
HANDOVER_PACK_EMITTED: Final[str] = "handover_pack_emitted"
HANDOVER_VERIFIED: Final[str] = "handover_verified"
HANDOVER_SNAPSHOT_TAKEN: Final[str] = "handover_snapshot_taken"
HANDOVER_CLOSED: Final[str] = "handover_closed"

#: The steps in order. `next_step` walks this, and `steps_done` reports in it, so
#: the sequence a human reads is the sequence the rules enforce.
STEP_ORDER: Final[tuple[str, ...]] = (
    HANDOVER_STARTED,
    HANDOVER_ELICITED,
    HANDOVER_PACK_EMITTED,
    HANDOVER_VERIFIED,
    HANDOVER_SNAPSHOT_TAKEN,
    HANDOVER_CLOSED,
)

#: What `OperationsRecords.list_audit_events` is asked for. The same six strings
#: as `STEP_ORDER` and deliberately the same object: a second tuple is a second
#: place to forget a step, and the port's own docstring requires a caller to name
#: what it wants precisely so it cannot page a tenant's whole audit trail.
HANDOVER_EVENT_TYPES: Final[tuple[str, ...]] = STEP_ORDER

#: Step -> the step that must already be recorded. `handover_started` is absent
#: because its precondition is about the *system* (no open handover), not about
#: an earlier step -- a distinction the caller enforces with
#: `HANDOVER_ALREADY_OPEN`.
PREREQUISITE: Final[dict[str, str]] = {
    HANDOVER_ELICITED: HANDOVER_STARTED,
    HANDOVER_PACK_EMITTED: HANDOVER_ELICITED,
    HANDOVER_VERIFIED: HANDOVER_PACK_EMITTED,
    HANDOVER_SNAPSHOT_TAKEN: HANDOVER_VERIFIED,
    HANDOVER_CLOSED: HANDOVER_SNAPSHOT_TAKEN,
}

#: The verb an operator types for each step. Carried so a refusal can name the
#: command to run rather than an internal event type: a checklist that refuses
#: without saying what comes next is a checklist people work around.
VERB_FOR: Final[dict[str, str]] = {
    HANDOVER_STARTED: "adopt handover start",
    HANDOVER_ELICITED: "adopt handover elicit",
    HANDOVER_PACK_EMITTED: "adopt handover pack",
    HANDOVER_VERIFIED: "adopt handover verify",
    HANDOVER_SNAPSHOT_TAKEN: "adopt handover snapshot",
    HANDOVER_CLOSED: "adopt handover close",
}


def encode_detail(payload: dict[str, Any]) -> str:
    """`audit_event.detail` for one step. Sorted keys, no spaces, no escaping.

    The export writer's one rendering rule, spelled here as `sidecar.render_
    sidecar` spells it -- so a detail written on Windows and read on a Linux
    runner is the same string, and two runs recording the same facts produce the
    same bytes.

    **Ids, counts, keys, paths and digests only.** No client content reaches this
    column: a failed verification task's text lives on the `escalation` row,
    where the FDE's deliberate action put it, and the structured-log deny-list
    (`body`, `text`, `question`, ...) is the same posture one table over.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def decode_detail(raw: str | None) -> dict[str, Any]:
    """`audit_event.detail` back as a mapping; `{}` when there is nothing to read.

    **Never raises.** A detail that does not decode is reported by the record
    that holds it (`HandoverRecord.malformed`) and rendered by `adopt handover
    status`, rather than taken as a reason to refuse to describe the handover at
    all. The rows are append-only and written by one module, so a malformed
    detail means something outside this product edited the store -- which is
    exactly the moment an operator most needs `status` to still answer.
    """
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


@dataclass(frozen=True, slots=True)
class HandoverRecord:
    """One handover, folded from its own audit rows. Immutable, like the rows.

    `events` is every row of this handover, oldest first. Everything else is
    either frozen at `start` or derived by walking them, so there is no state
    here that could disagree with the store.
    """

    id: str
    system_id: str
    scope: str
    environment_id: str | None
    receiving_owner: str
    is_group: bool
    started_at: _dt.datetime
    started_by: str | None
    events: tuple[AuditEvent, ...]
    #: Ids of rows whose `detail` did not decode. Empty in every state this
    #: product can produce; rendered by `status` when it is not.
    malformed: tuple[str, ...] = field(default_factory=tuple)

    def all_of(self, event_type: str) -> tuple[AuditEvent, ...]:
        """Every row of one step, oldest first. A step may be re-run."""
        return tuple(row for row in self.events if row.event_type == event_type)

    def latest(self, event_type: str) -> AuditEvent | None:
        """The newest row of one step, or `None` if the step has not been taken.

        The newest rather than the first, because a second elicitation pass, a
        second verification round or a fresh snapshot supersedes the earlier one
        for reporting -- while both rows stay in the trail, which is the whole
        reason the trail is append-only.
        """
        rows = self.all_of(event_type)
        return rows[-1] if rows else None

    def detail_of(self, event_type: str) -> dict[str, Any]:
        """The newest row's decoded detail, or `{}`."""
        row = self.latest(event_type)
        return {} if row is None else decode_detail(row.detail)

    @property
    def steps_done(self) -> tuple[str, ...]:
        """The recorded steps, in `STEP_ORDER`."""
        taken = {row.event_type for row in self.events}
        return tuple(step for step in STEP_ORDER if step in taken)

    @property
    def is_closed(self) -> bool:
        return HANDOVER_CLOSED in self.steps_done

    @property
    def next_step(self) -> str | None:
        """The first step not yet recorded, or `None` when the event is closed."""
        done = set(self.steps_done)
        return next((step for step in STEP_ORDER if step not in done), None)

    @property
    def opening(self) -> dict[str, Any]:
        """The position frozen at `start`: counts, and who owned it then."""
        opening = self.detail_of(HANDOVER_STARTED).get("opening")
        return opening if isinstance(opening, dict) else {}

    @property
    def packs(self) -> tuple[dict[str, Any], ...]:
        """The newest emission per audience, ordered by audience.

        Per audience rather than the newest overall: `adopt handover pack` emits
        one row per audience in one run, so "the newest" would otherwise report
        a single pack and hide the other three.
        """
        newest: dict[str, dict[str, Any]] = {}
        for row in self.all_of(HANDOVER_PACK_EMITTED):
            detail = decode_detail(row.detail)
            audience = str(detail.get("audience", ""))
            newest[audience] = detail
        return tuple(newest[key] for key in sorted(newest))

    @property
    def verification_rounds(self) -> tuple[dict[str, Any], ...]:
        """Every recorded round, oldest first -- none of them superseded.

        Unlike packs and snapshots, a second verification round does not replace
        the first: it is a second sitting of the receiving team, and a record
        that showed only the last one could turn "they failed four tasks, then
        passed after we wrote the missing runbook" into "they passed".
        """
        return tuple(decode_detail(row.detail) for row in self.all_of(HANDOVER_VERIFIED))

    @property
    def snapshot(self) -> dict[str, Any]:
        return self.detail_of(HANDOVER_SNAPSHOT_TAKEN)

    @property
    def closure(self) -> dict[str, Any]:
        return self.detail_of(HANDOVER_CLOSED)

    def step_taken_at(self, event_type: str) -> _dt.datetime | None:
        row = self.latest(event_type)
        return None if row is None else row.occurred_at

    def step_taken_by(self, event_type: str) -> str | None:
        row = self.latest(event_type)
        return None if row is None else row.actor_id


def fold(events: Sequence[AuditEvent], *, system_id: str) -> tuple[HandoverRecord, ...]:
    """Every handover of one system, newest first.

    Rows are grouped by `subject_ref` -- the handover id -- and ordered within a
    group by `(occurred_at, id)`, so two steps recorded in the same millisecond
    still order by their ULIDs rather than by whatever the database returned.

    **A group with no `handover_started` row is skipped**, and the reason is
    worth stating rather than leaving as a silent `continue`: the start row is
    the only place the frozen scope, the receiving owner and the opening
    position exist, so there is no record to build without it. `audit_event` has
    no delete path anywhere in the product (`no_destructive_sql` forbids one), so
    this is not a state the product can reach -- and inventing a record with a
    blank scope would be worse than omitting it, because every later step would
    then operate on a scope nobody froze.

    Args:
        events: Audit rows. Types other than the six are ignored, so a caller may
            hand the whole trail; `HANDOVER_EVENT_TYPES` is the narrower ask.
        system_id: Rows for other systems are dropped. The port scopes reads to
            the tenant; this scopes them to the system being handed over.
    """
    grouped: dict[str, list[AuditEvent]] = {}
    for row in events:
        if row.event_type not in HANDOVER_EVENT_TYPES or row.system_id != system_id:
            continue
        key = row.subject_ref or row.id
        grouped.setdefault(key, []).append(row)

    records: list[HandoverRecord] = []
    for handover_id, rows in grouped.items():
        ordered = tuple(sorted(rows, key=lambda row: (row.occurred_at, row.id)))
        start = next((row for row in ordered if row.event_type == HANDOVER_STARTED), None)
        if start is None:
            continue

        detail = decode_detail(start.detail)
        malformed = tuple(
            row.id for row in ordered if row.detail is not None and not decode_detail(row.detail)
        )
        records.append(
            HandoverRecord(
                id=handover_id,
                system_id=system_id,
                scope=str(detail.get("scope", "")),
                environment_id=_optional_str(detail.get("environment_id")),
                receiving_owner=str(detail.get("receiving_owner", "")),
                is_group=bool(detail.get("is_group", True)),
                started_at=start.occurred_at,
                started_by=start.actor_id,
                events=ordered,
                malformed=malformed,
            )
        )

    records.sort(key=lambda record: (record.started_at, record.id), reverse=True)
    return tuple(records)


def current(records: Sequence[HandoverRecord]) -> HandoverRecord | None:
    """The open handover, or `None`.

    The newest un-closed record. A closed handover is history: a system handed
    over twice -- an engagement that came back, a second receiving team a year
    later -- gets a second record, and resuming the first would append steps to
    an event both parties already accepted.
    """
    return next((record for record in records if not record.is_closed), None)


def require(record: HandoverRecord, step: str) -> None:
    """Refuse `step` unless its prerequisite is recorded.

    Raises:
        AdoptError: ``HANDOVER_STEP_OUT_OF_ORDER``, naming the verb to run
            first. Steps may be **re-run** once their prerequisite holds -- a
            second elicitation pass, another verification round, a fresh
            snapshot -- so this checks the predecessor and never whether `step`
            itself has already happened.
    """
    prerequisite = PREREQUISITE.get(step)
    if prerequisite is None or prerequisite in record.steps_done:
        return
    raise AdoptError(
        ErrorCode.HANDOVER_STEP_OUT_OF_ORDER,
        message=(
            f"{VERB_FOR[step]} needs {VERB_FOR[prerequisite]} first; "
            f"handover {record.id} has reached {record.steps_done[-1]}"
        ),
        hint=(
            f"Run `{VERB_FOR[prerequisite]}` and then this again. "
            "`adopt handover status` lists every step, what has been recorded "
            "and what comes next."
        ),
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)
