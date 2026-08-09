"""`suite-health`: runtime delta, flake list, test-count delta vs behavior delta.

`05` S9's hardening item, and the cadence `test-generation-discipline` prescribes:

> a 15-minute per-sprint suite health check -- runtime delta, flake list, and
> test-count delta vs. behavior delta (**count growing faster than behaviors is
> the early bloat signal**).

**This reports; it never gates.** Implementation spec §6 bans test count as a
target outright, and a gate on a count is a target with a stricter face -- it
would be satisfied by deleting tests, which is precisely the wrong lever. The
ratchet in `ci_ratchet.py` is the gate that exists, and it gates *runtime*,
because runtime is a cost the whole team pays and a count is not.

**The behavior column is entered by a human, and that is deliberate.** A script
can count test functions; nothing can count behaviors, because a behavior is a
claim about the product and a table-driven test asserting five of them is one
function. Deriving it would produce a number that looks measured and is not --
the fake precision the standing-claims discipline forbids. So the baseline
records what each sprint claimed, the script does the arithmetic, and a sprint
that adds forty tests for two behaviors shows up as the ratio it is.

**The flake list is what a re-run disagreed about**, not a guess. `--flakes`
runs the deterministic suites twice and reports any node whose outcome changed.
At unit and property level the flake budget is zero, so a non-empty list is a
finding rather than a statistic.

Usage:
    python scripts/suite_health.py --report
    python scripts/suite_health.py --flakes
    python scripts/suite_health.py --record --sprint S9 --behaviors 6
"""

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
BASELINE: Final[Path] = REPO_ROOT / "tests" / "suite-health.json"

__all__ = ["Snapshot", "collect", "deltas", "main"]

#: `-m unit or property` -- the suites whose flake budget is zero and whose
#: runtime the ratchet gates. Conformance and durability are excluded because one
#: needs a credential and the other spawns and kills processes, so their runtime
#: is a statement about the environment.
_DETERMINISTIC: Final[tuple[str, ...]] = ("-m", "unit or property")

_COUNT_PATTERN: Final[re.Pattern[str]] = re.compile(r"(\d+) passed")


@dataclass(frozen=True)
class Snapshot:
    """One sprint's suite facts."""

    sprint: str
    tests: int
    behaviors: int
    seconds: float

    @property
    def tests_per_behavior(self) -> float | None:
        return None if self.behaviors == 0 else self.tests / self.behaviors


def _load() -> list[Snapshot]:
    if not BASELINE.exists():
        return []
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    return [Snapshot(**entry) for entry in raw["snapshots"]]


def collect() -> tuple[int, float]:
    """Run the deterministic suites once. Returns (passed, seconds)."""
    started = time.monotonic()
    completed = subprocess.run(
        # No explicit `-q`: `pyproject.toml`'s addopts already passes one, and a
        # second switches pytest to a per-file summary with no "N passed" line at
        # all -- the same trap CR-40 recorded when the durability count counted
        # files instead of tests.
        [sys.executable, "-m", "pytest", *_DETERMINISTIC],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        # const-sync: ok -- how many characters of pytest output to echo, not a tunable.
        tail = 2000
        raise SystemExit(
            f"the suite exited {completed.returncode}. Suite health measures a green "
            f"suite; numbers from a red one describe which tests reached the end.\n"
            f"{completed.stdout[-tail:]}"
        )
    match = _COUNT_PATTERN.search(completed.stdout)
    if match is None:
        raise SystemExit(
            "could not read a test count from pytest's output. Reporting 0 here would "
            "look like a suite that shrank to nothing."
        )
    return int(match.group(1)), elapsed


def deltas(history: list[Snapshot], current: Snapshot) -> list[str]:
    """Human-readable deltas against the previous snapshot."""
    if not history:
        return ["no previous snapshot -- this is the baseline"]
    previous = history[-1]
    lines = [
        f"tests      {previous.tests:>5} -> {current.tests:<5} "
        f"({current.tests - previous.tests:+d})",
        f"behaviors  {previous.behaviors:>5} -> {current.behaviors:<5} "
        f"({current.behaviors - previous.behaviors:+d})",
        f"runtime    {previous.seconds:>5.1f}s -> {current.seconds:<5.1f}s "
        f"({current.seconds - previous.seconds:+.1f}s)",
    ]
    added_tests = current.tests - previous.tests
    added_behaviors = current.behaviors - previous.behaviors
    if added_behaviors > 0:
        ratio = added_tests / added_behaviors
        lines.append(f"this sprint added {ratio:.1f} test(s) per behavior")
        # No threshold: §6 bans the count as a target, so this names the signal
        # and leaves the judgement with a reader who knows what the sprint did.
        if ratio > previous.tests / max(previous.behaviors, 1):
            lines.append(
                "  ^ higher than the running average -- the early bloat signal. "
                "Check for a permutation storm or a pyramid duplicate."
            )
    elif added_tests > 0:
        lines.append(
            f"  ^ {added_tests} test(s) added and no behavior recorded. Either the "
            "baseline was not updated, or these tests assert something already asserted."
        )
    return lines


def _flakes() -> int:
    """Run the deterministic suites twice; report any node that disagreed."""
    outcomes: list[dict[str, str]] = []
    for attempt in (1, 2):
        print(f"--- run {attempt} ---", flush=True)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *_DETERMINISTIC, "-rA", "--tb=no"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        results: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            parts = line.split(" ", 1)
            # pytest's `-rA` short summary lines read `PASSED path::test`.
            if len(parts) == 2 and parts[0] in {"PASSED", "FAILED", "ERROR", "SKIPPED"}:
                results[parts[1].strip()] = parts[0]
        outcomes.append(results)

    first, second = outcomes
    disagreed = sorted(
        node
        for node in set(first) | set(second)
        if first.get(node, "ABSENT") != second.get(node, "ABSENT")
    )
    if not disagreed:
        print(f"\nflake list: empty over {len(first)} nodes, two runs. The budget is zero.")
        return 0
    print(f"\nflake list: {len(disagreed)} node(s) disagreed between two runs")
    for node in disagreed:
        print(f"  {node}: {first.get(node, 'ABSENT')} then {second.get(node, 'ABSENT')}")
    # Reported, not failed: a flake is de-flaked, never deleted, and this script
    # is the instrument that finds it rather than the gate that punishes it.
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="Measure and print the deltas.")
    parser.add_argument("--flakes", action="store_true", help="Run twice; list disagreements.")
    parser.add_argument("--record", action="store_true", help="Append a snapshot to the baseline.")
    parser.add_argument("--sprint", help="Sprint id for --record, e.g. S9.")
    parser.add_argument(
        "--behaviors",
        type=int,
        help="Behaviors this sprint added, from its test manifest. Entered by a human: "
        "nothing can count a behavior, and a derived number would be fake precision.",
    )
    arguments = parser.parse_args(argv)

    if arguments.flakes:
        return _flakes()
    if not (arguments.report or arguments.record):
        parser.error("give --report, --flakes or --record")

    history = _load()
    tests, seconds = collect()

    if arguments.record:
        if not arguments.sprint or arguments.behaviors is None:
            parser.error("--record needs --sprint and --behaviors")
        current = Snapshot(
            sprint=arguments.sprint,
            tests=tests,
            behaviors=arguments.behaviors,
            seconds=round(seconds, 1),
        )
    else:
        previous_behaviors = history[-1].behaviors if history else 0
        current = Snapshot(
            sprint="(uncommitted)",
            tests=tests,
            behaviors=previous_behaviors,
            seconds=round(seconds, 1),
        )

    print(f"\n=== suite health: {current.sprint} ===")
    print(f"deterministic suites: {tests} passed in {seconds:.1f}s\n")
    for line in deltas(history, current):
        print(f"  {line}")

    if arguments.record:
        history.append(current)
        BASELINE.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Suite health per sprint. `behaviors` is entered by a human from the "
                        "sprint's test manifest -- nothing can count a behavior, and a derived "
                        "number would be fake precision. Reported, never gated: implementation "
                        "spec §6 bans test count as a target."
                    ),
                    "snapshots": [vars(snapshot) for snapshot in history],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nrecorded to {BASELINE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
