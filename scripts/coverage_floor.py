"""`coverage-floor`: the alarm implementation spec §6 names and nothing read.

> Coverage survives as a floor alarm at `COVERAGE_FLOOR_CORE` on `adopt-store`,
> `adopt-schema`, `adopt-coverage`, `adopt-agent`, `adopt-workflow`, `plane-store`.

`COVERAGE_FLOOR_CORE` reached S9 with **no consumer anywhere in either tree** --
the one constant in §2.3 that measured nothing. This is its consumer.

**It is a floor alarm, and the distinction is the whole design.** §2.3 says
*"never a target"* and §6 bans coverage as a target outright. So:

- It **fails below the floor** -- an alarm that cannot fire is not an alarm, and
  a silently-passing one is worse than none.
- It **never rewards a rise**. There is no ratchet, no "coverage must not
  decrease" rule, and no per-file report. Adding that is how a floor becomes a
  target, and a target is what manufactures assertion-free tests.
- It reports **every** package's figure but judges only the six §6 names, so a
  reader can see the whole picture without the gate acquiring an opinion about
  packages the pack deliberately leaves alone. `adopt-cli` sits below the floor
  on purpose: `05`'s summary table budgets **zero dedicated tests** there and in
  `adopt-const`, because glue verified transitively by the E2E journeys is
  exactly what should not carry its own suite.

**One implementation, both repositories** -- the CR-29 pattern the licence gate
already set. `plane-store` is judged when this runs in `adopt-plane`; a package
absent from the tree is skipped rather than failed, because two copies of a
policy is one of them drifting.

**The suite it measures is `unit or property`**, not the full run. The
conformance and durability suites need a credential and a spawned process, so a
coverage number including them would move with what happened to be reachable --
and a floor that moves with the environment is not a floor.
"""

import argparse
import collections
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

# `COVERAGE_FLOOR_CORE` lives in `adopt_const` and **only** there: §2.3 declares
# it, §2.4 does not, and `constants-uniqueness` fails on a name in both modules.
# `adopt-plane` reaches it the same way it reaches this script -- from the sibling
# checkout, where `adopt_const` is already a resolved dependency (CR-29).
from adopt_const import COVERAGE_FLOOR_CORE  # noqa: E402

__all__ = ["FLOOR_PACKAGES", "Measurement", "breaches", "main", "summarize"]

#: Implementation spec §6, verbatim. A package not in this tree is skipped.
FLOOR_PACKAGES: Final[frozenset[str]] = frozenset(
    {
        "adopt-store",
        "adopt-schema",
        "adopt-coverage",
        "adopt-agent",
        "adopt-workflow",
        "plane-store",
    }
)

_SUITE: Final[tuple[str, ...]] = ("-m", "unit or property")


@dataclass(frozen=True)
class Measurement:
    """One package's line rate."""

    package: str
    covered: int
    statements: int

    @property
    def rate(self) -> float:
        # A package with no statements is vacuously covered. It cannot be under a
        # floor, and reporting 0% for it would send someone hunting for tests
        # that would assert nothing.
        return 1.0 if self.statements == 0 else self.covered / self.statements

    @property
    def judged(self) -> bool:
        return self.package in FLOOR_PACKAGES


def summarize(coverage_json: dict[str, object]) -> list[Measurement]:
    """Aggregate `coverage json` output per workspace package."""
    files = coverage_json.get("files", {})
    if not isinstance(files, dict):
        raise SystemExit(
            "coverage json produced no `files` mapping; a floor computed over nothing "
            "would report success by having nothing to measure."
        )
    totals: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for path, entry in files.items():
        parts = path.replace("\\", "/").split("/")
        if "packages" not in parts:
            continue
        index = parts.index("packages") + 1
        if index >= len(parts):
            continue
        summary = entry["summary"]
        bucket = totals[parts[index]]
        bucket[0] += int(summary["covered_lines"])
        bucket[1] += int(summary["num_statements"])
    return [
        Measurement(package=package, covered=covered, statements=statements)
        for package, (covered, statements) in sorted(totals.items())
    ]


def breaches(measurements: list[Measurement], *, floor: float = COVERAGE_FLOOR_CORE) -> list[str]:
    """The reasons the alarm fires. Empty when every judged package clears."""
    return [
        f"{m.package} is at {m.rate:.1%}, below the {floor:.0%} floor "
        f"({m.covered}/{m.statements} statements)"
        for m in measurements
        if m.judged and m.rate < floor
    ]


def _measure(root: Path) -> list[Measurement]:
    with tempfile.TemporaryDirectory() as scratch:
        data_file = Path(scratch) / "coverage.data"
        report = Path(scratch) / "coverage.json"
        environment_flag = f"--data-file={data_file}"
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                environment_flag,
                "--source=packages",
                "-m",
                "pytest",
                *_SUITE,
                "-q",
            ],
            cwd=root,
            check=False,
        )
        if run.returncode != 0:
            raise SystemExit(
                f"the suite exited {run.returncode}; a coverage figure from a failing "
                "suite measures which tests happened to run, not what is covered."
            )
        subprocess.run(
            [sys.executable, "-m", "coverage", "json", environment_flag, "-o", str(report), "-q"],
            cwd=root,
            check=True,
        )
        return summarize(json.loads(report.read_text(encoding="utf-8")))


def _self_test() -> int:
    """Prove the alarm still fires, and still stays silent where §6 is silent.

    *Fails when* the floor stops being applied, or starts being applied to a
    package the pack deliberately exempts. *Matters because* a floor alarm that
    cannot fire reads exactly like a healthy suite, and one that fires on
    `adopt-cli` would push dedicated tests onto a T4 surface the sprint plan
    budgets at zero. *No other instrument catches it because* the real run is
    green by construction today, so only planted data exercises the branch.
    """
    below = Measurement(package="adopt-store", covered=1, statements=100)
    above = Measurement(package="adopt-store", covered=99, statements=100)
    exempt = Measurement(package="adopt-cli", covered=1, statements=100)
    empty = Measurement(package="adopt-workflow", covered=0, statements=0)

    cases: list[tuple[str, list[Measurement], bool]] = [
        ("a judged package under the floor", [below], True),
        ("a judged package over the floor", [above], False),
        ("an unjudged package under the floor -- §6 does not name adopt-cli", [exempt], False),
        ("a judged package with no statements is vacuously covered", [empty], False),
        ("one judged package under the floor among several", [above, below, exempt], True),
    ]

    problems: list[str] = []
    for label, measurements, should_fire in cases:
        fired = bool(breaches(measurements))
        if fired is not should_fire:
            problems.append(
                f"  {label}: expected {'alarm' if should_fire else 'silence'}, "
                f"got {'alarm' if fired else 'silence'}"
            )
        else:
            print(f"  OK -- {label}: {'alarm' if fired else 'silence'}")

    if problems:
        print("SELF-TEST FAILED:")
        print("\n".join(problems))
        return 1
    print(f"self-test OK: the floor alarm behaves correctly on all {len(cases)} planted cases")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Measure and apply the floor.")
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository to measure. `adopt-plane` runs this from the sibling checkout (CR-29).",
    )
    parser.add_argument("--self-test", action="store_true", help="Prove the alarm still fires.")
    arguments = parser.parse_args(argv)

    if arguments.self_test:
        return _self_test()
    if not arguments.check:
        parser.error("give --check or --self-test")

    measurements = _measure(arguments.root.resolve())
    print(f"\ncoverage floor: {COVERAGE_FLOOR_CORE:.0%} on the implementation spec §6 packages\n")
    for measurement in measurements:
        mark = "judged" if measurement.judged else "      "
        print(
            f"  {measurement.package:<18} {measurement.rate:6.1%}  {mark}  "
            f"({measurement.covered}/{measurement.statements})"
        )

    judged = [m for m in measurements if m.judged]
    if not judged:
        raise SystemExit(
            "no §6 floor package was measured. Either the tree moved or `--source` is "
            "wrong; a floor over an empty set is the failure this alarm exists to prevent."
        )

    failures = breaches(measurements)
    if failures:
        for failure in failures:
            print(f"::error::coverage-floor: {failure}")
        return 1
    print(
        f"\ncoverage-floor: OK -- all {len(judged)} judged package(s) clear the floor. "
        "This is an alarm, not a target: a rise is not an achievement."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
