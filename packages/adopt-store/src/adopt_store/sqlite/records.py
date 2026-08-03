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
from typing import Any

from pydantic import BaseModel

from adopt_model import Engagement, Environment, Firm, System, SystemLifecycleEvent
from adopt_model._enums import LifecycleState
from adopt_obs import format_timestamp
from adopt_store.sqlite.store import SqliteStore

__all__ = ["SqliteScopeRecords"]


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


class SqliteScopeRecords:
    """The SQLite implementation of `adopt_scope.ScopeRecords`."""

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    # -- unit of work -----------------------------------------------------

    def transaction(self) -> AbstractContextManager[None]:
        return self._store.transaction()

    # -- inserts ----------------------------------------------------------

    def _insert(self, table: str, model: BaseModel) -> None:
        values = _to_row(model)
        columns = list(values)
        self._store.execute(_insert_sql(table, columns), tuple(values[c] for c in columns))

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
        rows = self._store.query(sql, parameters)
        return None if not rows else _from_row(model_type, dict(rows[0]))

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
