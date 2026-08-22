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
    AudienceTag,
    Binding,
    BindingRevision,
    Escalation,
    Identity,
    IdentityRevision,
    KnowledgeItem,
    KnowledgeRevision,
    ObservabilityBoundary,
    ProbeDefinition,
    ProbeDefinitionRevision,
    Provenance,
    ReviewBatch,
    ReviewItem,
    Sensor,
    SensorHeartbeat,
)
from adopt_model._enums import EscalationStatus, FreshnessState, ReviewResolution, SensorHealth

__all__ = [
    "BindingRecords",
    "EscalationRecords",
    "IdentityRecords",
    "KnowledgeRecords",
    "ObservabilityBoundaryRecords",
    "ProbeRecords",
    "ReviewRecords",
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
    """`knowledge_item`, `knowledge_revision`, `provenance` and `audience_tag`.

    The last two arrive with Build 2, which is the first code that writes them.
    Both hang off a knowledge row and neither is a revision family, so both are
    plain inserts: `provenance` is append-only by use rather than by rule -- a
    claim's source is a fact about a revision that already exists, and a
    revision is immutable -- and `audience_tag` is a set membership whose
    primary key is `(item_id, audience)`.
    """

    def transaction(self) -> AbstractContextManager[None]: ...
    def insert_item(self, row: KnowledgeItem) -> None: ...
    def get_item(self, item_id: str) -> KnowledgeItem | None: ...
    def set_item_freshness(
        self, item_id: str, freshness_state: FreshnessState, updated_at: _dt.datetime
    ) -> None:
        """The parent's denormalized freshness (contracts §5 obligation 6)."""
        ...

    def insert_provenance(self, row: Provenance) -> None:
        """Record where one revision's claim came from.

        There is no update and no delete, for the reason the revision tables
        have none: provenance that could be rewritten is provenance that cannot
        distinguish `artifact_observed` from `authored` after the fact, which is
        the one distinction v6.1 §6 Build 2 makes non-negotiable.
        """
        ...

    def insert_audience_tag(self, row: AudienceTag) -> None:
        """Tag an item with one audience. `(item_id, audience)` is the key."""
        ...

    def audiences_for_item(self, item_id: str) -> Sequence[str]:
        """The audiences already tagged, so a re-tag is a no-op rather than a
        constraint violation."""
        ...


class BindingRecords(Protocol):
    """`binding` and `binding_revision`."""

    def transaction(self) -> AbstractContextManager[None]: ...
    def insert_binding(self, row: Binding) -> None: ...
    def get_binding(self, binding_id: str) -> Binding | None: ...
    def find_binding(self, item_id: str, identity_id: str) -> Binding | None: ...
    def list_bindings_for_identity(self, identity_id: str) -> Sequence[Binding]: ...


class ReviewRecords(Protocol):
    """`review_batch` and `review_item` -- the one queue (v6.1 §6 F5).

    Introduced by Build 2 for harvest candidates and suggested bindings, and
    extended rather than replaced by Build 6's change items and Build 8's
    managed batches. One surface, one habit, one implementation: a second queue
    would be a second place a reviewer has to remember to look.

    **`resolution` is the only mutable column on either table**, and it moves
    once, from `NULL` to a terminal value. Neither table is a revision family,
    so `no-revision-update` does not reach them -- which is exactly why the
    narrowness is stated here rather than assumed. A queue row records a human's
    disposition; it never holds knowledge, and nothing here can rewrite what the
    reviewer was shown.
    """

    def transaction(self) -> AbstractContextManager[None]: ...
    def insert_batch(self, row: ReviewBatch) -> None: ...
    def insert_item(self, row: ReviewItem) -> None: ...
    def get_batch(self, review_batch_id: str) -> ReviewBatch | None: ...
    def get_item(self, review_item_id: str) -> ReviewItem | None: ...
    def items_in_batch(self, review_batch_id: str) -> Sequence[ReviewItem]: ...

    def set_item_resolution(self, review_item_id: str, resolution: ReviewResolution) -> None:
        """Stamp one item's disposition. Never un-stamps: the caller checks."""
        ...

    def set_batch_resolution(
        self,
        review_batch_id: str,
        resolution: ReviewResolution,
        resolved_at: _dt.datetime,
    ) -> None:
        """Close a batch once every item in it is resolved."""
        ...


class EscalationRecords(Protocol):
    """`escalation` -- a question the store could not answer, and what happened.

    Introduced by Build 3, which is the first code that writes the table
    (v6.1 §6 Build 3, F2). It is deliberately **not** a second review queue:
    `ReviewRecords` holds dispositions over knowledge someone proposed, and this
    holds questions nobody has answered yet. The two meet only when
    `adopt answer` turns one of these into knowledge, and even then the write
    goes through `KnowledgeFacade` -- nothing on this port creates a revision.

    **`question` is nullable and that nullability is a privacy control, not a
    convenience.** F2 splits passive logging from explicit escalation: an
    escalation opened without consent records that a question was asked and
    stores no text. A caller that always supplied the text would silently
    convert every ask into a stored transcript, which is exactly the default
    v6.1 refuses.

    **Two mutable columns, both moving once**: `status` `open -> answered`, and
    the `candidate_revision_id`/`answered_by`/`answered_at` triple stamped with
    it. `escalation` is not a revision family, so `no-revision-update` does not
    reach this `UPDATE` -- which is why the narrowness is written down here, the
    way `ReviewRecords` writes its own down.
    """

    def transaction(self) -> AbstractContextManager[None]: ...
    def insert_escalation(self, row: Escalation) -> None: ...
    def get_escalation(self, escalation_id: str) -> Escalation | None: ...

    def list_escalations(
        self, *, system_id: str, status: EscalationStatus | None = None
    ) -> Sequence[Escalation]:
        """Escalations for one system, newest first, optionally by status."""
        ...

    def set_escalation_answered(
        self,
        escalation_id: str,
        *,
        candidate_revision_id: str,
        answered_by: str | None,
        answered_at: _dt.datetime,
    ) -> None:
        """Stamp one escalation as answered. Never un-stamps: the caller checks.

        The revision id is required rather than optional because an escalation
        that reported itself answered while pointing at no knowledge would be
        indistinguishable from an open one to every reader except a human
        reading the timestamp -- and the whole point of the capture ratchet is
        that the next asker gets the answer, not that someone marked a row.
        """
        ...


class ProbeRecords(Protocol):
    """`probe_definition` and `probe_definition_revision`."""

    def transaction(self) -> AbstractContextManager[None]: ...
    def insert_probe_definition(self, row: ProbeDefinition) -> None: ...
    def get_probe_definition(self, probe_definition_id: str) -> ProbeDefinition | None: ...


class ObservabilityBoundaryRecords(Protocol):
    """`observability_boundary`.

    **There is no update on this port.** A boundary is declared, not amended:
    re-negotiation appends a row and `latest_boundary` reads the newest. The
    table is not a `*_revision` family, so `no-revision-update` would not have
    caught an `UPDATE` here -- which is exactly why the absence is stated rather
    than assumed. What may leave a client environment is not a field to be
    quietly corrected.
    """

    def transaction(self) -> AbstractContextManager[None]: ...
    def insert_boundary(self, row: ObservabilityBoundary) -> None: ...

    def latest_boundary(
        self, *, system_id: str, environment_id: str | None
    ) -> ObservabilityBoundary | None:
        """The newest boundary for the scope, by `declared_at` then `id`.

        `id` breaks the tie because ULIDs are monotonic within a millisecond and
        `declared_at` is millisecond-truncated (contracts §1.2) -- two boundaries
        declared in the same millisecond would otherwise be ordered arbitrarily,
        and "arbitrarily" for this table means a client's permitted egress list
        depends on a sort nobody specified.
        """
        ...


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
