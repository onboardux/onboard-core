"""`ci-ratchet`: the suite runtime budget is a hard failure, not a warning.

NFR N13: unit ≤ `CI_UNIT_MAX_MINUTES`, per-PR ≤ `CI_PR_MAX_MINUTES`. Adding
runtime past the budget requires removing equivalent runtime **in the same
change**. That is what stops the suite being append-only, which is the state
every unmaintained suite reaches by default.

The budget is read from `adopt_const` rather than restated here, so retuning it
is a one-line change in one place.

Usage::

    python scripts/ci_ratchet.py --budget unit -- uv run pytest -m unit

The wrapped command's exit code is preserved when it fails. A command that
passes but exceeds its budget still fails the build: a green suite that takes
twice as long as it is allowed to is a debt being taken out silently.
"""

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Final

from adopt_const import CI_PR_MAX_MINUTES, CI_UNIT_MAX_MINUTES

SECONDS_PER_MINUTE: Final[int] = 60

BUDGETS: Final[dict[str, int]] = {
    "unit": CI_UNIT_MAX_MINUTES,
    "pr": CI_PR_MAX_MINUTES,
}


@dataclass(frozen=True)
class RatchetResult:
    budget_name: str
    budget_seconds: float
    elapsed_seconds: float
    command_exit_code: int

    @property
    def over_budget(self) -> bool:
        return self.elapsed_seconds > self.budget_seconds

    @property
    def exit_code(self) -> int:
        if self.command_exit_code != 0:
            return self.command_exit_code
        return 1 if self.over_budget else 0

    def describe(self) -> str:
        verdict = "OVER BUDGET" if self.over_budget else "within budget"
        return (
            f"ci-ratchet [{self.budget_name}]: {self.elapsed_seconds:.1f}s against a "
            f"{self.budget_seconds:.0f}s budget -- {verdict}."
        )

    def remedy(self) -> str:
        return (
            f"The {self.budget_name} suite exceeded its budget by "
            f"{self.elapsed_seconds - self.budget_seconds:.1f}s. Remove or merge "
            "equivalent runtime in this same change -- the ratchet exists so the "
            "suite cannot grow without someone deciding what it is worth."
        )


def evaluate(budget_name: str, elapsed_seconds: float, command_exit_code: int) -> RatchetResult:
    """Pure evaluation, so the ratchet can be tested without running a suite."""
    if budget_name not in BUDGETS:
        raise KeyError(f"unknown budget {budget_name!r}; expected one of {sorted(BUDGETS)}")
    return RatchetResult(
        budget_name=budget_name,
        budget_seconds=BUDGETS[budget_name] * SECONDS_PER_MINUTE,
        elapsed_seconds=elapsed_seconds,
        command_exit_code=command_exit_code,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--budget", choices=sorted(BUDGETS), required=True)
    parser.add_argument(
        "--elapsed",
        type=float,
        default=None,
        help="evaluate a recorded duration instead of running a command",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if args.elapsed is not None:
        result = evaluate(args.budget, args.elapsed, 0)
    else:
        command = [c for c in args.command if c != "--"]
        if not command:
            parser.error("supply a command after `--`, or pass --elapsed")
        started = time.monotonic()
        completed = subprocess.run(command, check=False)
        result = evaluate(args.budget, time.monotonic() - started, completed.returncode)

    print(result.describe())
    if result.over_budget:
        print(result.remedy())
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
