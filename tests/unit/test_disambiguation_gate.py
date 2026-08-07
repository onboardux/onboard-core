"""The `04` §4 pre-model gate: what the model is asked, and when it is not asked.

*Fails when* a model call happens on a path the AI spec makes deterministic, or
when the evidence sent to one carries more than scores, rule names, paths and a
bounded listing. *Matters because* step 4's exclusion is a **privacy invariant**,
not a token-saving measure: it is what lets the offline and
no-content-leaves-the-environment claims survive a security review *even when the
flag is on*, and an FDE runs this inside a client environment against code they do
not own. *No other instrument catches it because* `tests/property/test_offline.py`
proves no socket opens with the flag **off**, which is the easy half -- nothing
else looks at what the request would carry once it is on.

**The runner here is a recording fake, not an adapter.** The claim under test is
what the *caller* sends and when, so the instrument is a stand-in for `02` §10.1's
Protocol that records the request and never answers a provider. Driving this
through `fake_recorded` would put the seam's whole loop between the assertion and
the thing asserted.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from adopt_agent import AgentRequest, AgentResult, Cost, Trace
from adopt_const import (
    AGENT_DETECT_LISTING_MAX_ENTRIES,
    AGENT_DETECT_MAX_USD,
    AGENT_DETECT_MAX_WALL_SECONDS,
)
from adopt_detect import detect as run_detect
from adopt_detect.detect import bounded_listing
from adopt_detect.disambiguate import (
    DISAMBIGUATION_PROMPT_REF,
    ArchetypeProposal,
    build_evidence,
    propose,
)
from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit

REPOS = Path(__file__).resolve().parent.parent / "fixtures" / "repos"
#: The tree S6 built to be genuinely ambiguous -- a Django service with a dbt
#: project inside it. Using the corpus's own refusal case rather than a tree
#: invented here keeps this test measuring the same ambiguity the detector reports.
AMBIGUOUS = REPOS / "_mixed" / "django_with_dbt"

_PROPOSAL: dict[str, Any] = {
    "primary": "web",
    "confidence": 0.6,
    "reasoning": "Django settings and manage.py dominate; the dbt project is secondary.",
    "secondary": ["data"],
}


class _RecordingRunner:
    """Realizes `02` §10.1's `AgentRunner` and records what it was asked.

    Returns a fixed proposal, because what is under test is the request. A runner
    that answered differently per call would make these assertions about the fake.
    """

    def __init__(self, output: dict[str, Any] | None = None) -> None:
        self.requests: list[AgentRequest] = []
        self._output = output if output is not None else _PROPOSAL

    def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            status="ok",
            output=self._output,
            artifacts=[],
            cost=Cost(input_tokens=1, output_tokens=1, usd=0.0, wall_ms=1),
            trace=Trace(
                adapter="fake_recorded",
                model="recorded",
                params_hash="0" * 64,
                skill_ref=request.skill_ref,
                skill_sha256="0" * 64,
                inputs_sha256="0" * 64,
                steps=[],
            ),
        )

    def adapters(self) -> list[Any]:
        return []


def test_a_confident_result_is_never_sent_to_a_model() -> None:
    """`04` §4 step 2, enforced at the pass rather than trusted of the caller.

    A caller that skipped its own ambiguity check would otherwise send a resolved
    tree to a provider -- paid for, and for an answer already known.
    """
    result = run_detect(REPOS / "web" / "django_shop")
    runner = _RecordingRunner()

    assert result.ambiguous is False  # the guard: this tree must be confident
    with pytest.raises(AdoptError) as raised:
        propose(result, root=REPOS / "web" / "django_shop", runner=runner)

    assert raised.value.code is ErrorCode.DETECT_AMBIGUOUS
    assert runner.requests == [], "a confident result reached the runner"


def test_the_evidence_carries_no_file_contents() -> None:
    """The privacy invariant, asserted over the serialized request.

    Searched as a whole rather than field by field: an implementation that added
    contents under a key this test did not think of would still be caught. The
    planted needle is a string that exists **only inside a fixture file's body**,
    so a match means file contents left the tree.
    """
    result = run_detect(AMBIGUOUS)
    runner = _RecordingRunner()

    propose(result, root=AMBIGUOUS, runner=runner)

    assert len(runner.requests) == 1
    sent = runner.requests[0].model_dump_json()
    for needle in _file_body_needles(AMBIGUOUS):
        assert needle not in sent, f"file content {needle!r} reached the request"


def _file_body_needles(root: Path) -> list[str]:
    """Distinctive lines from inside the fixture's files.

    Read from the tree at test time rather than hard-coded, so a fixture gaining a
    file strengthens this assertion without anyone remembering to update it -- the
    pattern `tests/property/test_log_egress.py` uses for planted secrets.
    """
    needles: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            # Long enough that a coincidental match is not credible, and not a
            # path -- paths are legitimately in the listing.
            if len(stripped) > 24 and "/" not in stripped:
                needles.append(stripped)
    return needles[:20]


def test_the_evidence_is_exactly_the_four_placeholders_the_prompt_names() -> None:
    """The keys are the contract between `build_evidence` and `detect-001@1`.

    A key the template does not name is dead weight sent to a provider; a missing
    one is a render-time refusal. Both are caught here rather than at the first
    real call.
    """
    result = run_detect(AMBIGUOUS)

    evidence = build_evidence(result, root=AMBIGUOUS)

    assert set(evidence) == {"scores_json", "rules_fired_json", "listing_limit", "listing"}
    assert evidence["listing_limit"] == AGENT_DETECT_LISTING_MAX_ENTRIES
    # The scores are this package's own numbers and the rules are names and paths.
    assert set(json.loads(evidence["scores_json"])) <= {"web", "platform", "lowcode", "data", "ai"}
    for hit in json.loads(evidence["rules_fired_json"]):
        assert set(hit) == {"archetype", "rule", "path", "why"}


def test_the_listing_is_bounded_and_says_when_it_truncated() -> None:
    """A listing that stopped is less evidence than it looks like.

    The bound is exercised **at a limit of one** against the same real tree, so the
    truncation path is driven rather than assumed: the fixture corpus is smaller
    than `AGENT_DETECT_LISTING_MAX_ENTRIES`, so a test that only used the constant
    would never reach the branch that reports truncation, and the marker could be
    removed without anything failing.
    """
    result = run_detect(AMBIGUOUS)
    evidence = build_evidence(result, root=AMBIGUOUS)
    lines = evidence["listing"].splitlines()

    assert lines, "an empty listing would satisfy every other assertion here"
    assert len(lines) <= AGENT_DETECT_LISTING_MAX_ENTRIES
    assert "truncated" not in evidence["listing"]

    clipped, truncated = bounded_listing(AMBIGUOUS, limit=1)

    assert truncated is True
    assert len(clipped) == 1
    # And the full walk really does see more than one path, without which the
    # assertion above would hold for a tree with a single file.
    assert len(bounded_listing(AMBIGUOUS, limit=AGENT_DETECT_LISTING_MAX_ENTRIES)[0]) > 1


def test_the_request_carries_the_prompt_version_and_the_ai_spec_budget() -> None:
    """`04` §5's table, and rule 2: callers name the version, there is no latest."""
    result = run_detect(AMBIGUOUS)
    runner = _RecordingRunner()

    propose(result, root=AMBIGUOUS, runner=runner)
    request = runner.requests[0]

    assert request.skill_ref == DISAMBIGUATION_PROMPT_REF == "detect-001/v1"
    assert request.budget.max_usd == AGENT_DETECT_MAX_USD
    assert request.budget.max_wall_seconds == AGENT_DETECT_MAX_WALL_SECONDS
    # No tools: this prompt asks for a classification, and a tool would be a
    # capability the pass has no use for and the model no reason to be offered.
    assert request.tools == []


def test_the_idempotency_key_is_stable_for_one_tree_and_differs_across_trees() -> None:
    """Re-running the pass over an unchanged tree replays rather than pays twice.

    Both halves matter: a key that varied per call would bill a client for the same
    question, and a key that ignored the evidence would return one tree's proposal
    for another -- which is not a wrong answer but a *different referent*.
    """
    first = _RecordingRunner()
    second = _RecordingRunner()
    third = _RecordingRunner()

    propose(run_detect(AMBIGUOUS), root=AMBIGUOUS, runner=first)
    propose(run_detect(AMBIGUOUS), root=AMBIGUOUS, runner=second)
    other = REPOS / "_mixed"
    propose(run_detect(other), root=other, runner=third)

    assert first.requests[0].idempotency_key == second.requests[0].idempotency_key
    assert first.requests[0].idempotency_key != third.requests[0].idempotency_key


def test_a_proposal_carries_no_way_to_accept_itself() -> None:
    """`01` §8: writing the archetype always requires a human, no exemption.

    Asserted structurally rather than by reading the CLI: a field or method here is
    how a future caller would come to believe the decision had been made, so the
    guarantee is that neither exists.
    """
    proposal = ArchetypeProposal.model_validate(_PROPOSAL)

    assert not hasattr(proposal, "accepted")
    assert not hasattr(proposal, "apply")
    assert "accepted" not in ArchetypeProposal.model_fields
    with pytest.raises(ValueError, match="accepted"):
        ArchetypeProposal.model_validate({**_PROPOSAL, "accepted": True})


def test_an_unusable_reply_degrades_rather_than_inventing_an_answer() -> None:
    """`04` §3's last row: the deterministic path is the product.

    A pass that returned a proposal built from a failed run would be the one place
    a model outage turned into a wrong archetype rather than a missing one.
    """
    result = run_detect(AMBIGUOUS)

    class _Refusing(_RecordingRunner):
        def run(self, request: AgentRequest) -> AgentResult:
            answer = super().run(request)
            return answer.model_copy(update={"status": "budget_exhausted", "output": "partial"})

    with pytest.raises(AdoptError) as raised:
        propose(result, root=AMBIGUOUS, runner=_Refusing())

    assert raised.value.code is ErrorCode.AGENT_OUTPUT_SCHEMA
