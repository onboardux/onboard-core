"""CLI cold start stays inside `CLI_COLD_START_MS`.

Implementation spec §2.3 gives `CLI_COLD_START_MS` a gate and §6 requires one
perf harness per NFR. It had neither: the constant's only appearance in the tree
was a sentence in `adapters/base.py` explaining why nothing provider-shaped is
imported at CLI start. That sentence is a claim about this number, and until now
nothing measured it -- so the one constant whose whole purpose is to keep imports
out of the startup path was the one nobody could breach visibly.

**Cold start is measured in a fresh interpreter, every sample.** Timing an
in-process call would measure a warm import cache and report a number no operator
ever experiences; the first `adopt` of a session is the one that hurts, and it is
the only one worth a budget. So each sample is a real `subprocess` launch.

**`version` is the command under test**, deliberately: contracts §14 defines it
as reporting build facts and it opens no store, touches no network and reads no
config file. What it measures is therefore import cost and nothing else, which is
exactly what the constant guards. Timing `doctor` instead would fold config
resolution into a number named for start-up.

Reports always. **Asserts only on the reference runner** (`bench/RUNNER.md`
rule 1): a number from a developer's laptop is an anecdote, and an anecdote that
fails a build teaches people to disable the build.
"""

import argparse
import statistics
import subprocess
import sys
import time
from typing import Final

from adopt_const import CLI_COLD_START_MS
from bench import REFERENCE_ENV, is_reference_runner

#: Enough samples for a p95 to mean something. Each one pays a full interpreter
#: start, so this is the largest count that keeps the harness inside its share of
#: the nightly job.
# const-sync: ok -- a sample count for this harness, not a tunable.
ITERATIONS: Final[int] = 20

#: Discarded before measuring. The first launch in a fresh checkout pays for
#: bytecode compilation of every module on the path, which is a one-off cost of
#: the machine rather than of the CLI -- and folding it into the p95 would make
#: the number depend on whether anything had run before.
# const-sync: ok -- a warm-up count, not a tunable.
WARMUP: Final[int] = 2

_MILLISECONDS_PER_SECOND: Final[float] = 1000.0

#: `-m adopt_cli.main` rather than the `adopt` console script: the script is a
#: generated shim whose own start-up cost varies by installer, and the budget is
#: about our imports.
_COMMAND: Final[tuple[str, ...]] = (sys.executable, "-m", "adopt_cli.main", "version", "--json")


#: An interpreter that imports nothing. Its cost is the floor under any CLI on
#: this machine, and reporting it is what makes a breach diagnosable: `RUNNER.md`
#: rule 2 says the first question on a failure is whether the *code* regressed,
#: and that question is unanswerable from a single number. A 1,900 ms p95 means
#: something very different at a 30 ms floor than at a 150 ms one.
_BASELINE: Final[tuple[str, ...]] = (sys.executable, "-c", "pass")


def _time(command: tuple[str, ...]) -> float:
    """Milliseconds for one cold launch of `command`."""
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, check=False)
    elapsed = (time.perf_counter() - started) * _MILLISECONDS_PER_SECOND
    if completed.returncode != 0:
        raise SystemExit(
            f"{' '.join(command[1:])} exited {completed.returncode}; "
            f"a cold-start number for a command that failed is meaningless.\n"
            f"{completed.stderr.decode(errors='replace')}"
        )
    return elapsed


def _p95(command: tuple[str, ...]) -> float:
    for _ in range(WARMUP):
        _time(command)
    samples = sorted(_time(command) for _ in range(ITERATIONS))
    # `quantiles` with n=100 gives the 95th cut point directly, and it is the same
    # estimator every other harness here uses.
    return statistics.quantiles(samples, n=100)[94]


def measure() -> tuple[float, float]:
    """p95 cold start and the empty-interpreter floor beneath it, in ms."""
    return _p95(_COMMAND), _p95(_BASELINE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--assert",
        dest="do_assert",
        action="store_true",
        help="Fail when the budget is breached on the reference runner.",
    )
    arguments = parser.parse_args(argv)

    measured, baseline = measure()
    print(f"empty interpreter:    p95 {baseline:.0f} ms  (the floor on this machine)")
    print(
        f"adopt version --json: p95 {measured:.0f} ms over {ITERATIONS} cold starts "
        f"(budget {CLI_COLD_START_MS} ms); our imports cost {measured - baseline:.0f} ms of it"
    )

    if not arguments.do_assert:
        return 0
    if not is_reference_runner():
        print(
            f"not the reference runner ({REFERENCE_ENV} is unset): reported, not asserted. "
            "See bench/RUNNER.md rule 1."
        )
        return 0
    if measured > CLI_COLD_START_MS:
        print(f"FAIL: CLI cold start breached -- {measured:.0f} ms exceeds {CLI_COLD_START_MS} ms")
        return 1
    print("PASS: CLI cold start within budget on the reference runner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
