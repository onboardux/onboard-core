"""The two storage ports the bundle writer and reader run on.

Declared here rather than imported from `adopt_store`, following the precedent
`adopt_scope.records` set and CR-34 extended to coverage and freshness:
`no-raw-sqlite` names `adopt_export` as a source module and import-linter follows
the chain, so a dependency on `adopt_store` would reach `sqlite3` transitively
and break the contract. A structural protocol costs one file and keeps this
package free of any dialect -- which is what makes "the same bundle whichever
store answered" a property of the writer rather than a coincidence.

**The ports fetch and apply rows; they never decide.** Row order, the scope
refusal, digest verification and the all-or-nothing boundary are all the
writer's and the reader's. Pushing any of them into a realization would mean the
byte-identical round-trip was a property of whichever store ran, and the
round-trip property test would be comparing two callers of one query.

**`insert_rows` takes whole validated models, never a column subset.** That is
deliberate and load-bearing: `identity.covered_cache` is an exportable column,
and a port that could write *some* columns of `identity` would be the general
setter `no-covered-cache-write` exists to forbid. Import restores rows exactly as
a bundle recorded them, into an empty store, or it refuses.
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from pydantic import BaseModel

__all__ = ["ExportRecords", "ImportRecords"]


class ExportRecords(Protocol):
    """Read-only. No SQL, connection or cursor crosses this boundary."""

    def firm_slugs(self) -> Sequence[str]:
        """Every `firm.slug` in the store, and the writer decides what to do.

        Reported rather than resolved because `manifest.json` names **one** firm
        (contracts §11) and refusing is the writer's judgement. A port that
        returned "the" firm slug would have to pick one, and picking one for a
        store holding two produces a bundle labelled with a scope half its rows
        do not belong to -- which nothing downstream can detect.
        """
        ...

    def engagement_slugs(self) -> Sequence[str]:
        """Every `engagement.slug` in the store. Same argument as `firm_slugs`."""
        ...

    def table_rows[TModel: BaseModel](
        self, table: str, model_type: type[TModel]
    ) -> Sequence[TModel]:
        """Every row of one table, validated against its generated model.

        Unordered by contract. The writer sorts by the manifest's primary key,
        so the emitted order is a pure function of (manifest, rows) and identical
        whichever realization answered.
        """
        ...


class ImportRecords(Protocol):
    """Write side. Whole rows only, inside the caller's transaction."""

    def row_count(self, table: str) -> int:
        """How many rows one table already holds.

        The reader sums this across every exportable table to decide
        `EXPORT_TARGET_NOT_EMPTY`, and names the first non-empty table in the
        message -- "the store is not empty" sends an operator looking, and
        "`firm` already holds 1 row" tells them what they are about to lose.
        """
        ...

    def insert_rows(self, table: str, models: Sequence[BaseModel]) -> None:
        """Append whole rows. The model is the column authority, as everywhere else."""
        ...

    def transaction(self) -> AbstractContextManager[None]:
        """The one boundary the whole import commits or rolls back inside."""
        ...
