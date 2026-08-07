"""The `detect-001` golden-set report. AI spec §7.2 -- **informational, not gating.**

    uv run python tests/golden_prompts/detect_001/report.py --adapter local_openai

Two metrics, both from §7.2:

* **Top-1 accuracy** on `primary` against the hand label.
* **Calibration**: mean confidence on wrong answers below mean confidence on right
  ones. A model that is confidently wrong is worse than one that is uncertainly
  wrong, because `04` §5.1 rule 2 tells it that low confidence is a correct answer
  and PRD §8 puts a human behind every write. Accuracy alone cannot see that.

**Why this exits 0 whatever it measures.** §7.2 makes the gate informational at
Build 0 and says why: the flag is default-off and a human accepts every write, and
**a 15-item set cannot support a blocking threshold**. Inventing one would be
exactly the fake precision the standing-claims discipline forbids. The gate arrives
with the sample size, or the flag stays off.

**Without an adapter it reports NOT RUN and says so.** It does not report success.
A harness that printed a green summary having called nothing would be worse than no
harness -- the result would be quoted in a review as evidence.

**It is a script, not a test.** Under `pytest` it would either need a network or a
skip, and a skipped eval that looks like a passing test is the failure mode this
whole file is written against. `05` S7 asks for the set "reported not gating", and
this is the reporting.
"""

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

from adopt_agent import AgentRequest, Budget, Runner  # noqa: E402
from adopt_agent.adapters.base import REGISTRY  # noqa: E402
from adopt_const import (  # noqa: E402
    AGENT_DETECT_LISTING_MAX_ENTRIES,
    AGENT_DETECT_MAX_USD,
    AGENT_DETECT_MAX_WALL_SECONDS,
)
from adopt_store.annex import open_annex  # noqa: E402

CASES_FILE: Final[Path] = Path(__file__).with_name("cases.json")
PROMPTS: Final[Path] = REPO_ROOT / "prompts"
_JSON: Final[dict[str, Any]] = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}


@dataclass(frozen=True)
class Outcome:
    case_id: str
    expected: str
    got: str | None
    confidence: float | None
    detail: str = ""

    @property
    def correct(self) -> bool:
        return self.got == self.expected


def _evidence(case: dict[str, Any]) -> dict[str, Any]:
    """The four `04` §5.1 placeholders, from a recorded case.

    Built here rather than by importing `adopt_detect.disambiguate.build_evidence`
    on purpose: that function's input is a `DetectionResult` from a real tree, and
    these cases are recorded evidence. Rendering them the same way the pass does --
    canonical JSON, the same keys -- is what makes the measurement comparable to a
    live run.
    """
    return {
        "scores_json": json.dumps(case["scores"], **_JSON),
        "rules_fired_json": json.dumps(case["rules_fired"], **_JSON),
        "listing_limit": AGENT_DETECT_LISTING_MAX_ENTRIES,
        "listing": "\n".join(case["listing"]),
    }


def run(adapter: str, *, model: str | None, endpoint: str | None) -> list[Outcome]:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    outcomes: list[Outcome] = []
    with tempfile.TemporaryDirectory() as tmp:
        offline = REGISTRY[adapter].kind != "hosted"
        with open_annex(Path(tmp) / "runtime.db") as annex:
            runner = Runner(
                annex=annex,
                scope_ref="golden-prompts/detect-001",
                skills_root=PROMPTS,
                offline=offline,
                adapter_id=adapter,
                model=model,
                endpoint=endpoint,
            )
            for case in cases:
                outcomes.append(_one(runner, case))
    return outcomes


def _one(runner: Runner, case: dict[str, Any]) -> Outcome:
    request = AgentRequest(
        skill_ref="detect-001/v1",
        inputs=_evidence(case),
        budget=Budget(max_usd=AGENT_DETECT_MAX_USD, max_wall_seconds=AGENT_DETECT_MAX_WALL_SECONDS),
        idempotency_key=f"golden-{case['id']}",
    )
    try:
        result = runner.run(request)
    except Exception as exc:
        return Outcome(case["id"], case["archetype"], None, None, f"{type(exc).__name__}: {exc}")

    if result.status != "ok" or not isinstance(result.output, dict):
        return Outcome(case["id"], case["archetype"], None, None, f"status={result.status}")
    return Outcome(
        case["id"],
        case["archetype"],
        str(result.output.get("primary")),
        float(result.output.get("confidence", 0.0)),
    )


def report(outcomes: list[Outcome]) -> None:
    print(f"\n{'case':<14}{'expected':<11}{'got':<11}{'conf':>6}  verdict")
    for outcome in outcomes:
        confidence = f"{outcome.confidence:.2f}" if outcome.confidence is not None else "   -"
        verdict = "OK" if outcome.correct else f"WRONG {outcome.detail}".strip()
        print(
            f"{outcome.case_id:<14}{outcome.expected:<11}{outcome.got or '-'!s:<11}"
            f"{confidence:>6}  {verdict}"
        )

    scored = [o for o in outcomes if o.confidence is not None]
    right = [o for o in scored if o.correct]
    wrong = [o for o in scored if not o.correct]
    print(f"\ncases: {len(outcomes)}  answered: {len(scored)}  correct: {len(right)}")
    if scored:
        print(f"top-1 accuracy: {len(right) / len(scored):.2f}")
    if right and wrong:
        mean_right = sum(o.confidence or 0 for o in right) / len(right)
        mean_wrong = sum(o.confidence or 0 for o in wrong) / len(wrong)
        calibrated = mean_wrong < mean_right
        print(
            f"calibration: mean confidence right {mean_right:.2f} vs wrong {mean_wrong:.2f} "
            f"-- {'CALIBRATED' if calibrated else 'MISCALIBRATED'}"
        )
    else:
        print(
            "calibration: not computable -- it needs at least one right and one wrong "
            "answer, and reporting a number without both would be a claim about nothing"
        )
    print(
        "\nINFORMATIONAL (AI spec §7.2). This is not a gate: a 15-item set cannot "
        "support a blocking threshold, and the gate arrives with the sample size."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", help="Adapter id to measure, e.g. local_openai.")
    parser.add_argument("--model", help="Model id. Defaults to ADOPT_MODEL.")
    parser.add_argument("--endpoint", help="Endpoint. Defaults to ADOPT_ADAPTER_ENDPOINT.")
    arguments = parser.parse_args(argv)

    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    by_archetype: dict[str, int] = {}
    for case in cases:
        by_archetype[case["archetype"]] = by_archetype.get(case["archetype"], 0) + 1
    print(f"golden set: {len(cases)} cases, {by_archetype}")

    if not arguments.adapter:
        print(
            "\nNOT RUN -- no --adapter given, so nothing was measured.\n"
            "This is reported rather than passed: a summary printed without calling a "
            "model would be quoted in a review as evidence that it had been.\n"
            "Run: uv run python tests/golden_prompts/detect_001/report.py "
            "--adapter local_openai"
        )
        return 0

    import os

    model = arguments.model or os.environ.get("ADOPT_MODEL")
    endpoint = arguments.endpoint or os.environ.get("ADOPT_ADAPTER_ENDPOINT")
    if arguments.adapter not in REGISTRY:
        print(f"unknown adapter {arguments.adapter!r}; registered: {', '.join(sorted(REGISTRY))}")
        return 1
    if not model:
        print("NOT RUN -- no model. Set ADOPT_MODEL or pass --model.")
        return 0

    report(run(arguments.adapter, model=model, endpoint=endpoint))
    return 0


if __name__ == "__main__":
    sys.exit(main())
