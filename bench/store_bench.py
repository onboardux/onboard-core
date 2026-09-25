"""N3 -- opening a store stays inside `STORE_OPEN_P95_MS` at realistic size.

Measures what the requirement says: the time to open an existing store at
`STORE_OPEN_ITEM_COUNT` rows **including the `schema_meta` read** -- which is the
part that makes the measurement honest, because reading and appending that row is
work every open does and a benchmark that skipped it would report a number no
user ever experiences.

Reports always. **Asserts only on the reference runner** (`bench/RUNNER.md`
rule 1): a number from a developer's laptop is an anecdote, and an anecdote that
fails a build teaches people to disable the build.

The store is populated once and reopened `ITERATIONS` times, because opening is
the operation under test. Creating a store per sample would measure schema
creation, which is N1's job and already has its own harness.

**The budget judges the open net of the disk (OD-17, ruled 2026-09-25).** Every
open commits its `schema_meta` row into a fresh WAL, and SQLite makes that
durable with three syncs: the WAL header, the directory entry for the file it
just created, and the commit (counted with `strace` on Linux, SQLite 3.46). What
a sync costs belongs to the host. On 2026-09-24 this p95 read 225.8 ms on one
machine and 2.5 ms on a re-run of the same commit on another (N49), and the
`ubuntu-24.04` label hands out six CPU models, each with its own disk. So every
open is paired with a **floor sample**: bare SQLite committing one page into a
fresh WAL in the same directory, which is the same three syncs with none of this
repository's code in it. The two alternate, so a stall that slows an open slows
the floor beside it, and the gate fails when p95(open) - p95(floor) exceeds the
budget.

**What that keeps** is everything the code does: our pragmas, the version read,
the `schema_meta` append, anything that scales with the 50,000 rows, and any
*extra* durable I/O a change adds, because the floor subtracts one commit's
syncs and no more. **What it drops** is only the speed of the disk. On the hosts
that have never breached, the floor is under a millisecond and the verdict is
the one the raw p95 would have given.
"""

import argparse
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

from adopt_const import STORE_OPEN_P95_MS
from adopt_store import open_store
from bench import REFERENCE_ENV, is_reference_runner

#: Enough samples for a p95 to mean something, few enough to stay inside the
#: benchmark job's share of the pipeline.
# const-sync: ok -- a sample count for this harness, not a tunable.
ITERATIONS: Final[int] = 40

#: The row count N3 names. Scope rows are the only ones S2 can write, so the
#: population is environments -- the deepest level, which exercises the whole
#: parent chain on every insert.
# const-sync: ok -- the N3 measurement size, stated in the NFR rather than tuned.
STORE_OPEN_ITEM_COUNT: Final[int] = 50_000

_MILLISECONDS_PER_SECOND: Final[float] = 1000.0
_P95: Final[float] = 0.95


def _percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, round(_P95 * len(ordered)) - 1)
    return ordered[index]


def _populate(path: Path) -> None:
    with open_store(path, migrate=True) as handle:
        scope = handle.scope()
        firm = scope.create_firm(slug="northwind", name="Northwind LLP")
        engagement = scope.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
        system = scope.create_system(
            engagement_id=engagement.id, slug="orders-api", name="Orders API"
        )
        # One transaction for the whole population: per-row commits would measure
        # fsync throughput, and the store under test is one a client already has.
        with handle.backend.transaction():
            for index in range(STORE_OPEN_ITEM_COUNT):
                scope.create_environment(
                    system_id=system.id, slug=f"env-{index:06d}", name="synthetic"
                )


def _prepare_floor(path: Path) -> None:
    """A database whose only content is its header, already in WAL mode.

    `user_version` is the write, rather than a row, because a table would need a
    `CREATE TABLE` here and `no-foreign-tables` keeps those in the migrations.
    Setting it rewrites page 1, which is one durable page, as the open's append is.
    """
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA journal_mode = WAL;")
    connection.execute("PRAGMA user_version = 1;")
    connection.close()


def _floor_sample(path: Path, version: int) -> float:
    started = time.perf_counter()
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA journal_mode = WAL;")
    connection.execute(f"PRAGMA user_version = {int(version)};")
    elapsed = time.perf_counter() - started
    # Outside the timer, as `handle.close()` is outside an open. Closing the last
    # connection checkpoints and deletes the WAL, so the next sample creates it
    # afresh, exactly as the next open does.
    connection.close()
    return elapsed * _MILLISECONDS_PER_SECOND


def _paired_samples(store: Path, floor: Path) -> tuple[list[float], list[float]]:
    opens: list[float] = []
    floors: list[float] = []
    for index in range(ITERATIONS):
        started = time.perf_counter()
        handle = open_store(store)
        elapsed = time.perf_counter() - started
        handle.close()
        opens.append(elapsed * _MILLISECONDS_PER_SECOND)
        # A changing value, so every sample is a real write; 1 is the prepared one.
        floors.append(_floor_sample(floor, index + 2))
    return opens, floors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--assert",
        dest="do_assert",
        action="store_true",
        help="Fail when the budget is breached on the reference runner.",
    )
    arguments = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as scratch:
        store = Path(scratch) / "bench.db"
        floor_db = Path(scratch) / "floor.db"
        _populate(store)
        _prepare_floor(floor_db)
        opens, floors = _paired_samples(store, floor_db)

    measured = _percentile_95(opens)
    floor = _percentile_95(floors)
    net = measured - floor
    print(
        f"store open p95: {measured:.1f} ms over {ITERATIONS} opens "
        f"at {STORE_OPEN_ITEM_COUNT:,} rows"
    )
    print(
        f"  spread: min {min(opens):.1f} / median {statistics.median(opens):.1f} "
        f"/ max {max(opens):.1f} ms"
    )
    print(
        f"disk floor p95: {floor:.1f} ms (bare SQLite committing one page into a fresh "
        "WAL in the same directory, sampled beside each open)"
    )
    print(f"store open net of the disk: {net:.1f} ms (budget {STORE_OPEN_P95_MS} ms)")

    if not arguments.do_assert:
        return 0
    if not is_reference_runner():
        print(
            f"not the reference runner ({REFERENCE_ENV} is unset): reported, not asserted. "
            "See bench/RUNNER.md rule 1."
        )
        return 0
    if net > STORE_OPEN_P95_MS:
        print(f"FAIL: N3 breached -- {net:.1f} ms net of the disk exceeds {STORE_OPEN_P95_MS} ms")
        return 1
    if measured > STORE_OPEN_P95_MS:
        print(
            f"  the raw p95 exceeds the budget and this disk's floor accounts for it "
            f"(OD-17): {measured:.1f} ms open, {floor:.1f} ms floor"
        )
    print("PASS: N3 within budget on the reference runner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
