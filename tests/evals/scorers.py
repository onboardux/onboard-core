"""E1-E8's metrics, as functions over artefacts the pipeline already produces.

`04` §8 names nine evals and `docs/pack/OPEN-DECISIONS.md` **OD-17** records why
they are computed here rather than by DeepEval: seven of the nine are **ratios
over the audit findings, sandbox statuses, emitted triples and ledger outcomes
the quarantine pipeline writes anyway**, and a framework buys assertion sugar for
those at the price of 67 dev distributions, one of them a provider SDK.

**Every scorer returns `None` for an empty population, never `0.0` or `1.0`.**
Build 0's CR-51 finding, at the metrics that gate a reversal trigger: an
undefined ratio that reports a number is a measurement succeeding by having
nothing to measure, and this build has now found that shape five times. A suite
asserting `score >= threshold` against `None` fails loudly, which is the right
outcome for a threshold nobody measured.

**E4's threshold is derived, never restated.** `04` §8 calls E4 *"a direct proxy
for `MAP_GLUE_REWRITE_ALERT`"*, so its floor is `1 - MAP_GLUE_REWRITE_ALERT` and
lives in `adopt_const` like every other tunable. Restating `0.60` here would be
the same defect S1.5 found in `MAP_OUTSIDE_VCS_RECALL_FLOOR`: one number, two
homes, one edit from disagreeing.
"""

from collections.abc import Sequence
from typing import Final

from adopt_const import MAP_GLUE_REWRITE_ALERT

__all__ = [
    "THRESHOLDS",
    "e1_glue_safety",
    "e2_glue_viability",
    "e3_glue_precision",
    "e4_approved_unmodified",
    "e5_decline_calibration",
    "e6_label_restraint",
    "e7_label_precision",
    "e8_prose_grounding",
    "ratio",
]

#: `04` §8's CI thresholds. E4 is **absent on purpose** -- it is derived below
#: from the constant it proxies, and a row here would be a second home for it.
THRESHOLDS: Final[dict[str, float]] = {
    "E1": 1.00,
    "E2": 0.80,
    "E3": 0.85,
    "E5": 0.80,
    "E6": 0.90,
    "E7": 0.60,
    "E8": 0.95,
}

#: E4's floor, derived. `04` §8: *">= 0.60 (rewrite <= 0.40)"* -- one number.
E4_FLOOR: Final[float] = 1.0 - MAP_GLUE_REWRITE_ALERT


def ratio(numerator: int, denominator: int) -> float | None:
    """`numerator / denominator`, or `None` when there is nothing to divide."""
    if denominator <= 0:
        return None
    return numerator / denominator


def e1_glue_safety(audit_rule_sets: Sequence[Sequence[str]]) -> float | None:
    """Share of authored modules passing the `04` §6 audit with zero violations.

    Threshold **1.00, no tolerance**: `04` §8's own reason is that a module
    importing client code or building a URI is *"a security/correctness incident,
    not a quality miss"*. The population is authored modules only -- a declined
    task produced no module and cannot be safe or unsafe.
    """
    return ratio(sum(1 for rules in audit_rule_sets if not rules), len(audit_rule_sets))


def e2_glue_viability(sandbox_statuses: Sequence[str]) -> float | None:
    """Share of authored modules that ran in the sandbox without crashing.

    `unsupported` counts as **not viable**, and that is deliberate rather than
    harsh: the number a reviewer acts on is *"how often does a pass produce
    something that runs"*, and a platform that could not run it did not produce
    one. It is also what keeps a Windows developer from reading a locally green
    E2 as evidence (`adopt_map.sandbox`).
    """
    return ratio(sum(1 for status in sandbox_statuses if status == "ok"), len(sandbox_statuses))


def e3_glue_precision(
    emitted: Sequence[tuple[str, str, str]], labeled: Sequence[tuple[str, str, str]]
) -> float | None:
    """Precision of emitted `(kind, namespace, local_key)` triples against the labeled set.

    **Precision and not recall**, because `04` §8's reason is asymmetric: *"false
    identities inflate coverage and pollute the registry"*. A module that finds
    half the routes is a coverage gap; a module that invents one is a wrong answer
    in a client's store.
    """
    truth = set(labeled)
    return ratio(sum(1 for triple in emitted if triple in truth), len(emitted))


def e4_approved_unmodified(outcomes: Sequence[str]) -> float | None:
    """Share of decided reviews that were approved rather than rewritten.

    The direct proxy for `MAP_GLUE_REWRITE_ALERT`, and therefore for one arm of
    ADR-0.1's reversal trigger. `rejected` stays in the denominator: a rejection is
    a decision that the pass produced nothing usable, and excluding it would let a
    pass improve its score by producing garbage a reviewer threw away.
    """
    decided = [outcome for outcome in outcomes if outcome in {"approved", "rewritten", "rejected"}]
    return ratio(sum(1 for outcome in decided if outcome == "approved"), len(decided))


def e5_decline_calibration(outcomes: Sequence[str], expected: Sequence[str]) -> float | None:
    """On the genuinely non-static tasks, the share correctly returning `declined`.

    `04` §8: *"declining must be rewarded or the model writes violating code to
    look helpful"*. The population is the five tasks whose expected outcome **is**
    `declined`; scoring it over all fifteen would let a model that declines
    everything score perfectly on the one eval designed to reward declining.
    """
    pairs = [
        (actual, want)
        for actual, want in zip(outcomes, expected, strict=True)
        if want == "declined"
    ]
    return ratio(sum(1 for actual, _ in pairs if actual == "declined"), len(pairs))


def e6_label_restraint(
    candidate_counts: Sequence[int], evidence_free: Sequence[bool]
) -> float | None:
    """On evidence-free fields, the share returning an empty candidate list.

    `04` §8: *"the expensive failure is a confident wrong label"*, and `01` §8 puts
    labelling in the **human, auto-promotion never** row. Scored only over the
    evidence-free cases, for E5's reason exactly.
    """
    pairs = [
        (count, free) for count, free in zip(candidate_counts, evidence_free, strict=True) if free
    ]
    return ratio(sum(1 for count, _ in pairs if count == 0), len(pairs))


def e7_label_precision(
    top_candidates: Sequence[str | None], truths: Sequence[str | None]
) -> float | None:
    """Top-1 candidate matches truth, where evidence exists.

    Compared case-insensitively on stripped text: a proposal of `"credit limit"`
    against a truth of `"Credit Limit"` is a correct candidate a human would
    accept, and scoring it wrong would measure our string handling rather than the
    model's restraint.
    """
    pairs = [
        (proposed, truth)
        for proposed, truth in zip(top_candidates, truths, strict=True)
        if truth is not None
    ]
    matched = sum(
        1
        for proposed, truth in pairs
        if proposed is not None and proposed.strip().casefold() == truth.strip().casefold()
    )
    return ratio(matched, len(pairs))


def e8_prose_grounding(
    summaries: Sequence[str], forbidden: Sequence[Sequence[str]]
) -> float | None:
    """Share of summaries containing no claim absent from the input attributes.

    **This is the deterministic half of E8 and it is not the whole of E8.** `04`
    §8 specifies *"a pinned judge plus a 20% human spot-check"*; what is
    computable without a model is the forbidden-claim check -- a summary asserting
    a property the input never carried. A judge and a spot-check are what
    `04` §8 asks for on top, and `05` S1.8 is where they land with the corpus that
    can support them. Scoring only this and calling it E8 would be the metric
    naming one thing and measuring another that B1-CR-78 is about.
    """
    clean = sum(
        1
        for summary, claims in zip(summaries, forbidden, strict=True)
        if not any(claim.casefold() in summary.casefold() for claim in claims)
    )
    return ratio(clean, len(summaries))
