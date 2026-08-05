"""The storage ports the identity, knowledge, binding and probe facades run on.

Its own module for the reason `adopt_scope.records` is: `no-raw-sqlite` names
`adopt_store.facades` as a source module and import-linter follows the chain, so
nothing here may reach `sqlite3` even transitively. A facade that knows it is
talking to SQLite is a facade the Postgres realization has to reimplement, and
two implementations of one set of rules is what the tenant-escape suite would
then be unable to cover.

Row in, row out. No SQL, connection or cursor crosses this boundary.

**Heads, and the one that is derived.** Three families carry
`current_revision_id` on the parent. `identity` does not -- source spec §4
declares no such column, so its head is *derived*: the revision for that identity
which no other revision supersedes (contracts §5 obligation 3). The port makes
that difference explicit rather than hiding it, because a caller that assumed a
stored head everywhere would silently read `None` for every identity.
"""

import datetime as _dt
from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from adopt_model import (
    Binding,
    BindingRevision,
    Identity,
    IdentityRevision,
    KnowledgeItem,
    KnowledgeRevision,
    ProbeDefinition,
    ProbeDefinitionRevision,
    Sensor,
    SensorHeartbeat,
)
from adopt_model._enums import FreshnessState, SensorHealth

__all__ = [
    "BindingRecords",
    "IdentityRecords",
    "KnowledgeRecords",
    "ProbeRecords",
    "RevisionRecords",
    "SensorRecords",
]


class IdentityRecords(Protocol):
    """`identity` and `identity_revision`."""

    def transaction(self) -> AbstractContextManager[None]:
        """A unit of work. Nested use joins the outermost transaction."""
        ...

    def insert_identity(self, row: Identity) -> None: ...
    def find_identity_by_uri(self, uri: str) -> Identity | None: ...
    def get_identity(self, identity_id: str) -> Identity | None: ...

    def touch_identity_last_seen(self, identity_id: str, last_seen: _dt.datetime) -> None:
        """Advance `last_seen` and nothing else.

        `identity` is a parent row, so this `UPDATE` is legitimate and leaves the
        `no-revision-update` contract untouched. It is deliberately the *only*
        mutation offered on the table: `uri` is never rewritten (contracts §4
        rule 9), and `covered_cache` belongs to `adopt_coverage` alone.
        """
        ...


class KnowledgeRecords(Protocol):
    """`knowledge_item` and `knowledge_revision`."""

    def transaction(self) -> AbstractContextManager[None]: ...
    def insert_item(self, row: KnowledgeItem) -> None: ...
    def get_item(self, item_id: str) -> KnowledgeItem | None: ...
    def set_item_freshness(
        self, item_id: str, freshness_state: FreshnessState, updated_at: _dt.datetime
    ) -> None:
        """The parent's denormalized freshness (contracts §5 obligation 6)."""
        ...


class BindingRecords(Protocol):
    """`binding` and `binding_revision`."""

    def transaction(self) -> AbstractContextManager[None]: ...
    def insert_binding(self, row: Binding) -> None: ...
    def get_binding(self, binding_id: str) -> Binding | None: ...
    def find_binding(self, item_id: str, identity_id: str) -> Binding | None: ...
    def list_bindings_for_identity(self, identity_id: str) -> Sequence[Binding]: ...


class ProbeRecords(Protocol):
    """`probe_definition` and `probe_definition_revision`."""

    def transaction(self) -> AbstractContextManager[None]: ...
    def insert_probe_definition(self, row: ProbeDefinition) -> None: ...
    def get_probe_definition(self, probe_definition_id: str) -> ProbeDefinition | None: ...


class SensorRecords(Protocol):
    """`sensor` and `sensor_heartbeat`.

    Neither table is a revision family, so `update_sensor_health` is an ordinary
    parent-row `UPDATE` and leaves `no-revision-update` untouched. The column
    list is closed on purpose: health, its reason and the three observation
    timestamps are what a heartbeat moves, and nothing here can reach
    `expected_cadence_seconds`, because a cadence that the reporting path could
    rewrite is a cadence that would drift to fit whatever the sensor happened to
    be doing.
    """

    def transaction(self) -> AbstractContextManager[None]: ...
    def insert_sensor(self, row: Sensor) -> None: ...
    def get_sensor(self, sensor_id: str) -> Sensor | None: ...
    def list_sensors(self, *, system_id: str, environment_id: str | None) -> Sequence[Sensor]: ...
    def insert_heartbeat(self, row: SensorHeartbeat) -> None: ...

    def update_sensor_health(
        self,
        sensor_id: str,
        *,
        health: SensorHealth,
        degradation_reason: str | None,
        last_attempted_at: _dt.datetime,
        last_success_at: _dt.datetime | None,
        last_event_at: _dt.datetime | None,
    ) -> None: ...

    def sensors_without_cadence(self) -> Sequence[Sensor]:
        """Every sensor whose `expected_cadence_seconds` is NULL.

        A NULL cadence silently disables the missed-heartbeat check, so
        `store doctor` reports it. The read lives here rather than in
        `adopt_freshness` because `doctor` is the caller and `resolve_freshness`
        writes and reports nothing.
        """
        ...


class RevisionRecords(Protocol):
    """The append-only half of all four families.

    There is **no update and no delete** on this port, for any family. That is
    the append-only guarantee expressed as an absent method rather than as a rule
    someone has to remember: a caller cannot reach for a mutation that has no
    name.
    """

    def transaction(self) -> AbstractContextManager[None]: ...

    def insert_identity_revision(self, row: IdentityRevision) -> None: ...
    def insert_knowledge_revision(self, row: KnowledgeRevision) -> None: ...
    def insert_binding_revision(self, row: BindingRevision) -> None: ...
    def insert_probe_definition_revision(self, row: ProbeDefinitionRevision) -> None: ...

    def advance_head(self, table: str, parent_id: str, revision_id: str) -> None:
        """Point a parent's `current_revision_id` at its new head.

        A pointer with no foreign key (CR-07), so nothing but this method and
        `doctor` knows the relationship exists.
        """
        ...

    def head_of(self, table: str, parent_id: str) -> str | None:
        """The stored head pointer, for the three families that carry one."""
        ...

    def derived_identity_head(self, identity_id: str) -> str | None:
        """The identity revision no other revision supersedes.

        Returns `None` for an identity with no revisions, and raises nothing when
        the chain has forked -- reporting a fork is `doctor`'s job, and a reader
        that raised here would make a forked store unreadable rather than
        diagnosable.
        """
        ...

    def revision_ids(self, table: str, parent_id: str) -> Sequence[str]: ...
    def superseded_ids(self, table: str, parent_id: str) -> Sequence[str]: ...
    def revision_exists(self, table: str, revision_id: str) -> bool: ...
    def parent_ids(self, table: str) -> Sequence[str]: ...
    def head_pointers(self, table: str) -> Sequence[tuple[str, str | None]]: ...
