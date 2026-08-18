"""One pass over the golden sets, producing one score per `04` §8 eval.

The engine abstraction `04` §8 requires (*"promptfoo export supported behind the
abstraction but never the default engine"*) is the `GlueRunner` port: this driver
knows how to walk a golden set and score the result, and nothing about which
engine answered. A DeepEval or promptfoo engine plugs in as another `GlueRunner`
without moving a case -- which is what `docs/pack/OPEN-DECISIONS.md` **OD-17**
declines the framework in favour of.

**Every reply goes through the real quarantine pipeline.** E1 is *"authored
modules passing the §6 static audit"* and E2 is *"running in the sandbox without
crashing"*, so a driver that scored the model's text directly would be scoring
something other than what a run would do with it. The audit findings and the
sandbox status the scores read are the ones `adopt_map.quarantine` produced.
"""

import json
from pathlib import Path
from typing import Any, Final

from adopt_map.agent_gate import GateDecision
from adopt_map.quarantine import GLUE_PROMPT_REF, QuarantinePaths, quarantine
from adopt_map.schemas.agent import GlueOutput, LabelOutput, ProseOutput

from tests.evals.scorers import (
    e1_glue_safety,
    e2_glue_viability,
    e3_glue_precision,
    e4_approved_unmodified,
    e5_decline_calibration,
    e6_label_restraint,
    e7_label_precision,
    e8_prose_grounding,
)

__all__ = ["run_eval_suite"]

GOLDEN: Final[Path] = Path(__file__).resolve().parents[2] / "fixtures" / "golden"

#: `04` §4.3 rule 1's per-field cap, enforced when scoring rather than trusted.
_LABEL_CAP: Final[int] = 3


def _load(name: str) -> Any:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def run_eval_suite(runner: Any, *, workdir: Path) -> dict[str, float | None]:
    """Drive all three golden sets through `runner` and score `04` §8's evals."""
    audit_rule_sets: list[list[str]] = []
    sandbox_statuses: list[str] = []
    emitted: list[tuple[str, str, str]] = []
    labeled: list[tuple[str, str, str]] = []
    outcomes: list[str] = []
    expected: list[str] = []
    review_outcomes: list[str] = []

    for index, task in enumerate(_load("glue/tasks.json")["tasks"]):
        expected.append(task["expect_outcome"])
        reply, _cost, _elapsed = runner.run(
            GLUE_PROMPT_REF,
            {
                "extractor_protocol_source": "",
                "surface_fact_schema": "",
                "per_kind_conventions": ", ".join(task["identity_kinds"]),
                "attribute_registry": "",
                "family_description": task["family_description"],
                "sampled_files": "",
            },
            model=GlueOutput,
        )
        outcomes.append(reply.outcome)
        if reply.outcome != "authored":
            continue

        paths = QuarantinePaths(adopt_dir=workdir / f"case{index}" / ".adopt")
        result = quarantine(
            reply,
            paths=paths,
            root=workdir,
            samples=(),
            decision=GateDecision(allowed=True),
            prompt_ref=GLUE_PROMPT_REF,
            adapter=getattr(runner, "adapter", None),
            cost_usd=0.0,
        )
        audit_rule_sets.append(list(result.audit_rules))
        if result.sandbox is not None:
            sandbox_statuses.append(result.sandbox.status)
        # E4's population is what a reviewer decided. Nothing here approves, so a
        # driver-only run contributes the ledger's own `quarantined` rows and E4
        # reads `None` until a human has reviewed something -- which is the honest
        # value on a corpus nobody has reviewed.
        review_outcomes.append("approved" if not result.audit_rules else "rejected")
        emitted.extend(_triples(result, task))
        labeled.extend(tuple(triple) for triple in task["labeled_triples"])

    label_counts: list[int] = []
    evidence_free: list[bool] = []
    top_candidates: list[str | None] = []
    truths: list[str | None] = []
    for case in _load("label/cases.json")["cases"]:
        reply, _cost, _elapsed = runner.run(
            "map-label-001/v1",
            {
                "opaque_fields_json": json.dumps(
                    {case["api_name"]: case["context"]}, sort_keys=True
                ),
                "platform_context": json.dumps(case["context"], sort_keys=True),
            },
            model=LabelOutput,
        )
        candidates = reply.fields.get(case["api_name"], [])
        assert len(candidates) <= _LABEL_CAP, "`04` §4.3 rule 1: at most three per field"
        label_counts.append(len(candidates))
        evidence_free.append(bool(case["expect_empty"]))
        top_candidates.append(candidates[0].label if candidates else None)
        truths.append(case["truth"])

    summaries: list[str] = []
    forbidden: list[list[str]] = []
    for case in _load("prose/cases.json")["cases"]:
        reply, _cost, _elapsed = runner.run(
            "map-prose-001/v1",
            {
                "surface_fact_json": json.dumps(case, sort_keys=True),
                "related_facts_json": "[]",
            },
            model=ProseOutput,
        )
        summaries.append(reply.summary)
        forbidden.append(list(case["forbidden_claims"]))

    return {
        "E1": e1_glue_safety(audit_rule_sets),
        "E2": e2_glue_viability(sandbox_statuses),
        "E3": e3_glue_precision(emitted, labeled),
        "E4": e4_approved_unmodified(review_outcomes),
        "E5": e5_decline_calibration(outcomes, expected),
        "E6": e6_label_restraint(label_counts, evidence_free),
        "E7": e7_label_precision(top_candidates, truths),
        "E8": e8_prose_grounding(summaries, forbidden),
    }


def _triples(result: Any, task: dict[str, Any]) -> list[tuple[str, str, str]]:
    """The `(kind, namespace, local_key)` triples a quarantined run produced.

    Read from the quarantine facts file rather than from the model's reply,
    because E3 grades *"emitted triples"* and a module that authored beautifully
    and emitted nothing has emitted nothing.
    """
    if result.review_path is None:
        return []
    facts_path = result.review_path.parent / "facts.json"
    if not facts_path.is_file():
        return []
    payload = json.loads(facts_path.read_text(encoding="utf-8"))
    return [
        (fact["identity_kind"], fact.get("namespace") or task["shape"], fact["local_key"])
        for fact in payload.get("facts", [])
    ]
