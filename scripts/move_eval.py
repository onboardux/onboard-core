"""`01` section 6 **M4** -- move precision on the labeled rename corpus.

    uv run python scripts/move_eval.py --check
    uv run python scripts/move_eval.py --self-test

**M4 is the metric with two right answers, and that is the whole reason this is
its own instrument.** `01` section 6 defines it as *"moves emitted that a human
confirms, >= 0.95; ambiguous cases must be declined, not guessed"*. A scorer that
counted only emitted moves would reward a build that resolved every ambiguity by
coin toss, because a guess that happens to be right is indistinguishable from a
correct move in the numerator. So each labelled case carries the answer a human
gives -- `move`, `no-move`, or `decline` -- and the report prints **precision over
the cases that should move** beside **declination accuracy over the cases that
should not**, never one number blended from both.

**What the corpus is, and what it is not** (`fixtures/labeled/renames.json`). Ten
hand-labelled cases: seven are the behaviours `01` F5 and `02` section 10 C11
specify, and three replay rename shapes taken from real commit history in the S1.8
soak corpus. That third group exists because a corpus made only of the cases the
implementation was written against measures the implementation against itself --
`03` section 7's argument for fixtures, applied to a metric.

**One measured fact about real renames, worth carrying.** Across the most recent
300 commits of `saleor`, git finds **11** renames and exactly **one** is a pure
move (`R100`); the other ten changed content in the same commit. `01` F5 makes a
move an exact semantic-digest match, so ten of eleven real renames are correctly
*not* moves -- they are changes at a new path. M4's numerator is therefore a
minority event in real code, and a build reporting a low move count is not
necessarily a build with a broken mover. That belongs beside the number, which is
why this tool prints it rather than leaving it to whoever quotes the ratio.
"""

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.e2e.map_journey import Journey  # noqa: E402

#: The hand-labelled corpus.
CORPUS: Final[Path] = REPO_ROOT / "fixtures" / "labeled" / "renames.json"

#: `01` §6 M4's stated target, as this script's `--check` default.
#:
#: **It happens to equal `MAP_CONF_GRAMMAR`, and the gate is right to ask.** They
#: are different numbers that share a value: one is the confidence a grammar hit
#: earns, the other is the share of emitted moves a human must confirm. Retuning
#: either must not move the other, which is exactly what importing one for the
#: other would do.
#:
#: It is not promoted into `adopt_const` either, and that is a decision rather than
#: an omission: `00` §5 rule 3 pairs a constant with a `03` §3 row, and `03` §3 is
#: the table of **runtime** tunables the product reads. M4's target is a reporting
#: threshold one exit-time evaluator consults and nothing the CLI ships ever sees.
# const-sync: ok -- `01` §6 M4's target; equals MAP_CONF_GRAMMAR by coincidence, and the two must retune independently.
M4_FLOOR: Final[float] = 0.95


@dataclass
class CaseResult:
    """One labelled case, run."""

    case_id: str
    expect: str
    moves: list[dict[str, str]]
    conflicts: list[dict[str, Any]]
    correct: bool
    note: str = ""


@dataclass
class Report:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def move_cases(self) -> list[CaseResult]:
        return [r for r in self.results if r.expect == "move"]

    @property
    def declining_cases(self) -> list[CaseResult]:
        return [r for r in self.results if r.expect in {"decline", "no-move"}]

    @property
    def precision(self) -> float | None:
        """Emitted moves that a human confirms.

        **Undefined reports `None`, never 1.0.** A corpus with no move case has
        not achieved perfect precision; it has measured nothing, and `ci_metrics`
        made the same argument for the same reason.
        """
        emitted = [r for r in self.results if r.moves]
        if not emitted:
            return None
        confirmed = [r for r in emitted if r.expect == "move" and r.correct]
        return len(confirmed) / len(emitted)

    @property
    def recall(self) -> float | None:
        cases = self.move_cases
        if not cases:
            return None
        return sum(1 for r in cases if r.correct) / len(cases)

    @property
    def declination_accuracy(self) -> float | None:
        cases = self.declining_cases
        if not cases:
            return None
        return sum(1 for r in cases if r.correct) / len(cases)


def _apply(tree: Path, operations: Sequence[dict[str, Any]]) -> None:
    """Replay one case's edits against a copied tree."""
    for operation in operations:
        kind = operation["op"]
        if kind == "rename":
            (tree / operation["from"]).rename(tree / operation["to"])
        elif kind == "copy":
            shutil.copyfile(tree / operation["from"], tree / operation["to"])
        elif kind == "delete":
            target = tree / operation["path"]
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        elif kind == "mkdir":
            (tree / operation["path"]).mkdir(parents=True, exist_ok=True)
        elif kind == "append":
            with (tree / operation["path"]).open("a", encoding="utf-8") as handle:
                handle.write(operation["text"])
        elif kind == "write":
            (tree / operation["path"]).write_text(operation["text"], encoding="utf-8")
        else:  # pragma: no cover -- a corpus defect, not a runtime branch
            raise ValueError(f"unknown corpus operation {kind!r} in a labelled case")


def _pairs_match(moves: Sequence[dict[str, str]], expected: Sequence[dict[str, str]]) -> bool:
    for want in expected:
        if not any(
            want["from_contains"] in move["from"] and want["to_contains"] in move["to"]
            for move in moves
        ):
            return False
    return True


def run_case(case: dict[str, Any], workdir: Path) -> CaseResult:
    """Map once, edit, map again, and compare against the human's answer."""
    root = workdir / str(case["id"])
    if root.exists():
        shutil.rmtree(root)
    journey = Journey(root, fixture=str(case.get("fixture", "stub")))
    journey.map()

    _apply(journey.tree, case["operations"])
    second = journey.map()

    moves = list(second.payload["moves"])
    conflicts = list(second.payload["conflicts"])
    expect = str(case["expect"])

    if expect == "move":
        correct = bool(moves) and _pairs_match(moves, case.get("expected_pairs", []))
        note = "" if correct else f"expected a move, got {len(moves)}"
    elif expect == "decline":
        wanted_reason = case.get("conflict_reason")
        correct = not moves and any(
            wanted_reason is None or conflict["reason"] == wanted_reason for conflict in conflicts
        )
        note = (
            "" if correct else f"expected a declination with a conflict, got {len(moves)} move(s)"
        )
    else:  # "no-move"
        correct = not moves
        note = "" if correct else f"expected no move, got {len(moves)}"

    return CaseResult(
        case_id=str(case["id"]),
        expect=expect,
        moves=moves,
        conflicts=conflicts,
        correct=correct,
        note=note,
    )


def evaluate(corpus: dict[str, Any], workdir: Path, only: str | None = None) -> Report:
    report = Report()
    for case in corpus["cases"]:
        if only and case["id"] != only:
            continue
        report.results.append(run_case(case, workdir))
    return report


def render(report: Report) -> str:
    lines = ["`01` §6 M4 -- move precision on the labeled rename corpus", ""]
    for result in report.results:
        mark = "ok  " if result.correct else "MISS"
        lines.append(
            f"  {mark} {result.case_id:38} expect={result.expect:8} "
            f"moves={len(result.moves)} conflicts={len(result.conflicts)} {result.note}"
        )
    lines.append("")

    def show(value: float | None) -> str:
        return "undefined (no case)" if value is None else f"{value:.3f}"

    lines.append(f"  precision              {show(report.precision)}  (M4, floor {M4_FLOOR})")
    lines.append(f"  recall over move cases {show(report.recall)}")
    lines.append(
        f"  declination accuracy   {show(report.declination_accuracy)}  "
        f"({len(report.declining_cases)} case(s) whose right answer is no move)"
    )
    return "\n".join(lines)


def self_test(workdir: Path) -> int:
    """Prove the scorer reports a miss, and prove it does not report one on a clean run.

    The planted case is the one that matters: an ambiguous rename **resolved**
    rather than declined. A scorer that counted emitted moves alone would score
    that as a success, which is the specific way M4 can be satisfied by a build
    that guesses.
    """
    clean = Report(
        results=[
            CaseResult("a", "move", [{"from": "x", "to": "y"}], [], True),
            CaseResult("b", "decline", [], [{"reason": "ambiguous_move"}], True),
        ]
    )
    if clean.precision != 1.0 or clean.declination_accuracy != 1.0:
        print("self-test FAILED: a clean corpus did not score 1.000", file=sys.stderr)
        return 1
    print("self-test: a clean corpus scores 1.000 on both halves (positive control) ->")

    guessed = Report(
        results=[
            CaseResult("a", "move", [{"from": "x", "to": "y"}], [], True),
            CaseResult("b", "decline", [{"from": "p", "to": "q"}], [], False),
        ]
    )
    if guessed.precision is None or guessed.precision >= 1.0:
        print(
            f"self-test FAILED: a guessed ambiguous move scored {guessed.precision} -- "
            "the scorer is counting emitted moves rather than confirmed ones",
            file=sys.stderr,
        )
        return 1
    print(f"self-test: a guessed ambiguous move drops precision -> {guessed.precision:.3f}")

    if guessed.declination_accuracy != 0.0:
        print(
            "self-test FAILED: a resolved ambiguity scored as a correct declination",
            file=sys.stderr,
        )
        return 1
    print("self-test: the same case scores 0.000 declination accuracy ->")

    empty = Report()
    if empty.precision is not None or empty.declination_accuracy is not None:
        print("self-test FAILED: an empty corpus reported a number", file=sys.stderr)
        return 1
    print("self-test: an empty corpus reports undefined, never 1.000 ->")
    print("self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--only", default=None, help="run one case by id")
    parser.add_argument("--min-precision", type=float, default=None)
    parser.add_argument("--check", action="store_true", help="fail below `01` §6 M4's floor")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    import tempfile

    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="move-eval-"))
    if args.self_test:
        return self_test(workdir)

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    report = evaluate(corpus, workdir, only=args.only)

    if args.json:
        print(
            json.dumps(
                {
                    "precision": report.precision,
                    "recall": report.recall,
                    "declination_accuracy": report.declination_accuracy,
                    "cases": [
                        {
                            "id": r.case_id,
                            "expect": r.expect,
                            "correct": r.correct,
                            "moves": len(r.moves),
                            "conflicts": len(r.conflicts),
                            "note": r.note,
                        }
                        for r in report.results
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(render(report))

    floor = args.min_precision if args.min_precision is not None else M4_FLOOR
    if args.check or args.min_precision is not None:
        if report.precision is None:
            print(
                "VIOLATION: precision is undefined -- the corpus emitted no move", file=sys.stderr
            )
            return 1
        if report.precision < floor:
            print(
                f"VIOLATION: move precision {report.precision:.3f} is below {floor:.3f} "
                "-- `01` §6 M4",
                file=sys.stderr,
            )
            return 1
        if report.declination_accuracy is not None and report.declination_accuracy < 1.0:
            print(
                f"VIOLATION: declination accuracy {report.declination_accuracy:.3f} -- "
                "`01` §8 gives ambiguous-move resolution to nobody in Build 1, so a "
                "resolved ambiguity is a defect however good precision looks",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
