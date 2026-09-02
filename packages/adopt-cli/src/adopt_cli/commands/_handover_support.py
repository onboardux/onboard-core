"""The composition root for `adopt handover`: store rows in, facade calls out.

`adopt_handover` holds every rule that could be wrong -- the step order, the
fold, the digest, the agenda, what a valid checklist is -- and holds no dialect,
because `no-raw-sqlite` names it a source module. This module is where those
rules meet a real store, and it contains no SQL of its own: CR-36's exemption
used exactly as intended, the same way `_pack_support` uses it.

**Every write here is one transaction over one open connection.** The handle
caches its facades, and `SqliteStore.transaction` joins a nested use to the
outermost one -- so `close_handover`'s assignment, gap dispositions, audit row
and value events commit together or not at all. That is not a docstring claim:
`tests/unit/test_handover_transfer.py` asserts the rollback by row count.
"""

import datetime as _dt
from pathlib import Path
from typing import Any, Final, cast

from adopt_handover import (
    HANDOVER_CLOSED,
    HANDOVER_STARTED,
    HandoverRecord,
    OpenQuestion,
    PackConflict,
    PackGap,
    current,
    encode_detail,
    fold,
    is_open_gap,
)

from adopt_model import (
    AssignmentReason,
    AuditEvent,
    OwnershipAssignment,
    OwnershipScope,
    ValueEvent,
)
from adopt_obs import AdoptError, ErrorCode, new_id
from adopt_scope import Scope

__all__ = [
    "HANDOVER_VALUE_EVENT",
    "ResolvedSystem",
    "close_handover",
    "current_owner_of",
    "open_conflicts",
    "open_gaps",
    "open_questions",
    "opening_position",
    "records_for",
    "refuse_if_replica",
    "require_open",
    "resolve_system",
    "write_event",
    "write_value_event",
]

#: `value_event.event_type` for an emitted pack. The vocabulary is the
#: manifest's `value_event_type`, which has carried this value since Build 0 and
#: has had no writer until now.
HANDOVER_VALUE_EVENT = "handover.generated"

#: `ownership_assignment.assignment_reason` for the transfer. The plane's
#: activation deliberately writes `policy` and names this value as "B9's verb"
#: -- this is that verb.
_TRANSFER_REASON: Final[AssignmentReason] = "handover"

#: The transfer is **system**-scoped. `current_owner` prefers the narrowest
#: active assignment, so a system-scoped row is what makes the receiving owner
#: win over whoever owns the engagement -- which is the whole point of a
#: handover that transfers one system out of a wider engagement.
_TRANSFER_SCOPE: Final[OwnershipScope] = "system"


class ResolvedSystem:
    """The frozen target of a handover: one system, optionally one environment."""

    __slots__ = ("environment_id", "firm_id", "scope", "scope_path", "slug", "system_id")

    def __init__(self, *, scope: Scope, scope_path: str, system_id: str, slug: str) -> None:
        self.scope = scope
        self.scope_path = scope_path
        self.system_id = system_id
        self.slug = slug
        self.firm_id = scope.firm.id
        self.environment_id = scope.environment.id if scope.environment is not None else None


def resolve_system(handle: Any, *, system: str | None, scope: str | None) -> ResolvedSystem:
    """The system this handover is about.

    `--scope` resolves the full chain and narrows to an environment when one is
    named; `--system` takes a system id or slug and freezes the **whole**
    system, which `recompute_coverage`, the pack read port and the boundary read
    already interpret as every environment.

    Raises:
        AdoptError: ``SCOPE_VIOLATION`` when neither names a system, when a slug
            matches no system, or when it matches several. Refused rather than
            guessed for `resolve_scope`'s reason: writing an ownership transfer
            against the wrong system is not a mistake anybody notices quickly.
    """
    from adopt_cli.commands._map_support import _rows, resolve_scope
    from adopt_model import Engagement, Firm, System

    if system is not None and scope is not None:
        raise AdoptError(
            ErrorCode.SCOPE_VIOLATION,
            message="pass --system or --scope, not both",
            hint="They name the same thing two ways, and a disagreement between "
            "them would have to be resolved by argument order.",
        )

    if system is None:
        resolved = resolve_scope(handle, scope)
        if resolved.system is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message="a handover is about one system, and this scope names none",
                hint="Pass --system <id|slug>, or --scope firm/engagement/system"
                "[/environment]. A handover of an engagement is a handover of "
                "each of its systems, which is several events rather than one.",
            )
        return ResolvedSystem(
            scope=resolved,
            scope_path=resolved.path(),
            system_id=str(resolved.system.id),
            slug=str(resolved.system.slug),
        )

    systems = _rows(handle, "system", System)
    matches = [row for row in systems if row.id == system or row.slug == system]
    if not matches:
        raise AdoptError(
            ErrorCode.SCOPE_VIOLATION,
            message=f"no system {system!r} in this store",
            hint="`adopt store info` lists what this store holds. --system takes "
            "a system id or its slug.",
        )
    if len(matches) > 1:
        raise AdoptError(
            ErrorCode.SCOPE_VIOLATION,
            message=f"{len(matches)} systems share the slug {system!r}",
            hint="Slugs are unique within an engagement, not across a store. "
            "Pass --scope firm/engagement/system to say which, or --system "
            "with the system id.",
        )

    found = matches[0]
    engagements = {row.id: row for row in _rows(handle, "engagement", Engagement)}
    firms = {row.id: row for row in _rows(handle, "firm", Firm)}
    engagement = engagements.get(found.engagement_id)
    firm = firms.get(engagement.firm_id) if engagement is not None else None
    if engagement is None or firm is None:
        raise AdoptError(
            ErrorCode.SCOPE_VIOLATION,
            message=f"the scope chain above system {found.slug!r} is incomplete",
            hint="`adopt store doctor` reports a broken scope chain.",
        )

    path = f"{firm.slug}/{engagement.slug}/{found.slug}"
    return ResolvedSystem(
        scope=handle.scope().resolve(path),
        scope_path=path,
        system_id=str(found.id),
        slug=str(found.slug),
    )


def refuse_if_replica(store_override: Path | None) -> None:
    """Refuse a writing handover verb against a store `adopt pull` maintains.

    The third of the replica rules, and the quietest: every writing step lands
    canon -- the ownership assignment, the escalations a failed task opens, the
    gap dispositions, the audit trail that **is** the acceptance record -- into a
    file the next `adopt pull` replaces wholesale. An entire engagement closure
    would vanish with no trace it had existed.

    Raises:
        AdoptError: ``HANDOVER_TARGET_IS_REPLICA``.
    """
    from adopt_cli.replica import read_marker
    from adopt_cli.store_option import configured_store_path

    store = configured_store_path(store_override)
    marker = read_marker(store)
    if marker is None:
        return
    raise AdoptError(
        ErrorCode.HANDOVER_TARGET_IS_REPLICA,
        message=f"{store} is a replica of {marker.system_id} on {marker.plane_url}",
        hint="R9 makes the plane the sole writer of an operated system's canon, "
        "and a handover writes canon: ownership, escalations, gap dispositions "
        "and the acceptance trail. The next `adopt pull` would replace all of "
        "it. The operated handover -- the plane recording acceptance for both "
        "parties -- is not built yet, so run this against the field store that "
        "holds the canon, before activation.",
    )


def now_of(handle: Any) -> _dt.datetime:
    """The store's clock when it has one, so a seeded test gets seeded stamps."""
    clock = getattr(handle, "clock", None)
    if clock is not None:
        # `cast` rather than `refresh._now`'s untyped signature: the handle is
        # structural here, so the clock is `Any`, and dropping the return type
        # would let a caller pass this timestamp somewhere no type checks it.
        return cast("_dt.datetime", clock.now())
    from adopt_obs import SystemClock

    return SystemClock().now()


def records_for(handle: Any, system_id: str) -> tuple[HandoverRecord, ...]:
    """Every handover of this system, newest first."""
    from adopt_handover import HANDOVER_EVENT_TYPES

    rows = handle.operations_records().list_audit_events(event_types=HANDOVER_EVENT_TYPES)
    return fold(rows, system_id=system_id)


def require_open(handle: Any, system_id: str) -> HandoverRecord:
    """The open handover, or a refusal naming how to start one.

    Raises:
        AdoptError: ``HANDOVER_NOT_OPEN``.
    """
    record = current(records_for(handle, system_id))
    if record is None:
        raise AdoptError(
            ErrorCode.HANDOVER_NOT_OPEN,
            message=f"no open handover for system {system_id}",
            hint="`adopt handover start --system <id> --receiving-owner <group>` "
            "opens one. A closed handover is history and is not resumed: "
            "appending steps to an event both parties accepted would rewrite "
            "an acceptance. `adopt handover status` shows what this system has.",
        )
    return record


def write_event(
    handle: Any,
    *,
    event_type: str,
    system: ResolvedSystem,
    detail: dict[str, Any],
    actor: str | None,
    now: _dt.datetime,
    handover_id: str | None,
) -> AuditEvent:
    """Append one step to the trail. Returns the row, whose id opens a handover.

    `handover_id` is `None` only for `handover_started`, where the row's own id
    becomes the handover id and `subject_ref` therefore points at itself --
    which is what lets the fold group by `subject_ref` with no special case for
    the row that opens the group.
    """
    event_id = new_id("aud")
    row = AuditEvent(
        id=event_id,
        firm_id=system.firm_id,
        system_id=system.system_id,
        event_type=event_type,
        actor_id=actor,
        subject_ref=handover_id or event_id,
        detail=encode_detail(detail),
        occurred_at=now,
    )
    handle.operations_records().insert_audit_event(row)
    return row


def write_value_event(
    handle: Any,
    *,
    system: ResolvedSystem,
    source_ref: str | None,
    actor: str | None,
    now: _dt.datetime,
) -> None:
    """One `handover.generated` ledger entry per emitted pack.

    `measured` rather than `modelled`: a pack was produced and its digest is the
    evidence. `minutes` is deliberately absent -- correction effort is
    `handover.correction_minutes`, which waits for the measurement that would
    give it a real value rather than being filled with a guess.
    """
    handle.operations_records().insert_value_event(
        ValueEvent(
            id=new_id("ve"),
            system_id=system.system_id,
            occurred_at=now,
            event_type=HANDOVER_VALUE_EVENT,
            minutes=None,
            actor_id=actor,
            source_ref=source_ref,
            confidence_label="measured",
        )
    )


def current_owner_of(handle: Any, *, system_id: str, at: _dt.datetime) -> str | None:
    """Who owns the system at `at`, by the narrowest active assignment."""
    owner = handle.operations_records().current_owner(system_id=system_id, at=at)
    return None if owner is None else str(owner.actor_or_group_id)


def _coverage(handle: Any, system: ResolvedSystem) -> Any:
    from adopt_coverage import recompute_coverage

    return recompute_coverage(handle.coverage_records(), system.system_id, system.environment_id)


def open_gaps(
    handle: Any, *, system: ResolvedSystem, now: _dt.datetime, coverage: Any = None
) -> tuple[PackGap, ...]:
    """Derived gaps joined to their dispositions, minus the settled ones.

    The join is `_pack_support.build_gaps` -- the same rows the pack's gap
    appendix renders -- so the agenda and the pack can never disagree about what
    is outstanding. Existence stays derived: nothing here can invent a gap
    `recompute_coverage` did not produce.
    """
    from adopt_knowledge import rank_gaps

    from adopt_cli.commands._pack_support import build_gaps

    result = coverage if coverage is not None else _coverage(handle, system)
    joined = build_gaps(rank_gaps(result.identities), handle.governance().gap_dispositions())
    return tuple(gap for gap in joined if is_open_gap(gap, now=now))


def open_conflicts(
    handle: Any, *, system: ResolvedSystem, coverage: Any = None
) -> tuple[PackConflict, ...]:
    """Open conflicts for the identities in scope (Bet 4's deliverable)."""
    from adopt_cli.commands._pack_support import build_conflicts

    result = coverage if coverage is not None else _coverage(handle, system)
    uris = {row.identity_id: row.uri for row in result.identities}
    return build_conflicts(handle, uris=uris)


def open_questions(handle: Any, *, system_id: str) -> tuple[OpenQuestion, ...]:
    """Unanswered questions for this system, newest first."""
    return tuple(
        OpenQuestion(id=str(row.id), owner_actor_id=row.owner_actor_id)
        for row in handle.governance().escalations(system_id=system_id, status="open")
    )


def opening_position(handle: Any, *, system: ResolvedSystem, now: _dt.datetime) -> dict[str, Any]:
    """The counts frozen at `start` -- what this handover began from.

    Recorded because the event's value is proportional to the state that
    accumulated before it (v6.1 §6's sales-motion note), and a closure that
    cannot say where it started cannot say what it added.
    """
    coverage = _coverage(handle, system)
    identities = len(coverage.identities)
    covered = sum(1 for row in coverage.identities if row.covered)

    confirmed = 0
    unverified = 0
    for _item, revision in handle.pack_records().knowledge_heads(
        system_id=system.system_id, environment_id=system.environment_id
    ):
        if str(revision.verification or "") == "verified":
            confirmed += 1
        else:
            unverified += 1

    return {
        "identities": identities,
        "covered": covered,
        "confirmed_items": confirmed,
        "unverified_items": unverified,
        "open_gaps": len(open_gaps(handle, system=system, now=now, coverage=coverage)),
        "open_conflicts": len(open_conflicts(handle, system=system, coverage=coverage)),
        "open_questions": len(open_questions(handle, system_id=system.system_id)),
        "current_owner": current_owner_of(handle, system_id=system.system_id, at=now),
    }


def close_handover(
    handle: Any,
    record: HandoverRecord,
    *,
    system: ResolvedSystem,
    accepted_by: str,
    actor: str | None,
    now: _dt.datetime,
) -> dict[str, Any]:
    """Transfer ownership and close the event. One transaction, or nothing.

    In order, inside one unit of work:

    1. the receiving owner's **system-scoped** assignment is written;
    2. the prior *system-scoped* assignment, if there was one, is ended --
       engagement-scoped rows are left alone, because `current_owner` already
       prefers the narrower row and closing a wider assignment would silently
       un-own every other system under that engagement;
    3. every open gap **nobody owns** is dispositioned `acknowledged` to the
       receiving owner; a gap that already names an owner keeps them, because a
       named owner is a decision somebody made and a transfer is not a reason to
       unmake it;
    4. the closing row is appended;
    5. **the honest post-check** -- `current_owner` is asked the same question
       the escalation router asks, of the same port, and must answer with the
       receiving owner. It runs inside this transaction, so its refusal rolls
       every write above back.

    Nothing here resolves a gap, answers a question or clears a conflict. The
    honesty rule is that unresolved items **transfer with owners** rather than
    being closed to look complete, and there is deliberately no code path in
    this build that could close one.

    Raises:
        AdoptError: ``HANDOVER_UNOWNED`` when the receiving owner is blank, or
            when `current_owner` does not resolve to them afterwards.
    """
    from adopt_handover import require

    require(record, HANDOVER_CLOSED)

    receiving_owner = record.receiving_owner.strip()
    if not receiving_owner:
        raise AdoptError(
            ErrorCode.HANDOVER_UNOWNED,
            message=f"handover {record.id} names no receiving owner",
            hint="The event cannot close leaving the system unowned. Start a new "
            "handover with --receiving-owner naming the group that will answer "
            "for this system.",
        )

    operations = handle.operations_records()
    with handle.backend.transaction():
        prior = operations.current_owner(system_id=system.system_id, at=now)
        assignment = OwnershipAssignment(
            id=new_id("own"),
            system_id=system.system_id,
            engagement_id=None,
            scope=_TRANSFER_SCOPE,
            actor_or_group_id=receiving_owner,
            is_group=record.is_group,
            effective_from=now,
            assigned_by=actor,
            assignment_reason=_TRANSFER_REASON,
        )
        operations.insert_assignment(assignment)

        closed_prior: str | None = None
        if prior is not None and prior.system_id == system.system_id:
            operations.close_assignment(str(prior.id), effective_to=now)
            closed_prior = str(prior.id)

        gaps = open_gaps(handle, system=system, now=now)
        assigned: list[str] = []
        kept: list[str] = []
        for gap in gaps:
            if gap.owner_actor_id:
                kept.append(gap.gap_key)
                continue
            handle.governance().dispose_gap(
                gap_key=gap.gap_key,
                identity_id=_identity_of(handle, gap, system=system),
                status="acknowledged",
                owner_actor_id=receiving_owner,
                note=f"transferred at handover {record.id}",
            )
            assigned.append(gap.gap_key)

        questions = open_questions(handle, system_id=system.system_id)
        conflicts = open_conflicts(handle, system=system)
        transferred = {
            "gaps_assigned": sorted(assigned),
            "gaps_kept_owner": sorted(kept),
            "questions": sorted(question.id for question in questions),
            "conflicts": len(conflicts),
        }

        detail = {
            "accepted_by": accepted_by,
            "ownership_assignment_id": assignment.id,
            "closed_prior_assignment_id": closed_prior,
            "bundle_digest": record.snapshot.get("bundle_digest"),
            "transferred": transferred,
            "unverified_sections_total": sum(
                int(pack.get("unverified_sections", 0) or 0) for pack in record.packs
            ),
        }
        write_event(
            handle,
            event_type=HANDOVER_CLOSED,
            system=system,
            detail=detail,
            actor=actor,
            now=now,
            handover_id=record.id,
        )

        # The honest check, inside the transaction that would have to roll back.
        owner_now = operations.current_owner(system_id=system.system_id, at=now)
        if owner_now is None or str(owner_now.actor_or_group_id) != receiving_owner:
            raise AdoptError(
                ErrorCode.HANDOVER_UNOWNED,
                message=(
                    f"after writing the transfer, {system.scope_path!r} resolves "
                    f"{'nobody' if owner_now is None else owner_now.actor_or_group_id!r} "
                    f"rather than {receiving_owner!r}"
                ),
                hint="The assignment was written and the ownership question still "
                "answers somebody else, so routing would not reach the receiving "
                "team. The close has been rolled back. This is a defect in the "
                "assignment's shape rather than in what you typed -- report it "
                "with the scope path.",
            )

    detail["transferred"] = transferred
    return detail


def _identity_of(handle: Any, gap: PackGap, *, system: ResolvedSystem) -> str:
    """The identity id behind a derived gap.

    `PackGap` carries the URI rather than the id, because the pack renders URIs.
    `dispose_gap` needs the id for its foreign key, so it is looked up here from
    the same recompute the gap came from.
    """
    for row in _coverage(handle, system).identities:
        if row.uri == gap.uri:
            return str(row.identity_id)
    raise AdoptError(  # pragma: no cover -- the gap came from this same recompute
        ErrorCode.GAP_NOT_FOUND,
        message=f"no identity behind gap {gap.gap_key!r}",
        hint="The gap was derived from this store's own coverage a moment ago, "
        "so this is a defect rather than a state you can fix.",
    )


def record_detail_for_status(record: HandoverRecord) -> list[dict[str, Any]]:
    """The per-step rows `adopt handover status` prints."""
    from adopt_handover import STEP_ORDER, VERB_FOR

    rows: list[dict[str, Any]] = []
    done = set(record.steps_done)
    for step in STEP_ORDER:
        taken_at = record.step_taken_at(step)
        rows.append(
            {
                "step": step,
                "verb": VERB_FOR[step],
                "done": step in done,
                "at": None if taken_at is None else taken_at.isoformat(),
                "by": record.step_taken_by(step),
            }
        )
    return rows


def started_detail(
    *, system: ResolvedSystem, receiving_owner: str, is_group: bool, opening: dict[str, Any]
) -> dict[str, Any]:
    """`handover_started`'s detail: the frozen scope and the opening position."""
    return {
        "scope": system.scope_path,
        "environment_id": system.environment_id,
        "receiving_owner": receiving_owner,
        "is_group": is_group,
        "opening": opening,
    }


def has_open_handover(handle: Any, system_id: str) -> HandoverRecord | None:
    """The open record if there is one -- `start`'s precondition."""
    return current(records_for(handle, system_id))


HANDOVER_START_EVENT = HANDOVER_STARTED
