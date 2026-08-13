"""The storage ports `adopt_map` runs on -- and why it declares its own.

`no-raw-sqlite` names `adopt_map` as a source module and import-linter follows
**indirect** chains, so nothing here may reach `sqlite3` even transitively. That
is the contract working as intended rather than an obstacle: a module that knows
it is talking to SQLite is a module the Postgres realization has to reimplement.
So this package declares protocols and is handed a realization that satisfies
them structurally, exactly as `adopt_coverage`, `adopt_scope` and `adopt_export`
are (CR-34/CR-37's pattern).

**Which realization, and why these two ports (`docs/pack/OPEN-DECISIONS.md` OD-1,
OD-2).** `02` §6 requires Build 1 to write `provenance`, `death_condition`,
`conflict` and `audit_event`. All four have generated models and registered id
prefixes, and **none has a facade or a port anywhere in `adopt-core`** --
`adopt-store` is protected (`03` §4), so Build 1 cannot add one. Build 0 exposes
exactly two general-purpose whole-table accessors on the store handle, and they
are what these protocols are shaped to:

* `Store.export_records()` -- `table_rows(table, model_type)` reads any canonical
  table as generated models. `ScopeLookupRecords` below is that method and
  nothing else, because Build 0's `ScopeRecords` has no get-by-id for firm,
  engagement or environment and no way to list a system's environments, which
  `MAP_ENVIRONMENT_AMBIGUOUS` needs in order to list anything (OD-2).
* `Store.import_records()` -- `insert_rows(table, models)` inserts whole
  generated-model rows into any canonical table. **Whole rows only**: the column
  list derives from the model, so there is no argument through which a caller
  could name a subset, and therefore no way to use it as the per-column setter
  `no-covered-cache-write` forbids.

The protocols are deliberately **narrower than the classes that satisfy them**.
`SurfaceAuxRecords` names one insert method, so nothing in this package can reach
for a read it was not given, and a reviewer can see the whole write surface in
four lines.

**What is not here, on purpose.** No `update`, no `delete`, no `execute`. The
append-only guarantee is expressed as an absent method rather than as a rule
somebody has to remember: a caller cannot reach for a mutation that has no name.
The revision families are written through Build 0's `RevisionWriter`, which this
package receives whole.
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from pydantic import BaseModel

__all__ = ["ScopeLookupRecords", "SurfaceAuxRecords"]


class ScopeLookupRecords(Protocol):
    """Read any canonical table as generated models.

    Satisfied structurally by `adopt_store.sqlite.SqliteExportRecords`, obtained
    from `Store.export_records()`. Rows come back **unordered** and this package
    orders what it needs: ordering supplied by a realization would make Build 1's
    behaviour a property of one dialect's collation.
    """

    def table_rows[TModel: BaseModel](
        self, table: str, model_type: type[TModel]
    ) -> Sequence[TModel]: ...


class SurfaceAuxRecords(Protocol):
    """Insert whole rows into the four `02` §6 tables that have no facade.

    `provenance`, `death_condition`, `conflict` and `audit_event`. Satisfied
    structurally by `adopt_store.sqlite.SqliteImportRecords`, obtained from
    `Store.import_records()`.

    Nested `transaction()` use joins the outermost transaction, which is what
    lets the writer wrap an entire run in one (`02` §6, PRD F3.3).
    """

    def insert_rows(self, table: str, models: Sequence[BaseModel]) -> None: ...

    def transaction(self) -> AbstractContextManager[None]: ...
