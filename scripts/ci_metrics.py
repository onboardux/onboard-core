"""The PRD §6 success metrics, emitted as CI events. `05` S9.

PRD §6 defines a north star computed from four CI events and six diagnostics
D1-D6 stated as exact predicates. **None of the ten existed anywhere in either
tree** -- a full-text search for `foreign_table_declared`, `sdk_import_violation`,
`backend_import_violation`, `schema_lint_violation` and `g0_runs_green` returned
nothing. So the measurement surface the PRD calls "ratios over CI events with no
human input" had no events and no ratios.

**Emission is to a file, never to a network.** PRD N10 is zero sockets outside a
declared adapter endpoint and implementation spec §8 makes OSS mode zero-telemetry
*permanently* -- *"there is no opt-in switch to add later; the plane is the
telemetry path"*. A metrics wire is the single most likely place for that to be
broken by accident and for the breach to look like a feature, so this module
imports no HTTP client and opens no socket: it appends JSON lines to a run-local
file which CI uploads as an artefact.

**Why the predicates live here and not in the workflow.** They are arithmetic
with a right answer, and every one of D1-D4 and D6 is a ratio whose *degenerate*
case reads as success: a ratio over zero runs is not 1.0, it is undefined, and a
gate that reports 1.0 for it is the "measurement that succeeds by having nothing
to measure" failure this codebase has now caught four times -- the drill that
collected one test, the fake standing in for a local adapter, and
`escape_coverage.py` reporting 100% of one port in twelve. **An undefined ratio
is reported as `null` and never as `1.0`.**

**What is deliberately not here.** No thresholds and no gating. §6's anti-gaming
note bans test count and line coverage as targets, and D1-D6 are *diagnostics* --
the gates that block are the ones in §7, each of which already fails on its own
violation. This turns those outcomes into a record; it does not add a second
opinion about them.

Usage:
    python scripts/ci_metrics.py --event g0_run --field green=true
    python scripts/ci_metrics.py --summarize
    python scripts/ci_metrics.py --self-test
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

__all__ = ["EVENTS", "Diagnostic", "diagnostics", "emit", "main", "ratio", "read_events"]

#: Where events accumulate. A run-local path, overridable so a caller can point it
#: at an artefact directory; never a URL, and there is no code path here that
#: could accept one.
EVENT_FILE_ENV: Final[str] = "ADOPT_CI_METRICS_FILE"
DEFAULT_EVENT_FILE: Final[str] = "ci-metrics.jsonl"

#: The event vocabulary. Four are PRD §6's north-star inputs, named verbatim; the
#: rest are what D1-D6's predicates count. A name not in here is refused, because
#: a typo'd event is a metric that silently reads zero forever.
EVENTS: Final[frozenset[str]] = frozenset(
    {
        # North star -- substrate completeness. Every one of these is a Build 0
        # defect when it fires, not a downstream team's style choice.
        "schema.foreign_table_declared",
        "schema.migration_authored_outside_build0",
        "agent.sdk_import_violation",
        "workflow.backend_import_violation",
        # D1 -- G0 pass rate.
        "g0_run",
        # D2 -- non-additive attempts caught pre-merge.
        "schema_lint_violation",
        "non_additive_shipped",
        # D3 -- append-only integrity.
        "forked_revision_chain",
        # D4 -- coverage cache honesty.
        "cache_recompute_disagreement_silently_reconciled",
        # D5 -- adapter breadth.
        "adapter_verdict",
        # D6 -- scope-isolation coverage.
        "query_path_declared",
    }
)


@dataclass(frozen=True)
class Diagnostic:
    """One PRD §6 diagnostic: its predicate's value, and whether it holds."""

    key: str
    label: str
    value: float | int | None
    target: str
    #: `None` when the predicate is undefined -- no runs, no adapters, no paths.
    #: Deliberately distinct from `False`: "we did not measure" and "we measured
    #: and it is wrong" are different facts and only one of them is a defect.
    holds: bool | None


def _event_file() -> Path:
    return Path(os.environ.get(EVENT_FILE_ENV) or REPO_ROOT / DEFAULT_EVENT_FILE)


def emit(name: str, fields: dict[str, str], *, path: Path | None = None) -> None:
    """Append one event. Refuses an unregistered name."""
    if name not in EVENTS:
        raise SystemExit(
            f"{name!r} is not a registered CI event. Registered: {', '.join(sorted(EVENTS))}. "
            "An unregistered name is a metric that reads zero forever."
        )
    target = path or _event_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": name, **fields}, sort_keys=True) + "\n")


def read_events(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def ratio(numerator: int, denominator: int) -> float | None:
    """`numerator / denominator`, or `None` when the ratio is undefined.

    Returning `None` rather than `1.0` for `0/0` is the whole point of this
    function existing. Every §6 diagnostic is a ratio targeting `1.0`, so a
    degenerate case defaulted to `1.0` reports perfect health for a pipeline that
    measured nothing at all.
    """
    return None if denominator == 0 else numerator / denominator


def _truthy(value: str | None) -> bool:
    return str(value).lower() in {"true", "1", "yes"}


def diagnostics(events: Sequence[dict[str, str]]) -> list[Diagnostic]:
    """Compute D1-D6 exactly as PRD §6 states them."""
    by_name: dict[str, list[dict[str, str]]] = {}
    for event in events:
        by_name.setdefault(str(event.get("event")), []).append(event)

    def count(name: str) -> int:
        return len(by_name.get(name, []))

    g0 = by_name.get("g0_run", [])
    g0_green = [run for run in g0 if _truthy(run.get("green"))]

    lint_violations = count("schema_lint_violation")
    shipped = count("non_additive_shipped")

    adapters = by_name.get("adapter_verdict", [])
    green_non_test = {
        verdict["adapter"]
        for verdict in adapters
        if _truthy(verdict.get("green")) and verdict.get("kind") != "test"
    }

    paths = by_name.get("query_path_declared", [])
    with_case = [path for path in paths if _truthy(path.get("has_escape_case"))]

    return [
        Diagnostic(
            key="D1",
            label="G0 pass rate",
            value=ratio(len(g0_green), len(g0)),
            target="= 1.0",
            holds=None if not g0 else len(g0_green) == len(g0),
        ),
        Diagnostic(
            key="D2",
            label="non-additive attempts caught pre-merge",
            value=ratio(lint_violations, lint_violations + shipped),
            target="= 1.0",
            holds=None if lint_violations + shipped == 0 else shipped == 0,
        ),
        Diagnostic(
            key="D3",
            label="forked revision chains",
            value=count("forked_revision_chain"),
            target="= 0",
            holds=count("forked_revision_chain") == 0,
        ),
        Diagnostic(
            key="D4",
            label="cache disagreements silently reconciled",
            value=count("cache_recompute_disagreement_silently_reconciled"),
            target="= 0",
            holds=count("cache_recompute_disagreement_silently_reconciled") == 0,
        ),
        Diagnostic(
            key="D5",
            label="green non-test adapters",
            value=len(green_non_test) if adapters else None,
            target=">= 2",
            # `2` is the arity AI spec §7.1 states in prose, not a tunable
            # (§7.2 exempts it). `local in green` is deferred -- CR-48.
            holds=None if not adapters else len(green_non_test) >= 2,
        ),
        Diagnostic(
            key="D6",
            label="new query paths with an escape case",
            value=ratio(len(with_case), len(paths)),
            target="= 1.0",
            holds=None if not paths else len(with_case) == len(paths),
        ),
    ]


def north_star(events: Sequence[dict[str, str]]) -> list[str]:
    """The four substrate-completeness violations. Any one is a Build 0 defect."""
    names = [
        "schema.foreign_table_declared",
        "schema.migration_authored_outside_build0",
        "agent.sdk_import_violation",
        "workflow.backend_import_violation",
    ]
    counts = {name: sum(1 for e in events if e.get("event") == name) for name in names}
    return [f"{name} fired {n} time(s)" for name, n in counts.items() if n]


def _render(events: Sequence[dict[str, str]]) -> str:
    lines = [
        "## PRD §6 success metrics",
        "",
        "| # | Diagnostic | Value | Target | Holds |",
        "|---|---|---|---|---|",
    ]
    for diagnostic in diagnostics(events):
        value = "n/a" if diagnostic.value is None else f"{diagnostic.value}"
        holds = {True: "yes", False: "**no**", None: "not measured"}[diagnostic.holds]
        lines.append(
            f"| {diagnostic.key} | {diagnostic.label} | {value} | {diagnostic.target} | {holds} |"
        )
    violations = north_star(events)
    lines += ["", "**Substrate completeness (north star):** "]
    lines[-1] += "1.0 -- no violation event fired" if not violations else "; ".join(violations)
    lines += ["", f"_{len(events)} event(s) in this run._"]
    return "\n".join(lines)


def _self_test() -> int:
    """Prove the predicates reject what they exist to reject.

    *Fails when* a §6 predicate inverts or its degenerate case starts reading as
    success. *Matters because* D1-D4 and D6 all target `1.0` and are the PRD's
    stated anti-gaming surface -- a predicate that returns 1.0 for an empty run
    reports perfect health forever, which is the exact shape of the four
    measurement defects this build has already found. *No other instrument
    catches it because* on a healthy pipeline every one of these is green by
    construction, so only planted data exercises the failing branch.
    """
    cases: list[tuple[str, list[dict[str, str]], str, bool | None]] = [
        ("no runs at all -- D1 is undefined, NOT 1.0", [], "D1", None),
        (
            "one red G0 among two",
            [
                {"event": "g0_run", "green": "true"},
                {"event": "g0_run", "green": "false"},
            ],
            "D1",
            False,
        ),
        ("two green G0 runs", [{"event": "g0_run", "green": "true"}] * 2, "D1", True),
        (
            "a non-additive migration shipped",
            [{"event": "non_additive_shipped", "table": "x"}],
            "D2",
            False,
        ),
        ("a forked chain", [{"event": "forked_revision_chain", "chain": "x"}], "D3", False),
        ("no forked chains", [], "D3", True),
        (
            "a cache disagreement silently reconciled",
            [{"event": "cache_recompute_disagreement_silently_reconciled", "n": "3"}],
            "D4",
            False,
        ),
        (
            "the recorded fake standing in for a second adapter",
            [
                {
                    "event": "adapter_verdict",
                    "adapter": "anthropic",
                    "kind": "hosted",
                    "green": "true",
                },
                {
                    "event": "adapter_verdict",
                    "adapter": "fake_recorded",
                    "kind": "test",
                    "green": "true",
                },
            ],
            "D5",
            False,
        ),
        (
            "two hosted adapters green",
            [
                {
                    "event": "adapter_verdict",
                    "adapter": "anthropic",
                    "kind": "hosted",
                    "green": "true",
                },
                {
                    "event": "adapter_verdict",
                    "adapter": "openai",
                    "kind": "hosted",
                    "green": "true",
                },
            ],
            "D5",
            True,
        ),
        ("no adapter ran -- D5 is undefined, NOT satisfied", [], "D5", None),
        (
            "a query path with no escape case",
            [
                {"event": "query_path_declared", "path": "a", "has_escape_case": "true"},
                {"event": "query_path_declared", "path": "b", "has_escape_case": "false"},
            ],
            "D6",
            False,
        ),
    ]

    problems: list[str] = []
    for label, events, key, expected in cases:
        actual = next(d.holds for d in diagnostics(events) if d.key == key)
        if actual is not expected:
            problems.append(f"  {label}: {key} expected {expected!r}, got {actual!r}")
        else:
            print(f"  OK -- {label}: {key} = {actual!r}")

    if north_star([{"event": "agent.sdk_import_violation", "module": "x"}]) == []:
        problems.append("  a north-star violation event was not reported")
    else:
        print("  OK -- a north-star violation event is reported")

    if problems:
        print("SELF-TEST FAILED:")
        print("\n".join(problems))
        return 1
    print(f"self-test OK: the §6 predicates behave correctly on all {len(cases)} planted cases")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", help="Event name to emit.")
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="A field on the event. Repeatable.",
    )
    parser.add_argument("--summarize", action="store_true", help="Compute and print D1-D6.")
    parser.add_argument("--self-test", action="store_true", help="Prove the predicates reject.")
    arguments = parser.parse_args(argv)

    if arguments.self_test:
        return _self_test()

    if arguments.event:
        fields: dict[str, str] = {}
        for pair in arguments.field:
            key, separator, value = pair.partition("=")
            if not separator:
                parser.error(f"--field {pair!r} is not KEY=VALUE")
            fields[key] = value
        emit(arguments.event, fields)
        print(f"ci-metrics: {arguments.event} {fields}")
        return 0

    if not arguments.summarize:
        parser.error("give --event, --summarize or --self-test")

    events = read_events(_event_file())
    rendered = _render(events)
    print(rendered)
    # GitHub renders this on the run page. Written rather than posted: there is
    # no network path out of this module and there is not going to be one.
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
