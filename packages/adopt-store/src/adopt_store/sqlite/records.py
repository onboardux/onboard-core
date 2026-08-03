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
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Final

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
    ProbeDefinition,
    ProbeDefinitionRevision,
    System,
    SystemLifecycleEvent,
)
from adopt_model._enums import FreshnessState, LifecycleState
from adopt_obs import format_timestamp
from adopt_store.sqlite.store import SqliteStore

__all__ = [
    "SqliteBindingRecords",
    "SqliteIdentityRecords",
    "SqliteKnowledgeRecords",
    "SqliteProbeRecords",
    "SqliteRevisionRecords",
    "SqliteScopeRecords",
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


def _to_row(model: BaseModel) -> dict[str, Any]:
    """Model -> column values, timestamps rendered and booleans stored as 0/1.

    The generated model is the authority on what a row contains: it is closed
    with ``extra="forbid"``, so a column invented here would fail construction
    long before it could reach the file.
    """
    values: dict[str, Any] = {}
    for name, value in model.model_dump().items():
        if isinstance(value, _dt.datetime):
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
    """
    model = model_type.model_validate(dict(row))
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
