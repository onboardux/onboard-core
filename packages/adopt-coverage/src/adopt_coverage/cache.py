"""The coverage cache write. **The only one in either repository.**

`no-covered-cache-write` scans every `.py` string literal and every `.sql` line
under `packages/`, `scripts/`, `tools/`, `bench/` and `schema/` for the cache
columns named alongside a write keyword, and `packages/adopt-coverage` is the
only source path it permits. That is why the statement is here and not beside the
other `identity` writes in `adopt_store.sqlite.records`, where it would be
rejected by the gate -- correctly, because a setter reachable from the store is a
setter every caller can reach.

**Why this module holds SQL when nothing else in the package does.** The
alternatives were each worse and are recorded so they are not re-proposed:

* a generic ``update_identity_columns(id, mapping)`` on the store passes the
  regex while opening a *wider* hole than the specific setter it replaces --
  every column becomes writable by every caller, which is dodging the gate
  rather than satisfying it (CR-24: a gate people work around stops meaning
  anything);
* splitting the statement so the column and the keyword land on different lines
  is the same dodge, less visible;
* adding ``adopt_store.sqlite`` to the contract's ``allowed_paths`` makes the
  write reachable by anyone holding a store, which is the invariant itself.

The executor is a **structural** protocol, so this package imports no store
module and no chain reaches `sqlite3` -- `no-raw-sqlite` names `adopt_coverage`
as a source module and would otherwise reject it.

**Known cost, stated rather than discovered later.** One SQL statement lives in a
package that is otherwise dialect-free, and the parameter marker differs under
psycopg. No sprint assigns the Postgres realization of coverage; whichever one
does inherits this seam and this note.
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Final, Protocol

from adopt_coverage.recompute import CoverageResult
from adopt_obs import format_timestamp

__all__ = ["CacheWriter", "rebuild_cache"]

#: The statement, in one place. Parameter order is (covered, at, identity_id).
_WRITE_CACHE: Final[str] = (
    "UPDATE identity SET covered_cache = ?, covered_cache_at = ? WHERE id = ?"
)


class CacheWriter(Protocol):
    """The two operations rebuilding the cache needs, and nothing else.

    Satisfied structurally by `adopt_store.sqlite.store.SqliteStore`. Deliberately
    **not** an import of that class: this package may not reach `sqlite3` even
    transitively, and a protocol this narrow cannot be used to write anything the
    caller did not already have the statement for.
    """

    def transaction(self) -> AbstractContextManager[None]: ...
    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> None: ...


def rebuild_cache(writer: CacheWriter, result: CoverageResult) -> int:
    """Rebuild `covered_cache` from a recompute result. **Never the reverse.**

    The direction is the whole contract (PRD F7.4, CUJ-3 step 4). This function
    takes a `CoverageResult` and no store-read of its own precisely so that there
    is no expression here in which the cache could influence the value written.

    Args:
        writer: The store to write through.
        result: What `recompute_coverage` decided.

    Returns:
        How many rows were written -- every identity in scope, not only the
        disagreeing ones. A cache rebuilt only where it disagreed would leave
        `covered_cache_at` lying about when the rest was last confirmed.
    """
    stamp = format_timestamp(result.computed_at)
    rows: Sequence[tuple[object, ...]] = [
        (int(entry.covered), stamp, entry.identity_id) for entry in result.identities
    ]
    with writer.transaction():
        for parameters in rows:
            writer.execute(_WRITE_CACHE, parameters)
    return len(rows)
