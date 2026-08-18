"""The six-condition gate and its sample -- `04` §2, §7, `05` S1.7.

`05` S1.7 asks for *"one test per gate condition"* and for budget exhaustion
*"asserting exit 6 **and** a complete deterministic map"*. Both are here, and the
per-condition suite is **parameterized over `GATE_CONDITIONS`** rather than
written six times: a condition added to `04` §2 without a case fails
`test_every_condition_has_a_case` on the day it is added, which is the coverage
claim the checkbox is really making.

The defect sentence for the group: *these fail when a condition stops being able
to refuse; it matters because each one is the only thing standing between a
client tree and a model call; no other instrument catches it because a passing
gate and an absent gate produce identical output on a run where nothing was
wrong.*
"""

import io
import json
from pathlib import Path

import pytest
from adopt_map.agent_gate import (
    GATE_CONDITIONS,
    AgentBudget,
    GateInputs,
    evaluate,
    family_coverage,
    sample_digest,
    select_samples,
)
from adopt_map.fileindex import build_index

from adopt_const import (
    MAP_AGENT_MAX_FILE_BYTES,
    MAP_AGENT_MAX_FILES_SAMPLED,
    MAP_STAGE1_REQUIRED_FAMILIES,
)
from adopt_obs import (
    AdoptError,
    ErrorCode,
    LogLevel,
    MapExitCode,
    map_exit_code_for,
    set_sink,
)

pytestmark = pytest.mark.unit

#: Facts covering every required family, so G-3 refuses unless a case says
#: otherwise. Built from the constant rather than typed out, so a change to
#: `MAP_STAGE1_REQUIRED_FAMILIES` moves the fixture with it.
_ALL_REACHED = dict.fromkeys(MAP_STAGE1_REQUIRED_FAMILIES, 1)


def _passing() -> GateInputs:
    """Inputs on which all six conditions hold. Each case breaks exactly one."""
    return GateInputs(
        agent_flag=True,
        config_enabled=True,
        deterministic_complete=True,
        facts_by_kind={**_ALL_REACHED, "endpoint": 0},
        budget=AgentBudget(),
        tier="T2",
        target_kinds=("endpoint",),
        target_namespaces=("http",),
    )


def test_the_passing_inputs_actually_pass() -> None:
    """The positive control, and it is not optional.

    Every case below asserts a refusal. Without this, a gate that refused
    *everything* -- one condition inverted, one field misread -- would satisfy all
    six of them perfectly. That is S1.6's FOV line 2 lesson and B1-CR-69's, in the
    one place where a false refusal is invisible: a skipped pass looks exactly like
    a successful run.
    """
    assert evaluate(_passing()).allowed


@pytest.mark.parametrize(
    ("condition", "mutation"),
    [
        ("G-1", {"agent_flag": False}),
        ("G-2", {"deterministic_complete": False}),
        ("G-3", {"facts_by_kind": _ALL_REACHED}),
        ("G-4", {"budget": None}),
        ("G-5", {"tier": "T0"}),
        ("G-6", {"target_kinds": ("ui_component",)}),
    ],
)
def test_each_condition_refuses_on_its_own(condition: str, mutation: dict[str, object]) -> None:
    """One mutation, one refusal, named."""
    decision = evaluate(GateInputs(**{**vars_of(_passing()), **mutation}))
    assert not decision.allowed
    assert decision.failed == condition
    assert decision.detail


def vars_of(inputs: GateInputs) -> dict[str, object]:
    """A frozen slots dataclass's fields as a mapping, for one-field mutation."""
    return {name: getattr(inputs, name) for name in GateInputs.__slots__}


def test_every_condition_has_a_case() -> None:
    """The vocabulary and the suite cannot drift apart.

    Compared against `GATE_CONDITIONS` rather than against a count, because a
    seventh condition named `G-7` and a renamed `G-4` are different defects and a
    count catches neither.
    """
    covered = {case.values[0] for case in _parametrized_cases()}
    assert covered == set(GATE_CONDITIONS)


def _parametrized_cases() -> list[pytest.param]:  # type: ignore[valid-type]
    marker = next(
        mark
        for mark in test_each_condition_refuses_on_its_own.pytestmark  # type: ignore[attr-defined]
        if mark.name == "parametrize"
    )
    return [pytest.param(case[0]) for case in marker.args[1]]


def test_g1_needs_both_halves() -> None:
    """`01` F12.1: *"`--agent` **and** `agent.enabled` -- both, neither alone"*."""
    inputs = _passing()
    assert not evaluate(GateInputs(**{**vars_of(inputs), "agent_flag": False})).allowed
    assert not evaluate(GateInputs(**{**vars_of(inputs), "config_enabled": False})).allowed


def test_g3_opens_for_a_named_family_even_when_everything_was_reached() -> None:
    """`04` §2 G-3's second arm: *"or the operator named a family"*."""
    inputs = GateInputs(
        **{
            **vars_of(_passing()),
            "facts_by_kind": _ALL_REACHED,
            "requested_families": ("graphql",),
        }
    )
    assert evaluate(inputs).allowed


def test_g3_scores_presence_and_not_a_ratio() -> None:
    """B1-CR-84: a required family with zero deterministic facts is unreached.

    The two ratios this pack offers both read 0.000 on a healthy run -- coverage
    because `audience_tag` is unwritable (B1-CR-62), M2 because it measures the
    evidence rung (B1-CR-78) -- so a gate built on either would open every time.
    """
    scores = family_coverage({**_ALL_REACHED, "endpoint": 0})
    assert scores["endpoint"] == 0.0
    assert all(scores[kind] == 1.0 for kind in MAP_STAGE1_REQUIRED_FAMILIES if kind != "endpoint")
    assert set(scores) == set(MAP_STAGE1_REQUIRED_FAMILIES)


def test_g6_refuses_a_secret_namespace_and_client_execution() -> None:
    """The other two members of `04` §2 G-6's never-agent list."""
    inputs = vars_of(_passing())
    secret = evaluate(GateInputs(**{**inputs, "target_namespaces": ("secret:vault",)}))
    executing = evaluate(GateInputs(**{**inputs, "requires_client_execution": True}))
    assert (secret.failed, executing.failed) == ("G-6", "G-6")


@pytest.mark.parametrize(
    ("kwargs", "which"),
    [
        ({"usd": 99.0}, "cost"),
        ({"seconds": 10_000.0}, "wall_clock"),
        ({"files": MAP_AGENT_MAX_FILES_SAMPLED}, "files_sampled"),
    ],
)
def test_budget_exhaustion_raises_the_exit_6_code(kwargs: dict[str, float], which: str) -> None:
    """`04` §7 and `02` §8: every budget aborts the pass at **exit 6**.

    The exit code is asserted through `map_exit_code_for` rather than by reading a
    literal `6`, because `02` §8's table is the contract and B1-CR-35 records that
    six of the fourteen codes disagree with Build 0's category default.
    """
    budget = AgentBudget()
    with pytest.raises(AdoptError) as raised:
        budget.spend(**kwargs)
    assert raised.value.code is ErrorCode.MAP_AGENT_BUDGET_EXHAUSTED
    assert map_exit_code_for(raised.value.code) == MapExitCode.AGENT_BUDGET_EXHAUSTED
    assert budget.exhausted_by == which


def test_headroom_is_false_at_the_ceiling_and_true_below_it() -> None:
    """G-4 asks whether a prompt's declared minimum fits, not whether any budget exists."""
    budget = AgentBudget(max_cost_usd=1.0)
    assert budget.has_headroom()
    budget.spent_usd = 1.0
    assert not budget.has_headroom()


def test_samples_are_sorted_by_path_and_identical_across_calls(tmp_path: Path) -> None:
    """`04` §7: deterministic sample selection.

    The digest is compared rather than the list, because that is what a review row
    carries: two runs whose samples differ in order would produce two review rows
    a human cannot tell apart.
    """
    for name in ("c.py", "a.py", "b.py"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    index = build_index(tmp_path)

    first, omitted = select_samples(index)
    second, _ = select_samples(index)

    assert [sample.path for sample in first] == sorted(sample.path for sample in first)
    assert sample_digest(first) == sample_digest(second)
    assert omitted == 0


def test_truncation_is_disclosed_on_the_file_and_in_the_count(tmp_path: Path) -> None:
    """`04` §7: *"head-truncate with a visible marker"*, and the omitted count.

    Two disclosures because they answer different questions: the marker tells a
    reviewer this file is partial, and the count tells them files exist that the
    prompt never saw at all.
    """
    (tmp_path / "big.py").write_text("#" * (MAP_AGENT_MAX_FILE_BYTES + 10), encoding="utf-8")
    (tmp_path / "small.py").write_text("x = 1\n", encoding="utf-8")
    index = build_index(tmp_path)

    samples, omitted = select_samples(index, limit=1)

    assert omitted == 1
    big = next(sample for sample in samples if sample.path.endswith("big.py"))
    assert big.truncated
    assert "truncated at" in big.text


def test_a_refused_gate_records_the_condition() -> None:
    """`04` §2: a skip records `agent_gate_skipped` with the failing condition.

    A structured event, never an error code: `02` §1.4 registers fourteen `MAP_*`
    codes and a skipped pass is not one of them, because it is not a failure. Read
    through `set_sink` rather than `caplog`, because `adopt_obs.get_logger` is not
    a `logging` logger -- it is the structured emitter `03` §6 requires, and a test
    reading `caplog` would pass on a run that logged nothing at all.
    """
    sink = io.StringIO()
    set_sink(sink, min_level=LogLevel.DEBUG)
    try:
        evaluate(GateInputs(**{**vars_of(_passing()), "agent_flag": False})).record()
        evaluate(_passing()).record()
    finally:
        set_sink(io.StringIO())

    lines = [json.loads(line) for line in sink.getvalue().splitlines() if line.strip()]
    skips = [line for line in lines if line["event"] == "agent_gate_skipped"]
    assert [skip["condition"] for skip in skips] == ["G-1"], (
        "exactly one skip should be recorded: the refusal. An allowed decision "
        "that also recorded one would make every successful pass look skipped."
    )
