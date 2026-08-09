"""`conformance-matrix`: the two-adapter rule. AI spec §7.1, PRD N12 and D5.

> **Gate.** `conformance-matrix` fails unless **≥ 2 non-test adapters are green**,
> within `CONFORMANCE_CI_MAX_MINUTES`. A pipeline exercising one vendor is a
> failing pipeline.

**The "at least one local" half is deferred to backlog** *(CR-48, owner-decided 2026-08-07)*. `04` §7.1 required a *local* adapter green, to prove the seam is not
vendor-shaped; the owner deferred it because standing up a local model costs
resources this programme does not have today and the FDE user base reaches
providers over their own API endpoints. **What that gives up is stated plainly in
`BACKLOG.md`**: two hosted adapters prove the seam is not *single*-vendor, and do
not prove it runs with no hosted vendor at all -- which is the offline and BYO
story. The revisit trigger is the first client who asks to run without one.

**What did not change is the fake.** `fake_recorded` is typed `test` and never
counts toward the arity, so a pipeline green on the fake plus one vendor is still a
failing pipeline. That is the half of the old rule doing the structural work.

**The rule lives here, not in the workflow.** A gate expressed in shell inside a
YAML step is a gate nobody writes a negative test for, and this one has three ways
to be wrong that all look green: counting a red adapter as green, counting the
recorded fake as local, and never noticing that only one adapter ran at all.
`--self-test` drives each of those.

**Why the fake cannot satisfy the gate.** `AdapterKind` types `fake_recorded` as
`test`, and this script reads the kind from `REGISTRY` rather than from a list of
its own. So "non-test" is answered by the same table the seam uses, and a pipeline
exercising no real model cannot pass the gate that exists to prove one was
exercised.

**Each adapter is run in its own pytest invocation**, so one adapter's failure
cannot abort another's cases and every adapter gets a verdict. A single run with
`--adapters=a,b` reports 26 test results and no per-adapter verdict, which is what
the matrix needs.

**Each adapter may name its own model, and once CR-48 made the required pair two
*hosted* vendors it had to.** `ADOPT_MODEL` is one process-wide value and no
adapter carries a default -- AI spec §2 is explicit that the seam never picks a
model for you, and `anthropic.build` and `openai.build` both refuse construction
without one. So a single `ADOPT_MODEL` across `--adapters=openai,anthropic` names
a model at one vendor and a stranger at the other, and the matrix could not go
green however many credentials were provisioned. `id=model` is resolved **here**,
per subprocess, rather than in the suite: the conformance harness already reads
`ADOPT_MODEL` from the environment, so the seam that needs to differ per adapter
is the environment each invocation gets. A bare id keeps its old meaning and
falls back to the inherited `ADOPT_MODEL`, which is what `fake_recorded` -- typed
`test`, and needing no model at all -- relies on.

Usage:
    python scripts/conformance_matrix.py --adapters openai=gpt-5,anthropic=claude-sonnet-4-5
    python scripts/conformance_matrix.py --adapters fake_recorded
    python scripts/conformance_matrix.py --self-test
"""

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

from adopt_agent.adapters.base import REGISTRY  # noqa: E402
from adopt_const import CONFORMANCE_CI_MAX_MINUTES  # noqa: E402
from scripts import ci_metrics  # noqa: E402

__all__ = ["Target", "Verdict", "evaluate", "main", "parse_targets", "run_one"]

# const-sync: ok -- seconds in a minute, a unit conversion, not AGENT_ADAPTER_TIMEOUT_S.
_SECONDS_PER_MINUTE: Final[int] = 60

#: The suite the matrix runs. Named here rather than passed in: a matrix job that
#: could be pointed at a different suite is a matrix job that can be pointed at a
#: passing one.
_SUITE: Final[str] = "tests/conformance"


@dataclass(frozen=True)
class Verdict:
    """One adapter's result."""

    adapter: str
    kind: str
    green: bool
    detail: str = ""


def evaluate(verdicts: list[Verdict], *, elapsed_seconds: float) -> list[str]:
    """The gate. Returns the reasons it fails, empty when it passes.

    Returning reasons rather than a boolean is deliberate: an operator whose
    pipeline just went red needs to know whether they are short an adapter or over
    the time budget, because those are different fixes and one of them is not code.
    """
    failures: list[str] = []
    green = [verdict for verdict in verdicts if verdict.green]
    # A `test` adapter never counts toward the arity. That is the half of the old
    # rule which survives the owner's deferral untouched, and it is the half doing
    # the structural work: `fake_recorded` exercises no provider, so a pipeline
    # green on the fake plus one vendor has proven one vendor.
    real = [verdict for verdict in green if verdict.kind != "test"]

    # `2` is exempt from the constants rule (`03` §7.2): it is the arity the AI
    # spec states in prose, not a tunable anyone may retune.
    if len(real) < 2:
        failures.append(
            f"only {len(real)} non-test adapter(s) are green and the gate requires 2: "
            f"{', '.join(v.adapter for v in real) or 'none'}. "
            f"`fake_recorded` is typed `test` and never counts toward the arity"
        )

    budget_seconds = CONFORMANCE_CI_MAX_MINUTES * _SECONDS_PER_MINUTE
    if elapsed_seconds > budget_seconds:
        failures.append(
            f"the matrix took {elapsed_seconds:.0f}s against a budget of "
            f"{budget_seconds}s (CONFORMANCE_CI_MAX_MINUTES)"
        )
    return failures


def _kind_of(adapter: str) -> str:
    entry = REGISTRY.get(adapter)
    if entry is None:
        raise SystemExit(
            f"{adapter!r} is not a registered adapter. Registered: {', '.join(sorted(REGISTRY))}."
        )
    return str(entry.kind)


@dataclass(frozen=True)
class Target:
    """One adapter to run, and the model it runs against.

    `model is None` means "inherit `ADOPT_MODEL`", which is the pre-CR-48
    behaviour and the only thing `fake_recorded` ever needed.
    """

    adapter: str
    model: str | None = None


def parse_targets(spec: str) -> list[Target]:
    """Parse `--adapters`: `id`, or `id=model`, comma separated.

    Every adapter id is validated against `REGISTRY` here rather than at run
    time, so a typo costs a message instead of a red matrix twenty minutes
    later -- and an empty model after `=` is refused rather than silently
    treated as "inherit", because `openai=` reads like an intention to name one.
    """
    targets: list[Target] = []
    for chunk in spec.split(","):
        entry = chunk.strip()
        if not entry:
            continue
        adapter, separator, model = entry.partition("=")
        adapter = adapter.strip()
        model = model.strip()
        if separator and not model:
            raise SystemExit(
                f"{entry!r} names no model after '='. Give `{adapter}=<model>`, "
                f"or just `{adapter}` to inherit ADOPT_MODEL."
            )
        _kind_of(adapter)
        targets.append(Target(adapter=adapter, model=model or None))
    return targets


def run_one(target: Target) -> Verdict:
    """Run the whole suite against one adapter, with that adapter's model."""
    label = target.adapter if target.model is None else f"{target.adapter} ({target.model})"
    print(f"\n=== {label} ===", flush=True)
    # The child inherits this process's environment and overrides only the model,
    # so a per-adapter model never leaks into the next invocation -- which is the
    # whole defect this replaced.
    env = dict(os.environ)
    if target.model is not None:
        env["ADOPT_MODEL"] = target.model
    # Fixed argv, no shell: the suite path is a module constant and the adapter
    # id is validated against `REGISTRY` before this is reached.
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", _SUITE, f"--adapters={target.adapter}", "-v"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )
    return Verdict(
        adapter=target.adapter,
        kind=_kind_of(target.adapter),
        green=completed.returncode == 0,
        detail=f"pytest exited {completed.returncode}",
    )


def _self_test() -> int:
    """Prove the rule still rejects the three ways a matrix can look green.

    Each row is a real failure mode rather than an invented one: the fake counting
    toward the arity is what a "make CI green" change would try, and one adapter is
    the state this repository is in until a second credential lands. The two-hosted
    row is here as an **accept** rather than a reject, which is the whole visible
    difference CR-48 made -- and having it in the self-test is what stops that
    deferral being re-argued from memory.
    """
    hosted = "hosted"
    cases: list[tuple[str, list[Verdict], float, bool]] = [
        (
            "two hosted adapters green -- accepted since CR-48 deferred the local half",
            [Verdict("anthropic", hosted, True), Verdict("openai", hosted, True)],
            1.0,
            True,
        ),
        (
            "the recorded fake standing in for a second adapter",
            [Verdict("anthropic", hosted, True), Verdict("fake_recorded", "test", True)],
            1.0,
            False,
        ),
        (
            "one real adapter green, nothing else",
            [Verdict("local_openai", "local", True)],
            1.0,
            False,
        ),
        (
            "the fake alone, green",
            [Verdict("fake_recorded", "test", True)],
            1.0,
            False,
        ),
        (
            "a local adapter green and a hosted one red",
            [Verdict("local_openai", "local", True), Verdict("anthropic", hosted, False)],
            1.0,
            False,
        ),
        (
            "one local and one hosted, both green, inside the budget",
            [Verdict("local_openai", "local", True), Verdict("anthropic", hosted, True)],
            1.0,
            True,
        ),
        (
            "both green but over the time budget",
            [Verdict("local_openai", "local", True), Verdict("anthropic", hosted, True)],
            CONFORMANCE_CI_MAX_MINUTES * _SECONDS_PER_MINUTE + 1,
            False,
        ),
    ]

    problems: list[str] = []
    for label, verdicts, elapsed, should_pass in cases:
        failures = evaluate(verdicts, elapsed_seconds=elapsed)
        passed = not failures
        if passed is not should_pass:
            problems.append(
                f"  {label}: expected {'pass' if should_pass else 'reject'}, "
                f"got {'pass' if passed else 'reject: ' + '; '.join(failures)}"
            )
        else:
            print(f"  OK -- {label}: {'accepted' if passed else 'rejected'}")

    if problems:
        print("SELF-TEST FAILED:")
        print("\n".join(problems))
        return 1
    # Unpacked rather than indexed: `case[3]` is a positional index that happens
    # to equal a tunable, and `constants-sync` flagged it. A waiver would have
    # been honest and this is better -- the name says what the element is.
    rejected = sum(1 for *_, should_pass in cases if not should_pass)
    print(f"self-test OK: the two-adapter rule rejects all {rejected} bad matrices")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adapters",
        help=(
            "Comma-separated adapters to run, each in its own invocation. "
            "`id=model` names that adapter's model; a bare `id` inherits ADOPT_MODEL. "
            "e.g. --adapters=openai=gpt-5,anthropic=claude-sonnet-4-5"
        ),
    )
    parser.add_argument(
        "--self-test", action="store_true", help="Prove the two-adapter rule still rejects."
    )
    arguments = parser.parse_args(argv)

    if arguments.self_test:
        return _self_test()

    if not arguments.adapters:
        parser.error("give --adapters or --self-test")

    targets = parse_targets(arguments.adapters)
    if not targets:
        parser.error("--adapters was given with no adapter ids")
    started = time.monotonic()
    verdicts = [run_one(target) for target in targets]
    elapsed = time.monotonic() - started

    print("\n=== conformance matrix ===")
    for verdict in verdicts:
        print(
            f"  {verdict.adapter:<16} {verdict.kind:<8} "
            f"{'GREEN' if verdict.green else 'RED':<6} {verdict.detail}"
        )
    print(f"  elapsed {elapsed:.0f}s of {CONFORMANCE_CI_MAX_MINUTES * _SECONDS_PER_MINUTE}s")

    # PRD §6 D5 counts green non-test adapters, and this is the only place that
    # knows a verdict's `kind`. Emitting here rather than re-deriving it in the
    # metrics job keeps one answer to "which adapters were green".
    for verdict in verdicts:
        ci_metrics.emit(
            "adapter_verdict",
            {
                "adapter": verdict.adapter,
                "kind": verdict.kind,
                "green": "true" if verdict.green else "false",
            },
        )

    failures = evaluate(verdicts, elapsed_seconds=elapsed)
    if failures:
        for failure in failures:
            print(f"::error::conformance-matrix: {failure}")
        return 1
    print("conformance-matrix: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
