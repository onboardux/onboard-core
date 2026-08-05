"""`ScopeRecords` over SQLite: the storage half of the scope facade.

The rules — slug validity, immutability, no reissue, no silent transition — live
in `adopt_scope`. This module only reads and writes rows, and it is the *second*
implementation of the same port that makes that separation worth having: the
first is here, the second is Postgres in the closed repository, and both drive
one facade whose behaviour is asserted once.

**Every write validates against the generated model on the way in and the way
out.** The model is generated from `schema/canonical.yaml`, is closed with
`extra="forbid"`, and is therefore the egress allowlist: a column this code
invented would fail construction rather than reaching the file.
"""

import datetime as _dt
import json as _json
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Final, get_args, get_origin

from pydantic import BaseModel

from adopt_model import (
    Binding,
    BindingRevision,
    Engagement,
    Environment,
    Firm,
    Identity,
    IdentityRevision,
    KnowledgeItem,
    KnowledgeRevision,
    ObservabilityBoundary,
    ProbeDefinition,
    ProbeDefinitionRevision,
    Sensor,
    SensorHeartbeat,
    System,
    SystemLifecycleEvent,
)
from adopt_model._enums import FreshnessState, LifecycleState, SensorHealth
from adopt_obs import format_timestamp
from adopt_store.sqlite.store import SqliteStore

__all__ = [
    "SqliteBindingRecords",
    "SqliteCoverageRecords",
    "SqliteFreshnessRecords",
    "SqliteIdentityRecords",
    "SqliteKnowledgeRecords",
    "SqliteProbeRecords",
    "SqliteRevisionRecords",
    "SqliteScopeRecords",
    "SqliteSensorRecords",
]

#: Table names are interpolated into a handful of statements below, so each is
#: checked against a closed set first. The sets are the allowlist: a table name
#: that is not in one of them never reaches a statement, which is what makes the
#: interpolation safe rather than merely conventional.
_HEAD_POINTER_TABLES: Final[dict[str, str]] = {
    "knowledge_item": "id",
    "binding": "id",
    "probe_definition": "id",
}
_PARENT_TABLES: Final[dict[str, str]] = {**_HEAD_POINTER_TABLES, "identity": "id"}
_REVISION_PARENT_COLUMNS: Final[dict[str, str]] = {
    "identity_revision": "identity_id",
    "knowledge_revision": "item_id",
    "binding_revision": "binding_id",
    "probe_definition_revision": "probe_definition_id",
}


def _require_known(table: str, allowed: Mapping[str, str]) -> str:
    """The allowed column for ``table``, or a refusal naming the closed set."""
    column = allowed.get(table)
    if column is None:
        raise ValueError(
            f"{table!r} is not one of {sorted(allowed)}. Table names reaching a statement "
            "come from this closed set and never from a caller."
        )
    return column


_JSON_COLUMN_CACHE: dict[type[BaseModel], frozenset[str]] = {}


def _json_columns(model_type: type[BaseModel]) -> frozenset[str]:
    """Which of a model's fields are manifest `json` columns.

    SQLite has no JSON type, so such a column is TEXT on the way in and TEXT on
    the way out, and translating it is the realization's job -- exactly as it
    already is for datetimes and booleans. The set is derived from the generated
    model's own annotations rather than from a hand-kept list, which would be
    correct until the next `json` column is added by someone with no reason to
    look here.

    Cached per model type: annotations cannot change at runtime, and re-deriving
    them per row would put reflection on the recompute's hot path.
    """
    cached = _JSON_COLUMN_CACHE.get(model_type)
    if cached is not None:
        return cached
    resolved = frozenset(
        name
        for name, field in model_type.model_fields.items()
        if any(
            get_origin(candidate) in (dict, list)
            for candidate in (get_args(field.annotation) or (field.annotation,))
        )
    )
    _JSON_COLUMN_CACHE[model_type] = resolved
    return resolved


def _to_row(model: BaseModel) -> dict[str, Any]:
    """Model -> column values, timestamps rendered and booleans stored as 0/1.

    The generated model is the authority on what a row contains: it is closed
    with ``extra="forbid"``, so a column invented here would fail construction
    long before it could reach the file.
    """
    json_columns = _json_columns(type(model))
    values: dict[str, Any] = {}
    for name, value in model.model_dump().items():
        if name in json_columns and value is not None:
            # Sorted keys and no spaces: the export round-trip compares table
            # files byte for byte, so a JSON column that re-serialised in a
            # different key order would break G0 on a store nothing had changed.
            values[name] = _json.dumps(value, sort_keys=True, separators=(",", ":"))
        elif isinstance(value, _dt.datetime):
            values[name] = format_timestamp(value)
        elif isinstance(value, bool):
            values[name] = int(value)
        else:
            values[name] = value
    return values


def _from_row[TModel: BaseModel](model_type: type[TModel], row: Mapping[str, Any]) -> TModel:
    """Column values -> model, validated, with instants normalised back to UTC.

    Pydantic parses an RFC 3339 string into an aware datetime carrying its own
    `TzInfo`, which compares unequal to the `datetime.UTC` the writer supplied
    even when the two describe the same instant. Normalising here is what makes
    *write then read* return the value that was written rather than one that is
    merely equivalent — and equality on the generated models is what the export
    round-trip and every caller comparison rest on.

    `json` columns are decoded first, in the same place and for the same reason:
    a column the store rendered has to be un-rendered by the store, or the model
    rejects the very row it wrote.
    """
    values = dict(row)
    for name in _json_columns(model_type):
        candidate = values.get(name)
        if isinstance(candidate, str):
            values[name] = _json.loads(candidate)
    model = model_type.model_validate(values)
    shifted = {
        name: value.astimezone(_dt.UTC)
        for name, value in model.__dict__.items()
        if isinstance(value, _dt.datetime)
    }
    return model.model_copy(update=shifted) if shifted else model


def _insert_sql(table: str, columns: Sequence[str]) -> str:
    placeholders = ", ".join("?" for _ in columns)
    # S608: `table` and `columns` come from the generated models, never from a
    # caller. There is no user-supplied identifier anywhere in this module.
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"  # noqa: S608


def _insert(store: SqliteStore, table: str, model: BaseModel) -> None:
    """Write one row, with the generated model as the column authority."""
    values = _to_row(model)
    columns = list(values)
    store.execute(_insert_sql(table, columns), tuple(values[column] for column in columns))


def _one[TModel: BaseModel](
    store: SqliteStore, model_type: type[TModel], sql: str, parameters: tuple[object, ...] = ()
) -> TModel | None:
    """The first row as a validated model, or `None`."""
    rows = store.query(sql, parameters)
    return None if not rows else _from_row(model_type, dict(rows[0]))


class SqliteScopeRecords:
    """The SQLite implementation of `adopt_scope.ScopeRecords`."""

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    # -- unit of work -----------------------------------------------------

    def transaction(self) -> AbstractContextManager[None]:
        return self._store.transaction()

    # -- inserts ----------------------------------------------------------

    def _insert(self, table: str, model: BaseModel) -> None:
        _insert(self._store, table, model)

    def insert_firm(self, row: Firm) -> None:
        self._insert("firm", row)

    def insert_engagement(self, row: Engagement) -> None:
        self._insert("engagement", row)

    def insert_system(self, row: System) -> None:
        self._insert("system", row)

    def insert_environment(self, row: Environment) -> None:
        self._insert("environment", row)

    def insert_lifecycle_event(self, row: SystemLifecycleEvent) -> None:
        self._insert("system_lifecycle_event", row)

    # -- lookups ----------------------------------------------------------
    #
    # None of these filters on `lifecycle_state`. That is the mechanism behind
    # "a slug is never reissued": an ARCHIVED system is still found, so its slug
    # is still taken.

    def _one[TModel: BaseModel](
        self, model_type: type[TModel], sql: str, parameters: tuple[object, ...]
    ) -> TModel | None:
        return _one(self._store, model_type, sql, parameters)

    def find_firm(self, slug: str) -> Firm | None:
        return self._one(Firm, "SELECT * FROM firm WHERE slug = ?", (slug,))

    def find_engagement(self, firm_id: str, slug: str) -> Engagement | None:
        return self._one(
            Engagement,
            "SELECT * FROM engagement WHERE firm_id = ? AND slug = ?",
            (firm_id, slug),
        )

    def find_system(self, engagement_id: str, slug: str) -> System | None:
        return self._one(
            System,
            "SELECT * FROM system WHERE engagement_id = ? AND slug = ?",
            (engagement_id, slug),
        )

    def find_environment(self, system_id: str, slug: str) -> Environment | None:
        return self._one(
            Environment,
            "SELECT * FROM environment WHERE system_id = ? AND slug = ?",
            (system_id, slug),
        )

    def get_system(self, system_id: str) -> System | None:
        return self._one(System, "SELECT * FROM system WHERE id = ?", (system_id,))

    # -- state ------------------------------------------------------------

    def set_system_state(
        self, system_id: str, to_state: LifecycleState, updated_at: _dt.datetime
    ) -> None:
        """Advance a system's state.

        `system` is a parent row, not a `*_revision` table, so an UPDATE here is
        legitimate and the `no-revision-update` contract is untouched. The event
        that accompanies it is written by `adopt_scope.lifecycle.transition`,
        which is the only caller.
        """
        self._store.execute(
            "UPDATE system SET lifecycle_state = ?, updated_at = ? WHERE id = ?",
            (to_state, format_timestamp(updated_at), system_id),
        )


class SqliteIdentityRecords:
    """The SQLite implementation of `adopt_store.facades.records.IdentityRecords`."""

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def transaction(self) -> AbstractContextManager[None]:
        return self._store.transaction()

    def insert_identity(self, row: Identity) -> None:
        _insert(self._store, "identity", row)

    def find_identity_by_uri(self, uri: str) -> Identity | None:
        return _one(self._store, Identity, "SELECT * FROM identity WHERE uri = ?", (uri,))

    def get_identity(self, identity_id: str) -> Identity | None:
        return _one(self._store, Identity, "SELECT * FROM identity WHERE id = ?", (identity_id,))

    def touch_identity_last_seen(self, identity_id: str, last_seen: _dt.datetime) -> None:
        """Advance `last_seen`, and nothing else.

        The column list is exhaustive on purpose. `identity` is a parent row, so
        an `UPDATE` here is legitimate, but the one column named is the only one
        this path is allowed to move: `uri` is never rewritten (contracts §4 rule
        9) and `covered_cache` belongs to `adopt_coverage` alone, which
        `no-covered-cache-write` enforces.
        """
        self._store.execute(
            "UPDATE identity SET last_seen = ? WHERE id = ?",
            (format_timestamp(last_seen), identity_id),
        )


class SqliteKnowledgeRecords:
    """The SQLite implementation of `KnowledgeRecords`."""

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def transaction(self) -> AbstractContextManager[None]:
        return self._store.transaction()

    def insert_item(self, row: KnowledgeItem) -> None:
        _insert(self._store, "knowledge_item", row)

    def get_item(self, item_id: str) -> KnowledgeItem | None:
        return _one(
            self._store, KnowledgeItem, "SELECT * FROM knowledge_item WHERE id = ?", (item_id,)
        )

    def set_item_freshness(
        self, item_id: str, freshness_state: FreshnessState, updated_at: _dt.datetime
    ) -> None:
        self._store.execute(
            "UPDATE knowledge_item SET freshness_state = ?, updated_at = ? WHERE id = ?",
            (freshness_state, format_timestamp(updated_at), item_id),
        )


class SqliteBindingRecords:
    """The SQLite implementation of `BindingRecords`."""

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def transaction(self) -> AbstractContextManager[None]:
        return self._store.transaction()

    def insert_binding(self, row: Binding) -> None:
        _insert(self._store, "binding", row)

    def get_binding(self, binding_id: str) -> Binding | None:
        return _one(self._store, Binding, "SELECT * FROM binding WHERE id = ?", (binding_id,))

    def find_binding(self, item_id: str, identity_id: str) -> Binding | None:
        return _one(
            self._store,
            Binding,
            "SELECT * FROM binding WHERE item_id = ? AND identity_id = ?",
            (item_id, identity_id),
        )

    def list_bindings_for_identity(self, identity_id: str) -> Sequence[Binding]:
        rows = self._store.query(
            "SELECT * FROM binding WHERE identity_id = ? ORDER BY id", (identity_id,)
        )
        return [_from_row(Binding, dict(row)) for row in rows]


class SqliteProbeRecords:
    """The SQLite implementation of `ProbeRecords`."""

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def transaction(self) -> AbstractContextManager[None]:
        return self._store.transaction()

    def insert_probe_definition(self, row: ProbeDefinition) -> None:
        _insert(self._store, "probe_definition", row)

    def get_probe_definition(self, probe_definition_id: str) -> ProbeDefinition | None:
        return _one(
            self._store,
            ProbeDefinition,
            "SELECT * FROM probe_definition WHERE id = ?",
            (probe_definition_id,),
        )


class SqliteRevisionRecords:
    """The append-only half of all four families, over SQLite.

    **There is no `UPDATE` against any `*_revision` table in this class**, and
    `no-revision-update` scans the source text of both repositories to keep it
    that way. `advance_head` updates a *parent*, which is what contracts §5
    obligation 6 permits and what the head pointer exists for.
    """

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def transaction(self) -> AbstractContextManager[None]:
        return self._store.transaction()

    # -- inserts ----------------------------------------------------------

    def insert_identity_revision(self, row: IdentityRevision) -> None:
        _insert(self._store, "identity_revision", row)

    def insert_knowledge_revision(self, row: KnowledgeRevision) -> None:
        _insert(self._store, "knowledge_revision", row)

    def insert_binding_revision(self, row: BindingRevision) -> None:
        _insert(self._store, "binding_revision", row)

    def insert_probe_definition_revision(self, row: ProbeDefinitionRevision) -> None:
        _insert(self._store, "probe_definition_revision", row)

    # -- heads ------------------------------------------------------------

    def advance_head(self, table: str, parent_id: str, revision_id: str) -> None:
        _require_known(table, _HEAD_POINTER_TABLES)
        # S608: `table` is checked against a closed literal set on the line above,
        # so no caller-supplied text reaches the statement.
        self._store.execute(
            f"UPDATE {table} SET current_revision_id = ? WHERE id = ?",  # noqa: S608
            (revision_id, parent_id),
        )

    def head_of(self, table: str, parent_id: str) -> str | None:
        _require_known(table, _HEAD_POINTER_TABLES)
        rows = self._store.query(
            f"SELECT current_revision_id FROM {table} WHERE id = ?",  # noqa: S608
            (parent_id,),
        )
        if not rows:
            return None
        value = rows[0]["current_revision_id"]
        return None if value is None else str(value)

    def derived_identity_head(self, identity_id: str) -> str | None:
        """The identity revision no other revision supersedes.

        `identity` has no head-pointer column (source spec §4 declares none), so
        the head is computed. `LIMIT 1` is deliberate and is not a fork check: a
        forked chain is `doctor`'s finding, and a reader that raised here would
        make a damaged store unreadable at the exact moment someone needs to read
        it to work out what happened.
        """
        rows = self._store.query(
            "SELECT id FROM identity_revision WHERE identity_id = ? AND id NOT IN "
            "(SELECT supersedes_revision_id FROM identity_revision "
            " WHERE identity_id = ? AND supersedes_revision_id IS NOT NULL) "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (identity_id, identity_id),
        )
        return None if not rows else str(rows[0]["id"])

    # -- chain reads ------------------------------------------------------

    def revision_ids(self, table: str, parent_id: str) -> Sequence[str]:
        column = _require_known(table, _REVISION_PARENT_COLUMNS)
        rows = self._store.query(
            f"SELECT id FROM {table} WHERE {column} = ? ORDER BY id",  # noqa: S608
            (parent_id,),
        )
        return [str(row["id"]) for row in rows]

    def superseded_ids(self, table: str, parent_id: str) -> Sequence[str]:
        column = _require_known(table, _REVISION_PARENT_COLUMNS)
        rows = self._store.query(
            f"SELECT supersedes_revision_id FROM {table} "  # noqa: S608
            f"WHERE {column} = ? AND supersedes_revision_id IS NOT NULL",
            (parent_id,),
        )
        return [str(row["supersedes_revision_id"]) for row in rows]

    def revision_exists(self, table: str, revision_id: str) -> bool:
        _require_known(table, _REVISION_PARENT_COLUMNS)
        rows = self._store.query(
            f"SELECT 1 FROM {table} WHERE id = ?",  # noqa: S608
            (revision_id,),
        )
        return bool(rows)

    def parent_ids(self, table: str) -> Sequence[str]:
        _require_known(table, _PARENT_TABLES)
        rows = self._store.query(f"SELECT id FROM {table} ORDER BY id")  # noqa: S608
        return [str(row["id"]) for row in rows]

    def head_pointers(self, table: str) -> Sequence[tuple[str, str | None]]:
        _require_known(table, _HEAD_POINTER_TABLES)
        rows = self._store.query(
            f"SELECT id, current_revision_id FROM {table} ORDER BY id"  # noqa: S608
        )
        return [
            (
                str(row["id"]),
                None if row["current_revision_id"] is None else str(row["current_revision_id"]),
            )
            for row in rows
        ]


class SqliteSensorRecords:
    """The SQLite implementation of `SensorRecords`."""

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def transaction(self) -> AbstractContextManager[None]:
        return self._store.transaction()

    def insert_sensor(self, row: Sensor) -> None:
        _insert(self._store, "sensor", row)

    def get_sensor(self, sensor_id: str) -> Sensor | None:
        return _one(self._store, Sensor, "SELECT * FROM sensor WHERE id = ?", (sensor_id,))

    def list_sensors(self, *, system_id: str, environment_id: str | None) -> Sequence[Sensor]:
        if environment_id is None:
            rows = self._store.query(
                "SELECT * FROM sensor WHERE system_id = ? ORDER BY id", (system_id,)
            )
        else:
            rows = self._store.query(
                "SELECT * FROM sensor WHERE system_id = ? AND environment_id = ? ORDER BY id",
                (system_id, environment_id),
            )
        return [_from_row(Sensor, dict(row)) for row in rows]

    def insert_heartbeat(self, row: SensorHeartbeat) -> None:
        _insert(self._store, "sensor_heartbeat", row)

    def update_sensor_health(
        self,
        sensor_id: str,
        *,
        health: SensorHealth,
        degradation_reason: str | None,
        last_attempted_at: _dt.datetime,
        last_success_at: _dt.datetime | None,
        last_event_at: _dt.datetime | None,
    ) -> None:
        """Move a sensor's health and its observation timestamps.

        `sensor` is a parent row, not a `*_revision` table, so this `UPDATE` is
        legitimate. The column list is exhaustive on purpose: nothing here can
        reach `expected_cadence_seconds`, because the reporting path rewriting
        the cadence it is measured against would make the missed-heartbeat check
        unfalsifiable.
        """
        self._store.execute(
            "UPDATE sensor SET health = ?, degradation_reason = ?, last_attempted_at = ?, "
            "last_success_at = ?, last_event_at = ? WHERE id = ?",
            (
                health,
                degradation_reason,
                format_timestamp(last_attempted_at),
                None if last_success_at is None else format_timestamp(last_success_at),
                None if last_event_at is None else format_timestamp(last_event_at),
                sensor_id,
            ),
        )

    def sensors_without_cadence(self) -> Sequence[Sensor]:
        rows = self._store.query(
            "SELECT * FROM sensor WHERE expected_cadence_seconds IS NULL ORDER BY id"
        )
        return [_from_row(Sensor, dict(row)) for row in rows]


#: The head of an identity's chain is *derived* -- the revision no other
#: revision supersedes (contracts §5 obligation 3) -- so both readers of it
#: share one subquery rather than two spellings that could drift.
_UNSUPERSEDED_IDENTITY_REVISION: Final[str] = (
    "id NOT IN (SELECT supersedes_revision_id FROM identity_revision "
    "WHERE supersedes_revision_id IS NOT NULL)"
)


class SqliteCoverageRecords:
    """The SQLite implementation of `adopt_coverage.CoverageRecords`.

    Satisfied **structurally**: nothing here imports `adopt_coverage`, and
    `adopt_coverage` imports nothing here. That is what keeps `no-raw-sqlite`
    kept while the recompute still runs against a real store.

    Every method is a bulk fetch scoped by system, because N6 budgets the whole
    recompute at 50k identities: a per-identity round trip would spend the budget
    on round trips rather than on the six inputs it is supposed to evaluate.

    **No statement in this class names the coverage cache.** Reading `identity`
    with `SELECT *` carries `covered_cache` along without mentioning it, and the
    write lives in `adopt_coverage.cache`, which is the only place
    `no-covered-cache-write` permits it.
    """

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def _scoped(self, sql: str, system_id: str, environment_id: str | None, alias: str) -> Any:
        """Run `sql` with the environment predicate spliced in from a closed set."""
        if environment_id is None:
            return self._store.query(sql.format(scope=""), (system_id,))
        return self._store.query(
            sql.format(scope=f" AND {alias}.environment_id = ?"), (system_id, environment_id)
        )

    def identities_in_scope(
        self, *, system_id: str, environment_id: str | None
    ) -> Sequence[Identity]:
        rows = self._scoped(
            "SELECT * FROM identity i WHERE i.system_id = ?{scope} ORDER BY i.id",
            system_id,
            environment_id,
            "i",
        )
        return [_from_row(Identity, dict(row)) for row in rows]

    def systems_with_identities(self) -> Sequence[str]:
        rows = self._store.query("SELECT DISTINCT system_id FROM identity ORDER BY system_id")
        return [str(row["system_id"]) for row in rows]

    def head_identity_statuses(
        self, *, system_id: str, environment_id: str | None
    ) -> Mapping[str, str]:
        rows = self._scoped(
            # S608: the only interpolations are `_UNSUPERSEDED_IDENTITY_REVISION`, a
            # module constant, and the environment predicate `_scoped` splices from a
            # closed pair. No caller text reaches the statement.
            "SELECT r.identity_id AS identity_id, r.status AS status "  # noqa: S608
            "FROM identity_revision r JOIN identity i ON i.id = r.identity_id "
            f"WHERE i.system_id = ?{{scope}} AND r.{_UNSUPERSEDED_IDENTITY_REVISION} "
            "ORDER BY r.created_at, r.id",
            system_id,
            environment_id,
            "i",
        )
        return {str(row["identity_id"]): str(row["status"]) for row in rows}

    def bindings_in_scope(self, *, system_id: str, environment_id: str | None) -> Sequence[Binding]:
        rows = self._scoped(
            "SELECT b.* FROM binding b JOIN identity i ON i.id = b.identity_id "
            "WHERE i.system_id = ?{scope} ORDER BY b.id",
            system_id,
            environment_id,
            "i",
        )
        return [_from_row(Binding, dict(row)) for row in rows]

    def head_binding_statuses(
        self, *, system_id: str, environment_id: str | None
    ) -> Mapping[str, str]:
        rows = self._scoped(
            "SELECT b.id AS binding_id, rev.status AS status FROM binding b "
            "JOIN binding_revision rev ON rev.id = b.current_revision_id "
            "JOIN identity i ON i.id = b.identity_id "
            "WHERE i.system_id = ?{scope} ORDER BY b.id",
            system_id,
            environment_id,
            "i",
        )
        return {str(row["binding_id"]): str(row["status"]) for row in rows}

    def items_in_scope(self, *, system_id: str) -> Sequence[KnowledgeItem]:
        rows = self._store.query(
            "SELECT * FROM knowledge_item WHERE system_id = ? ORDER BY id", (system_id,)
        )
        return [_from_row(KnowledgeItem, dict(row)) for row in rows]

    def head_item_verifications(self, *, system_id: str) -> Mapping[str, str | None]:
        rows = self._store.query(
            "SELECT k.id AS item_id, rev.verification AS verification FROM knowledge_item k "
            "JOIN knowledge_revision rev ON rev.id = k.current_revision_id "
            "WHERE k.system_id = ? ORDER BY k.id",
            (system_id,),
        )
        return {
            str(row["item_id"]): None if row["verification"] is None else str(row["verification"])
            for row in rows
        }

    def audience_counts(self, *, system_id: str) -> Mapping[str, int]:
        rows = self._store.query(
            "SELECT a.item_id AS item_id, COUNT(*) AS tally FROM audience_tag a "
            "JOIN knowledge_item k ON k.id = a.item_id "
            "WHERE k.system_id = ? GROUP BY a.item_id ORDER BY a.item_id",
            (system_id,),
        )
        return {str(row["item_id"]): int(row["tally"]) for row in rows}

    def boundaries_for_system(self, *, system_id: str) -> Sequence[ObservabilityBoundary]:
        rows = self._store.query(
            "SELECT * FROM observability_boundary WHERE system_id = ? ORDER BY id", (system_id,)
        )
        return [_from_row(ObservabilityBoundary, dict(row)) for row in rows]


class SqliteFreshnessRecords:
    """The SQLite implementation of `adopt_freshness.FreshnessRecords`.

    Structurally satisfied, like `SqliteCoverageRecords`, and read-only in the
    strong sense: there is no write method on the class at all, so
    `resolve_freshness` could not write even if it tried.
    """

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def get_item(self, item_id: str) -> KnowledgeItem | None:
        return _one(
            self._store, KnowledgeItem, "SELECT * FROM knowledge_item WHERE id = ?", (item_id,)
        )

    def bindings_for_item(self, item_id: str) -> Sequence[Binding]:
        rows = self._store.query("SELECT * FROM binding WHERE item_id = ? ORDER BY id", (item_id,))
        return [_from_row(Binding, dict(row)) for row in rows]

    def head_binding_statuses(self, binding_ids: Sequence[str]) -> Mapping[str, str]:
        if not binding_ids:
            return {}
        rows = self._store.query(
            # S608: `_placeholders` emits only `?` characters, one per id.
            "SELECT b.id AS binding_id, rev.status AS status FROM binding b "  # noqa: S608
            "JOIN binding_revision rev ON rev.id = b.current_revision_id "
            f"WHERE b.id IN ({_placeholders(binding_ids)})",
            tuple(binding_ids),
        )
        return {str(row["binding_id"]): str(row["status"]) for row in rows}

    def head_identity_statuses(self, identity_ids: Sequence[str]) -> Mapping[str, str]:
        if not identity_ids:
            return {}
        rows = self._store.query(
            # S608: `_placeholders` emits only `?`; the rest is a module constant.
            "SELECT identity_id, status FROM identity_revision "  # noqa: S608
            f"WHERE identity_id IN ({_placeholders(identity_ids)}) "
            f"AND {_UNSUPERSEDED_IDENTITY_REVISION} ORDER BY created_at, id",
            tuple(identity_ids),
        )
        return {str(row["identity_id"]): str(row["status"]) for row in rows}

    def sensors_in_scope(self, *, system_id: str, environment_id: str | None) -> Sequence[Sensor]:
        if environment_id is None:
            rows = self._store.query(
                "SELECT * FROM sensor WHERE system_id = ? ORDER BY id", (system_id,)
            )
        else:
            rows = self._store.query(
                "SELECT * FROM sensor WHERE system_id = ? AND environment_id = ? ORDER BY id",
                (system_id, environment_id),
            )
        return [_from_row(Sensor, dict(row)) for row in rows]

    def latest_heartbeat_at(self, sensor_ids: Sequence[str]) -> Mapping[str, _dt.datetime]:
        if not sensor_ids:
            return {}
        rows = self._store.query(
            # S608: `_placeholders` emits only `?` characters, one per id.
            "SELECT sensor_id, MAX(observed_at) AS observed_at FROM sensor_heartbeat "  # noqa: S608
            f"WHERE sensor_id IN ({_placeholders(sensor_ids)}) GROUP BY sensor_id",
            tuple(sensor_ids),
        )
        return {
            str(row["sensor_id"]): _dt.datetime.fromisoformat(str(row["observed_at"])).astimezone(
                _dt.UTC
            )
            for row in rows
            if row["observed_at"] is not None
        }


def _placeholders(values: Sequence[str]) -> str:
    """`?, ?, ?` for an `IN` list.

    The count comes from the sequence length and never from caller text, so the
    interpolation carries no user-supplied characters at all.
    """
    return ", ".join("?" for _ in values)
