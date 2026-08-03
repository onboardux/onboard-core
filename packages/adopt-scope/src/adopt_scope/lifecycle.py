"""Lifecycle transitions — and the guarantee that none of them is silent.

PRD F3.1: *a lifecycle transition is never silent.* Implementation spec §4.5
behaviour 3 states the mechanism: every `lifecycle_state` change writes a
`system_lifecycle_event` **in the same transaction**, and there is no code path
that changes state without one.

That guarantee is implemented by making this module the only caller of
`ScopeRecords.set_system_state`. The state write and the event write are adjacent
statements inside one transaction, so there is no ordering in which one lands
without the other — not "the caller should remember to log it", which is the
version of this rule that silently stops being true.

**Merges and splits write paired events** (F3.5, §4.5 behaviour 4). Both systems
receive an event and each names the other in `related_system_id`, so the history
reads correctly from either side. A one-sided merge is the failure this pairing
exists to prevent: the surviving system's log would show an absorption and the
absorbed system's log would show nothing at all.
"""

import datetime as _dt

from adopt_model import SystemLifecycleEvent
from adopt_model._enums import LifecycleState
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, new_id, truncate_to_millisecond
from adopt_scope.records import ScopeRecords

__all__ = ["LIFECYCLE_EVENT_PREFIX", "RETIRED_STATES", "transition"]

#: Contracts §1.1 prefix for `system_lifecycle_event`.
LIFECYCLE_EVENT_PREFIX: str = "sle"

#: The two states after which a slug is never reissued (PRD F3.4). Named here
#: rather than inline so the slug rule and the lifecycle rule cannot disagree
#: about which states are terminal.
RETIRED_STATES: frozenset[LifecycleState] = frozenset({"ARCHIVED", "DISCONNECTED"})


def _event(
    *,
    system_id: str,
    from_state: LifecycleState | None,
    to_state: LifecycleState,
    reason: str,
    related_system_id: str | None,
    actor_id: str | None,
    occurred_at: _dt.datetime,
) -> SystemLifecycleEvent:
    return SystemLifecycleEvent(
        id=new_id(LIFECYCLE_EVENT_PREFIX),
        system_id=system_id,
        from_state=from_state,
        to_state=to_state,
        reason=reason,
        related_system_id=related_system_id,
        occurred_at=occurred_at,
        actor_id=actor_id,
    )


def transition(
    records: ScopeRecords,
    *,
    system_id: str,
    to_state: LifecycleState,
    reason: str,
    actor_id: str | None = None,
    related_system_id: str | None = None,
    clock: Clock | None = None,
) -> tuple[SystemLifecycleEvent, ...]:
    """Move a system to ``to_state``, writing its event in the same transaction.

    When ``related_system_id`` is given the transition is a merge or a split, and
    a **paired** event is written on the counterpart system naming this one. The
    counterpart's own state is not changed: what its state should become is a
    policy question item 12 owns (PRD F3 non-goals), and deciding it here would
    be inventing merge semantics this build is explicitly not specifying.

    Args:
        records: The storage port.
        system_id: The system whose state changes.
        to_state: The state to move to.
        reason: Why. Required — an unexplained transition is a gap in the audit
            trail that nobody can reconstruct later.
        actor_id: Who caused it, where a human or agent did.
        related_system_id: The counterpart on a merge or split.
        clock: Injected clock; tests pass `ManualClock`.

    Returns:
        The events written, this system's first.

    Raises:
        AdoptError: ``SCOPE_SLUG_INVALID`` when the system or its counterpart
            does not exist. ``SCOPE_VIOLATION`` when a system is asked to
            transition against itself.
    """
    active_clock = clock or SystemClock()

    if related_system_id is not None and related_system_id == system_id:
        raise AdoptError(
            ErrorCode.SCOPE_VIOLATION,
            message=f"system {system_id!r} cannot be its own merge or split counterpart",
            hint="A paired event needs two distinct systems. Check the counterpart id.",
        )

    with records.transaction():
        system = records.get_system(system_id)
        if system is None:
            raise AdoptError(
                ErrorCode.SCOPE_SLUG_INVALID,
                message=f"no system with id {system_id!r} exists",
                hint="Resolve the scope path first; `transition` does not create systems.",
            )

        counterpart = None
        if related_system_id is not None:
            counterpart = records.get_system(related_system_id)
            if counterpart is None:
                raise AdoptError(
                    ErrorCode.SCOPE_SLUG_INVALID,
                    message=f"no counterpart system with id {related_system_id!r} exists",
                    hint="A merge or split names an existing system on both sides.",
                )

        occurred_at = truncate_to_millisecond(active_clock.now())
        from_state = system.lifecycle_state

        # The state write and its event are adjacent inside one transaction.
        # This adjacency *is* the no-silent-transition guarantee.
        records.set_system_state(system_id, to_state, occurred_at)
        events = [
            _event(
                system_id=system_id,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                related_system_id=related_system_id,
                actor_id=actor_id,
                occurred_at=occurred_at,
            )
        ]
        if counterpart is not None:
            events.append(
                _event(
                    system_id=counterpart.id,
                    from_state=counterpart.lifecycle_state,
                    to_state=counterpart.lifecycle_state,
                    reason=reason,
                    related_system_id=system_id,
                    actor_id=actor_id,
                    occurred_at=occurred_at,
                )
            )
        for event in events:
            records.insert_lifecycle_event(event)

    return tuple(events)
