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
"""

import argparse
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


def _open_samples(path: Path) -> list[float]:
    samples: list[float] = []
    for _ in range(ITERATIONS):
        started = time.perf_counter()
        handle = open_store(path)
        elapsed = time.perf_counter() - started
        handle.close()
        samples.append(elapsed * _MILLISECONDS_PER_SECOND)
    return samples


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
        path = Path(scratch) / "bench.db"
        _populate(path)
        samples = _open_samples(path)

    measured = _percentile_95(samples)
    print(
        f"store open p95: {measured:.1f} ms over {ITERATIONS} opens "
        f"at {STORE_OPEN_ITEM_COUNT:,} rows (budget {STORE_OPEN_P95_MS} ms)"
    )

    if not arguments.do_assert:
        return 0
    if not is_reference_runner():
        print(
            f"not the reference runner ({REFERENCE_ENV} is unset): reported, not asserted. "
            "See bench/RUNNER.md rule 1."
        )
        return 0
    if measured > STORE_OPEN_P95_MS:
        print(f"FAIL: N3 breached -- {measured:.1f} ms exceeds {STORE_OPEN_P95_MS} ms")
        return 1
    print("PASS: N3 within budget on the reference runner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
