"""Every dedicated performance harness, one run, one verdict.

    uv run python -m bench.all --assert

The sprint plan has named this command since v2.0 and the module did not exist,
so the line could not be run literally -- the class of defect CR-30, CR-32 and
CR-40 each closed for a different command. It is a **runner**, not a seventh
measurement: every number below is produced by the harness that owns it, and this
file adds no timing of its own. Two homes for one measurement is one of them
drifting.

**What it is for.** Seven of PRD Q4's twelve constants have dedicated benchmark
harnesses. Ratifying those one command at a time invites a partial answer --
five green, two forgotten, and a table that records the five. This runs all
seven and **reports every breach rather than stopping at the first**.

**Asserts only on the reference runner.** Each harness already enforces
`bench/RUNNER.md` rule 1 for itself; this runner does not second-guess them, it
simply passes `--assert` down and reports what each returned. Off the reference
runner every harness reports and returns 0, so this command exits 0 there too --
**which is not evidence that a budget holds.** The nightly `perf` job is where
the assertion lives.

**Not run here:** `BINARY_MAX_MB`, the hosted conformance duration, the unit/PR
CI ratchets, and the coverage floor. Those are measured by the release,
conformance, ordinary CI, and coverage workflows respectively. Calling those
benchmark results would make this command claim evidence it never observed.
`schema_bench`'s Postgres half degrades to SQLite-only when
`ADOPT_BENCH_PG_DSN` is unset, which the harness itself reports.
"""

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Final

from bench import REFERENCE_ENV, is_reference_runner

__all__ = ["HARNESSES", "Result", "main", "run_one"]

#: Every dedicated benchmark harness, in the order the NFR table lists them.
#: NFRs measured by other workflows deliberately do not appear here.
HARNESSES: Final[tuple[tuple[str, str], ...]] = (
    ("N1", "bench.schema_bench"),
    ("N3", "bench.store_bench"),
    ("N4", "bench.export_bench"),
    ("N5", "bench.uri_bench"),
    ("N6", "bench.coverage_bench"),
    ("N7", "bench.freshness_bench"),
    ("CLI", "bench.cli_bench"),
)


@dataclass(frozen=True)
class Result:
    """One harness's outcome."""

    nfr: str
    module: str
    exit_code: int
    seconds: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def run_one(nfr: str, module: str, *, do_assert: bool) -> Result:
    """Run one harness in its own interpreter.

    Separate processes rather than imported `main()` calls: several harnesses
    populate 50,000-row stores, and a shared interpreter would let one harness's
    memory and import state shape the next one's numbers.
    """
    print(f"\n=== {nfr} -- {module} ===", flush=True)
    argv = [sys.executable, "-m", module] + (["--assert"] if do_assert else [])
    started = time.monotonic()
    completed = subprocess.run(argv, check=False)
    return Result(
        nfr=nfr, module=module, exit_code=completed.returncode, seconds=time.monotonic() - started
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--assert",
        dest="do_assert",
        action="store_true",
        help="Fail when any NFR constant is breached on the reference runner.",
    )
    arguments = parser.parse_args(argv)

    results = [run_one(nfr, module, do_assert=arguments.do_assert) for nfr, module in HARNESSES]

    print("\n=== bench.all ===")
    for result in results:
        print(
            f"  {result.nfr:<4} {result.module:<22} "
            f"{'OK' if result.ok else 'BREACHED':<9} {result.seconds:6.1f}s"
        )

    breached = [result for result in results if not result.ok]
    if not arguments.do_assert:
        print(f"\nreported only ({len(results)} harnesses); pass --assert to gate")
        return 0
    if not is_reference_runner():
        print(
            f"\nnot the reference runner ({REFERENCE_ENV} is unset): every harness reported "
            "and none asserted. This exit code is NOT evidence that a budget holds -- "
            "see bench/RUNNER.md rule 1."
        )
        return 0
    if breached:
        for result in breached:
            print(f"::error::bench.all: {result.nfr} ({result.module}) breached its budget")
        return 1
    print(f"\nbench.all: OK -- all {len(results)} benchmark budgets hold on the reference runner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
