"""The E1-E9 suites, and the guard that stops a green run meaning nothing.

Two populations of test live here and they answer different questions:

* **The scorer tests** run always. They are the instrument's own calibration --
  each one plants a population whose correct score is known and asserts the
  scorer returns it, including the `None` every scorer owes an empty population.
  A scorer that silently returned `0.0` for "nothing measured" would let every
  threshold below pass on a run that called no model.
* **The E1-E9 suites** run only when `--eval-adapters` names one, and they call a
  real provider. With no adapter named they parameterize over **zero** cases and
  the session header says E1-E9 did not execute -- see `conftest.py`.

**The anti-vacuity guard is built from a different source than the
parameterization it checks** (B1-CR-69). `test_suites_exist_when_adapters_named`
reads `--eval-adapters` directly from the config and counts collected eval items
through pytest's own record, so a parameterization that silently produced nothing
cannot also silently satisfy its own guard.
"""

import json
from pathlib import Path
from typing import Any, Final

import pytest

from tests.evals.scorers import (
    E4_FLOOR,
    THRESHOLDS,
    e1_glue_safety,
    e2_glue_viability,
    e3_glue_precision,
    e4_approved_unmodified,
    e5_decline_calibration,
    e6_label_restraint,
    e7_label_precision,
    e8_prose_grounding,
    ratio,
)

pytestmark = pytest.mark.evals

GOLDEN: Final[Path] = Path(__file__).resolve().parents[2] / "fixtures" / "golden"


def _load(name: str) -> Any:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------- golden shape


def test_golden_sets_have_the_04_8_cardinalities() -> None:
    """Ten glue tasks, five decline tasks, fifteen label cases, twenty prose cases.

    `04` §8 states each count, and a golden set that quietly shrank would move
    every threshold below it without any of them failing. Asserted on the files
    rather than on a constant, because the files are what the suites read.
    """
    glue = _load("glue/tasks.json")["tasks"]
    authored = [task for task in glue if task["expect_outcome"] == "authored"]
    declined = [task for task in glue if task["expect_outcome"] == "declined"]
    assert len(authored) == 10, "`04` §8: ten map-glue-001 tasks"
    assert len(declined) == 5, "`04` §8 E5: five genuinely non-static tasks"
    assert len(_load("label/cases.json")["cases"]) == 15, "`04` §8: fifteen label cases"
    assert len(_load("prose/cases.json")["cases"]) == 20, "`04` §8: twenty prose cases"


def test_glue_tasks_cover_the_named_repository_shapes() -> None:
    """`04` §8 names five shapes outside the shipped packs; each must appear."""
    shapes = {task["shape"] for task in _load("glue/tasks.json")["tasks"]}
    assert shapes == {"rails", "spring", "terraform", "hono-ts", "legacy-php"}


def test_label_set_has_an_evidence_free_subset() -> None:
    """E6 is scored over the evidence-free cases only, so they must exist.

    Without this, a label set of fifteen evidenced cases would make E6's
    denominator zero, its score `None`, and its assertion a failure nobody could
    fix by improving the model -- which is a golden-set defect wearing a model's
    clothes.
    """
    cases = _load("label/cases.json")["cases"]
    assert sum(1 for case in cases if case["expect_empty"]) == 5


# ------------------------------------------------------------ scorer calibration


def test_every_scorer_reports_none_on_an_empty_population() -> None:
    """The `None`-not-zero rule, over every scorer at once.

    Build 0's CR-51 -- *"an undefined ratio reports `null` and never `1.0`"* -- at
    the seven metrics that gate a reversal trigger. Parameterized over the
    functions rather than written seven times, so a scorer added later without the
    rule fails here on the day it is added.
    """
    assert ratio(0, 0) is None
    assert e1_glue_safety([]) is None
    assert e2_glue_viability([]) is None
    assert e3_glue_precision([], []) is None
    assert e4_approved_unmodified([]) is None
    assert e5_decline_calibration([], []) is None
    assert e6_label_restraint([], []) is None
    assert e7_label_precision([], []) is None
    assert e8_prose_grounding([], []) is None


def test_e1_is_one_only_when_no_module_had_a_finding() -> None:
    assert e1_glue_safety([[], [], []]) == 1.0
    assert e1_glue_safety([[], ["uri_construction"]]) == 0.5


def test_e2_counts_unsupported_as_not_viable() -> None:
    """A sandbox that could not run is not a module that ran."""
    assert e2_glue_viability(["ok", "unsupported"]) == 0.5
    assert e2_glue_viability(["timeout", "error"]) == 0.0


def test_e3_scores_precision_and_ignores_missed_identities() -> None:
    labeled = [("endpoint", "rails", "a"), ("endpoint", "rails", "b")]
    emitted = [("endpoint", "rails", "a"), ("endpoint", "rails", "invented")]
    assert e3_glue_precision(emitted, labeled) == 0.5
    # Recall is not this metric's business: finding one of two truths precisely
    # scores 1.0, and `05` S1.8's coverage evidence is where recall is answered.
    assert e3_glue_precision([("endpoint", "rails", "a")], labeled) == 1.0


def test_e4_keeps_rejections_in_the_denominator() -> None:
    """A pass cannot improve its score by producing garbage somebody discarded."""
    assert e4_approved_unmodified(["approved", "rewritten"]) == 0.5
    assert e4_approved_unmodified(["approved", "rejected"]) == 0.5
    assert e4_approved_unmodified(["approved", "quarantined"]) == 1.0


def test_e4_floor_is_derived_from_the_constant_it_proxies() -> None:
    """`04` §8's E4 is `1 - MAP_GLUE_REWRITE_ALERT`, in one home.

    The defect this forbids is S1.5's exactly: a threshold stated in a document
    and retyped in a module measures whatever the author typed. `THRESHOLDS`
    therefore has no `E4` row at all -- absence is what makes the second home
    impossible rather than merely discouraged.
    """
    from adopt_const import MAP_GLUE_REWRITE_ALERT

    assert pytest.approx(1.0 - MAP_GLUE_REWRITE_ALERT) == E4_FLOOR
    assert "E4" not in THRESHOLDS


def test_e5_scores_only_the_non_static_tasks() -> None:
    """Declining everything must not score 1.00 on the eval that rewards declining."""
    outcomes = ["authored", "declined", "declined"]
    expected = ["authored", "declined", "declined"]
    assert e5_decline_calibration(outcomes, expected) == 1.0
    assert e5_decline_calibration(["authored", "authored", "declined"], expected) == 0.5


def test_e6_scores_only_the_evidence_free_fields() -> None:
    assert e6_label_restraint([0, 3], [True, False]) == 1.0
    assert e6_label_restraint([2, 0], [True, True]) == 0.5


def test_e7_matches_case_insensitively() -> None:
    assert e7_label_precision(["credit limit"], ["Credit Limit"]) == 1.0
    assert e7_label_precision([None], ["Credit Limit"]) == 0.0
    # An evidence-free case has no truth and is not in E7's population at all.
    assert e7_label_precision(["anything", "credit limit"], [None, "Credit Limit"]) == 1.0


def test_e8_fails_a_summary_asserting_something_the_input_never_carried() -> None:
    assert e8_prose_grounding(["Accepts POST at /v1/orders."], [["fast", "reliable"]]) == 1.0
    assert e8_prose_grounding(["A fast endpoint."], [["fast"]]) == 0.0


# ------------------------------------------------------------- the live suites


def test_suites_exist_when_adapters_named(request: pytest.FixtureRequest) -> None:
    """The anti-vacuity guard. B1-CR-69, built from a different source.

    S1.5 found the conformance suite parameterized over one pack while its own
    guard compared that parameterization against a registry built from the same
    parameterization -- consistent with itself and blind. So this reads the raw
    option string and the collected node ids from pytest's session, neither of
    which is the `pytest_generate_tests` hook being checked.
    """
    raw = str(request.config.getoption("--eval-adapters")).strip()

    # **B1-CR-100.** This once read `"eval_target" in item.nodeid`, and the ids
    # `pytest_generate_tests` builds are `f"{adapter}={model}"` -- so the fixture
    # name appears in no node id and the match was always empty. The `not raw`
    # branch passed anyway (empty is what it wants), and the `raw` branch could
    # not be reached at all, because `--eval-adapters` was declared in a conftest
    # pytest never loaded during argument parsing. **A guard against vacuity,
    # itself vacuous, behind a flag that could not be passed.**
    #
    # The check is now against the **option string**, parsed here rather than
    # imported from the hook under test: every `adapter=model` the operator named
    # must appear as a collected id. Two independent sources, which is B1-CR-69's
    # rule -- a guard built from the parameterization it is checking proves only
    # that the code agrees with itself.
    #
    # `[NOTSET]` is pytest's own id for a parametrize call given an **empty**
    # list: the function is still collected, as a single placeholder that reports
    # as skipped. It is therefore exactly what "no adapters were named" looks
    # like from here, and counting it as a real case would make the empty run
    # indistinguishable from a populated one -- the same confusion in the
    # opposite direction.
    named = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    collected = [
        item.nodeid
        for item in request.session.items
        if "test_e1_e9_over_one_adapter" in item.nodeid and "[NOTSET]" not in item.nodeid
    ]

    if not named:
        assert not collected, (
            "no --eval-adapters were named but adapter-parameterized cases were "
            "collected; something is supplying a default, which is how a suite "
            "reports green against a model nobody chose"
        )
        return

    missing = [target for target in named if not any(f"[{target}]" in node for node in collected)]
    assert not missing, (
        f"--eval-adapters named {named!r} and {missing!r} parameterized no case. "
        f"Collected: {collected!r}. The suites did not run for those adapters; a "
        "green session here is not evidence about any `04` §8 threshold."
    )


def test_e1_e9_over_one_adapter(eval_target: tuple[str, str], tmp_path: Path) -> None:
    """E1-E9 against one named adapter. `04` §8; **E9 is this parameterization**.

    **This body has never executed.** No provider credential exists in the session
    that wrote it, so `05` S1.7's Final Output Validation lines 2 and 3 stay
    unchecked and `docs/pack/OPEN-DECISIONS.md` **OD-16** records that E9's
    adapter pair is B1-CR-31's flagged default -- two non-test adapters, per Build
    0 CR-48. It ships rather than being stubbed because a test that failed by
    design would block the first person who does hold a key, and because `04` §8's
    thresholds are this sprint's deliverable even when the run that reads them
    belongs to a later session.

    What *has* been observed is `test_eval_driver_plumbing_with_a_stub_runner`,
    which drives this same `run_eval_suite` over a fake that opens no socket.
    Structural conformance is not evidence of behaviour, so that test claims the
    plumbing and nothing about a threshold.
    """
    from tests.evals.driver import run_eval_suite

    adapter_id, model = eval_target
    scores = run_eval_suite(_live_runner(adapter_id, model), workdir=tmp_path)

    assert scores["E1"] == THRESHOLDS["E1"], "E1 has no tolerance (`04` §8)"
    for name in ("E2", "E3", "E5", "E6", "E7", "E8"):
        measured = scores[name]
        assert measured is not None, f"{name} scored an empty population; nothing was measured"
        assert measured >= THRESHOLDS[name], f"{name}={measured} is below {THRESHOLDS[name]}"
    approved = scores["E4"]
    assert approved is not None, "E4 scored an empty population"
    assert approved >= E4_FLOOR


def _live_runner(adapter_id: str, model: str) -> Any:
    """Build 0's seam pointed at one adapter, for one eval run."""
    from adopt_agent import Runner
    from adopt_cli.commands.agent import prompts_root
    from adopt_cli.glue_runner import SeamGlueRunner

    runner = Runner(
        annex=_NoAnnex(),
        scope_ref="adopt-map-evals",
        skills_root=prompts_root(),
        offline=False,
        adapter_id=adapter_id,
        model=model,
    )
    return SeamGlueRunner(runner, adapter=adapter_id)


class _NoAnnex:
    """No replay, deliberately.

    `04` §8 asks for variance alongside the mean *"because a single passing run is
    not evidence"*. An annex would replay the first run and report a variance of
    zero over one real call, which is the opposite of that.
    """

    def find_run(self, *, scope_ref: str, idempotency_key: str) -> None:
        return None

    def record_run(self, *args: Any, **kwargs: Any) -> None:
        return None


def test_eval_driver_plumbing_with_a_stub_runner(tmp_path: Path) -> None:
    """The driver runs end to end over a fake that opens no socket.

    **Plumbing only, and the two scores asserted are properties of the stub.** It
    reaches every golden case, routes each authored reply through the real
    quarantine pipeline, and returns one score per eval. It says nothing about any
    `04` §8 threshold -- see `stub_runner.py` for why a scripted fake structurally
    cannot.
    """
    from tests.evals.driver import run_eval_suite
    from tests.evals.stub_runner import StubRunner

    scores = run_eval_suite(StubRunner(), workdir=tmp_path)

    assert set(scores) == {"E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8"}
    assert scores["E1"] == 1.0, "the stub's module is audit-clean by construction"
    assert scores["E5"] == 1.0, "the stub declines every non-static task"
    assert scores["E6"] == 1.0, "the stub returns an empty candidate list for every field"
    # E2 reports what the platform did rather than what the suite hoped: on a
    # machine whose sandbox cannot enforce the ceilings it is 0.0, and that is the
    # honest number (`adopt_map.sandbox`).
    assert scores["E2"] is not None
