"""N1 -- the current schema creates cleanly in both dialects within the budget.

Measures what the requirement actually says: the time to take an empty store to
`SCHEMA_VERSION` by applying **every** generated migration in order. SQLite is
measured in process; Postgres is measured through `psql` when `ADOPT_BENCH_PG_DSN`
names a database, so the open repository does not acquire a Postgres driver
dependency it has no other use for.

**The ladder is read from the directory, never listed here.** This module named
`0001__init_v3.sql` in both dialects, which was correct while that was the only
tranche and silently wrong the moment Build 4 emitted `0002__coverage_gap.sql`:
the benchmark then created a version-3 store, compared its `user_version`
against `SCHEMA_VERSION` (4) and exited non-zero, so the nightly `bench` on
`main` went red the first night after the Builds 1-10 merge -- reporting the
tree as breaching N1 when nothing about N1 had changed. That is
`plane_store.postgres.connection.apply_generated_schema`'s Build 7 defect and
`release.yml`'s hard-coded `"schema_version": 3` (T1.12) in a third costume: a
hand-kept file list is a second source of truth for the schema, and it falls
behind the first time somebody adds a tranche. `test_ci_gates.py` now fails on a
migration filename written down in this file.

Reports always. **Asserts only on the reference runner** (`bench/RUNNER.md`
rule 1). When a dialect cannot be measured it says so and does not quietly
substitute the other one -- half a measurement presented as a whole is how a
number stops meaning anything.
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

from adopt_const import SCHEMA_CREATE_P95_SECONDS, SCHEMA_VERSION
from bench import REFERENCE_ENV, is_reference_runner

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
MIGRATIONS: Final[Path] = REPO_ROOT / "schema" / "migrations"
DSN_ENV: Final[str] = "ADOPT_BENCH_PG_DSN"

#: Enough samples for a p95 to mean something, few enough to stay inside the
#: benchmark job's share of the release pipeline.
# const-sync: ok -- a sample count for this harness, not COVERAGE_RECOMPUTE_P95_SECONDS.
ITERATIONS: Final[int] = 20


def _percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, round(0.95 * len(ordered)) - 1)
    return ordered[index]


def _ladder(dialect: str) -> list[Path]:
    """Every generated migration for one dialect, in application order.

    Sorted by filename, which is the order the emitter numbers them in and the
    order `apply_generated_schema` and `adopt_store.sqlite.store` both apply
    them. An empty ladder is a refusal rather than a zero-second measurement:
    a benchmark with nothing to run is the fastest possible benchmark and means
    nothing at all.
    """
    directory = MIGRATIONS / dialect
    found = sorted(directory.glob("*.sql")) if directory.is_dir() else []
    if not found:
        raise SystemExit(
            f"no generated {dialect} migrations under {directory}. "
            "Run `uv run adopt-schema generate` -- N1 measures creating the schema, and "
            "an empty ladder would report a perfect score for building nothing."
        )
    return found


def _sqlite_samples() -> list[float]:
    ladder = [path.read_text(encoding="utf-8") for path in _ladder("sqlite")]
    samples: list[float] = []
    with tempfile.TemporaryDirectory() as scratch:
        for iteration in range(ITERATIONS):
            path = Path(scratch) / f"bench_{iteration}.db"
            started = time.perf_counter()
            connection = sqlite3.connect(path)
            try:
                for sql in ladder:
                    connection.executescript(sql)
                connection.commit()
            finally:
                connection.close()
            samples.append(time.perf_counter() - started)

            # Closed explicitly, not left to the garbage collector: an open
            # handle blocks the scratch directory's removal on Windows, and a
            # benchmark that leaks handles is measuring a different program.
            check = sqlite3.connect(path)
            try:
                version = check.execute("PRAGMA user_version;").fetchone()[0]
            finally:
                check.close()
            if version != SCHEMA_VERSION:
                raise SystemExit(
                    f"the created store reports user_version {version}, not {SCHEMA_VERSION}. "
                    "The benchmark is measuring something that is not the current schema."
                )
    return samples


def _postgres_samples(dsn: str) -> list[float]:
    psql = shutil.which("psql")
    if psql is None:
        raise SystemExit(
            f"{DSN_ENV} is set but `psql` is not on PATH, so the Postgres dialect cannot be "
            "measured. Install the client or unset the variable -- do not let the run report "
            "a SQLite-only number as if it covered both dialects."
        )
    ladder = _ladder("postgres")
    samples: list[float] = []
    for iteration in range(ITERATIONS):
        schema = f"bench_{iteration}"
        started = time.perf_counter()
        script = f"DROP SCHEMA IF EXISTS {schema} CASCADE; CREATE SCHEMA {schema};"
        subprocess.run(
            [psql, dsn, "-v", "ON_ERROR_STOP=1", "-q", "-c", script],
            check=True,
            capture_output=True,
        )
        for sql_path in ladder:
            subprocess.run(
                [
                    psql,
                    dsn,
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-q",
                    "-c",
                    f"SET search_path TO {schema};",
                    "-f",
                    str(sql_path),
                ],
                check=True,
                capture_output=True,
            )
        samples.append(time.perf_counter() - started)
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--assert",
        dest="do_assert",
        action="store_true",
        help="Fail when a measured dialect breaches the budget on the reference runner.",
    )
    args = parser.parse_args(argv)

    results: dict[str, float] = {"sqlite": _percentile_95(_sqlite_samples())}
    dsn = os.environ.get(DSN_ENV)
    if dsn:
        results["postgres"] = _percentile_95(_postgres_samples(dsn))

    reference = is_reference_runner()
    print(f"bench.schema_bench: {ITERATIONS} iterations per dialect")
    for dialect, p95 in results.items():
        print(f"  {dialect}: p95 {p95:.3f}s (budget {SCHEMA_CREATE_P95_SECONDS}s)")
    if "postgres" not in results:
        print(f"  postgres: NOT MEASURED -- {DSN_ENV} is unset. N1 covers both dialects.")

    if not reference:
        print(
            f"reporting only: {REFERENCE_ENV} is not set, so this is not the reference runner "
            "pinned in bench/RUNNER.md. A constant is never ratified against another machine."
        )
        return 0

    breached = {d: p for d, p in results.items() if p > SCHEMA_CREATE_P95_SECONDS}
    if breached and args.do_assert:
        for dialect, p95 in breached.items():
            print(f"BREACH: {dialect} p95 {p95:.3f}s exceeds SCHEMA_CREATE_P95_SECONDS.")
        print(
            "A constant is retuned, never a benchmark (bench/RUNNER.md rule 2). Ask first "
            "whether the code regressed."
        )
        return 1
    print("bench.schema_bench: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
