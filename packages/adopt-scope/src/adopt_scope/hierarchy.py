"""The four-level hierarchy: creation, resolution, and the storage port.

`ScopeFacade` is both the `adopt-scope` public API (implementation spec §4.5)
and the `Store.scope()` facade (contracts §10.3). It is one class rather than
two because a second implementation of the same rules is a second place for them
to drift — and the rules here are the ones tenant isolation is expressed from.

**Storage is a port, not a dependency.** `ScopeRecords` is implemented once over
SQLite in `adopt_store` and once over Postgres in `plane_store`, so the escape
suite drives the *same* facade the local CLI drives. If the facade were written
against SQLite directly, the plane would need its own copy and the escape suite
would be testing code the CLI never runs.

**Caller-supplied ids and scope are rejected by being unrepresentable.**
Contracts §10.3 requires that ids are generated inside the facade and scope is
injected by it. No method below accepts an `id`, and each accepts only the
parent it hangs from — so there is no argument to reject at runtime, and no
future caller can find a way to pass one. That is a stronger guarantee than a
validation branch, which only rejects what someone remembered to check.
"""

import datetime as _dt
from typing import Final

from adopt_model import Engagement, Environment, Firm, System, SystemLifecycleEvent
from adopt_model._enums import Archetype, DeploymentMode, LifecycleState
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, new_id, truncate_to_millisecond
from adopt_scope.lifecycle import transition as _transition
from adopt_scope.records import ScopeRecords
from adopt_scope.resolve import Scope, ScopeNode, ScopePath
from adopt_scope.slug import ensure_slug_available, validate_slug

__all__ = ["ID_PREFIXES", "INITIAL_LIFECYCLE_STATE", "ScopeFacade"]

#: Contracts §1.1. Minted only through `adopt_obs.new_id`, which rejects an
#: unregistered prefix, so a typo here fails at the first call rather than
#: producing ids that look valid and join to nothing.
ID_PREFIXES: Final[dict[str, str]] = {
    "firm": "firm",
    "engagement": "eng",
    "system": "sys",
    "environment": "env",
}

#: A system enters the hierarchy recorded but not yet worked. PRD F3.5 keeps the
#: eight states distinct; nothing here collapses them.
INITIAL_LIFECYCLE_STATE: Final[LifecycleState] = "DISCOVERED"


def _missing(level: str, slug: str, parent: str | None) -> AdoptError:
    where = f" under {parent!r}" if parent else ""
    return AdoptError(
        ErrorCode.SCOPE_SLUG_INVALID,
        message=f"no {level} with slug {slug!r} exists{where}",
        hint=f"Create the {level} first, or check the scope path for a typo.",
    )


class ScopeFacade:
    """Create and resolve the `firm → engagement → system → environment` chain."""

    def __init__(self, records: ScopeRecords, *, clock: Clock | None = None) -> None:
        self._records = records
        self._clock: Clock = clock if clock is not None else SystemClock()

    def _now(self) -> _dt.datetime:
        """The clock reading at the precision the store keeps.

        Truncated here rather than on the way to SQL, so the row this facade
        *returns* is the row a later read produces. An untruncated timestamp on
        the returned model is a value that exists only in memory.
        """
        return truncate_to_millisecond(self._clock.now())

    # -- creation ---------------------------------------------------------

    def create_firm(self, *, slug: str, name: str) -> Firm:
        """Create the root of a scope chain.

        Raises:
            AdoptError: ``SCOPE_SLUG_INVALID`` or ``SCOPE_SLUG_REUSED``.
        """
        validate_slug(slug, level="firm")
        with self._records.transaction():
            existing = self._records.find_firm(slug)
            ensure_slug_available(slug, [existing.slug] if existing else [], level="firm")
            row = Firm(
                id=new_id(ID_PREFIXES["firm"]),
                slug=slug,
                name=name,
                created_at=self._now(),
            )
            self._records.insert_firm(row)
        return row

    def create_engagement(
        self, *, firm_id: str, slug: str, name: str, client_label: str | None = None
    ) -> Engagement:
        """Create an engagement under a firm.

        `client_label` is free text: the client-account entity is deferred
        (PRD F3 non-goals), and inventing one here would be a schema change.
        """
        validate_slug(slug, level="engagement")
        with self._records.transaction():
            existing = self._records.find_engagement(firm_id, slug)
            ensure_slug_available(slug, [existing.slug] if existing else [], level="engagement")
            row = Engagement(
                id=new_id(ID_PREFIXES["engagement"]),
                firm_id=firm_id,
                slug=slug,
                name=name,
                client_label=client_label,
                created_at=self._now(),
            )
            self._records.insert_engagement(row)
        return row

    def create_system(
        self,
        *,
        engagement_id: str,
        slug: str,
        name: str,
        archetype: Archetype | None = None,
        deployment_mode: DeploymentMode | None = None,
    ) -> System:
        """Create a system under an engagement, in `DISCOVERED`.

        The initial state is written directly rather than transitioned into:
        there is no prior state for a `system_lifecycle_event` to record a move
        from, and inventing one would make the event log claim a transition that
        never happened. Every state change *after* creation writes an event.
        """
        validate_slug(slug, level="system")
        with self._records.transaction():
            existing = self._records.find_system(engagement_id, slug)
            ensure_slug_available(slug, [existing.slug] if existing else [], level="system")
            created = self._now()
            row = System(
                id=new_id(ID_PREFIXES["system"]),
                engagement_id=engagement_id,
                slug=slug,
                name=name,
                archetype=archetype,
                lifecycle_state=INITIAL_LIFECYCLE_STATE,
                deployment_mode=deployment_mode,
                created_at=created,
                updated_at=created,
            )
            self._records.insert_system(row)
        return row

    def create_environment(
        self,
        *,
        system_id: str,
        slug: str,
        name: str,
        is_billable: bool = False,
        data_residency_region: str | None = None,
    ) -> Environment:
        """Create an environment under a system.

        `is_billable` and `data_residency_region` are **recorded and never
        interpreted** in Build 0 (PRD F3.6, owner decisions 14 and 17). Nothing
        in this repository reads either column; a grep for a reader is the test.
        """
        validate_slug(slug, level="environment")
        with self._records.transaction():
            existing = self._records.find_environment(system_id, slug)
            ensure_slug_available(slug, [existing.slug] if existing else [], level="environment")
            row = Environment(
                id=new_id(ID_PREFIXES["environment"]),
                system_id=system_id,
                slug=slug,
                name=name,
                is_billable=is_billable,
                data_residency_region=data_residency_region,
                created_at=self._now(),
            )
            self._records.insert_environment(row)
        return row

    # -- resolution -------------------------------------------------------

    def resolve(self, path: str | ScopePath) -> Scope:
        """Resolve a scope path to ids **and** slugs at every requested level.

        Raises:
            AdoptError: ``SCOPE_SLUG_INVALID`` when a path segment is malformed
                or names a scope that does not exist.
        """
        parsed = ScopePath.parse(path) if isinstance(path, str) else path

        firm = self._records.find_firm(parsed.firm)
        if firm is None:
            raise _missing("firm", parsed.firm, None)
        scope = Scope(firm=ScopeNode(id=firm.id, slug=firm.slug))
        if parsed.engagement is None:
            return scope

        engagement = self._records.find_engagement(firm.id, parsed.engagement)
        if engagement is None:
            raise _missing("engagement", parsed.engagement, firm.slug)
        scope = Scope(
            firm=scope.firm,
            engagement=ScopeNode(id=engagement.id, slug=engagement.slug),
        )
        if parsed.system is None:
            return scope

        system = self._records.find_system(engagement.id, parsed.system)
        if system is None:
            raise _missing("system", parsed.system, engagement.slug)
        scope = Scope(
            firm=scope.firm,
            engagement=scope.engagement,
            system=ScopeNode(id=system.id, slug=system.slug),
        )
        if parsed.environment is None:
            return scope

        environment = self._records.find_environment(system.id, parsed.environment)
        if environment is None:
            raise _missing("environment", parsed.environment, system.slug)
        return Scope(
            firm=scope.firm,
            engagement=scope.engagement,
            system=scope.system,
            environment=ScopeNode(id=environment.id, slug=environment.slug),
        )

    # -- lifecycle --------------------------------------------------------

    def transition(
        self,
        system_id: str,
        to_state: LifecycleState,
        reason: str,
        actor_id: str | None = None,
        related_system_id: str | None = None,
    ) -> tuple[SystemLifecycleEvent, ...]:
        """Move a system's lifecycle state, writing its event in the same transaction.

        Delegates to `adopt_scope.lifecycle.transition`, which is where the
        no-silent-transition guarantee is implemented and property-tested.
        """
        return _transition(
            self._records,
            system_id=system_id,
            to_state=to_state,
            reason=reason,
            actor_id=actor_id,
            related_system_id=related_system_id,
            clock=self._clock,
        )
