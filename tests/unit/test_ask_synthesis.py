"""Critical semantic invariant #7: ungrounded synthesis is discarded, always.

v6.1 §4 R6: *a synthesis that cites no store revision is discarded, never served,
never lands as knowledge.* The first clause is easy and the other two are where
this fails in practice -- a discarded answer that still reached a terminal is
served, and a discarded answer that still reached a store is knowledge.

**The dangerous case is not "cited nothing".** It is a citation that *looks*
right: a `krev_01...` the model produced from the shape of the ones it was shown,
or one from a different question's passages. That reads as grounded to every
human who sees it and sends an FDE looking for knowledge that does not exist. So
it gets a test of its own, and the assertion is on the whole citation set rather
than on its length.

Driven through the real `Runner` against the recorded fake adapter (AI spec §2,
kind `test`), so the whole seam is exercised -- budget, annex, prompt loading --
and the only thing scripted is what the model said.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from adopt_ask.branch import KNOWN, STALE, UNKNOWN, Answer, Citation
from adopt_ask.synthesis import (
    SYNTHESIS_PROMPT_REF,
    Synthesis,
    build_inputs,
    ground,
    render_with,
    synthesize,
)

from adopt_agent import Runner
from adopt_agent.annex import AnnexRecords
from adopt_store.annex import open_annex

pytestmark = pytest.mark.unit

GOOD = "krev_01AAAAAAAAAAAAAAAAAAAAAAAA"
ALSO_GOOD = "krev_01BBBBBBBBBBBBBBBBBBBBBBBB"
#: Shaped exactly like a real revision id and belonging to no revision. The whole
#: point: nothing about the string itself gives it away.
FABRICATED = "krev_01CCCCCCCCCCCCCCCCCCCCCCCC"


def _citation(revision_id: str = GOOD, *, title: str = "Refund approvals") -> Citation:
    return Citation(
        revision_id=revision_id,
        item_id="ki_01AAA",
        title=title,
        body_md="The approval step exists because chargebacks were disputed.",
        identity_uris=("onboard-v1://acme/plat/orders/prod/endpoint/-/POST %2Fv1%2Frefunds",),
        origin="ranked",
        freshness_state="unverified",
        deciding_rule="no_rule_fired",
    )


def _known(*revision_ids: str) -> Answer:
    return Answer(
        question="why does the approval step exist on refunds?",
        branch=KNOWN,
        citations=tuple(_citation(value) for value in (revision_ids or (GOOD,))),
    )


@pytest.fixture
def annex(tmp_path: Path) -> Iterator[AnnexRecords]:
    with open_annex(tmp_path / ".adopt" / "runtime.db") as records:
        yield records


@pytest.fixture
def prompts_root() -> Path:
    """The **real** `prompts/` directory, so the shipped `ask-001` is loaded.

    A synthetic skill would test the seam and leave the actual prompt file
    unexercised -- and a prompt that fails to load makes every synthesis discard
    silently, which is indistinguishable from a well-behaved model declining to
    answer.
    """
    return Path(__file__).resolve().parents[2] / "prompts"


def _runner(annex: AnnexRecords, prompts_root: Path, tmp_path: Path, *turns: str) -> Runner:
    endpoint = tmp_path / "recorded.json"
    endpoint.write_text(
        json.dumps(
            {
                "turns": [
                    {"text": text, "tool_calls": [], "input_tokens": 10, "output_tokens": 5}
                    for text in turns
                ]
            }
        ),
        encoding="utf-8",
    )
    return Runner(
        annex=annex,
        scope_ref="acme/plat",
        skills_root=prompts_root,
        offline=True,
        adapter_id="fake_recorded",
        endpoint=str(endpoint),
    )


class TestInvariantSevenThroughTheSeam:
    def test_a_synthesis_citing_nothing_is_discarded(
        self, annex: AnnexRecords, prompts_root: Path, tmp_path: Path
    ) -> None:
        """*Fails when* an uncited synthesis is returned instead of `None`.
        *Matters because* the caller's only signal to serve the extractive answer
        is that `None` -- a `Synthesis` here is served, and an uncited paragraph
        about a client's system is the unqualified guess this entire build exists
        to prevent. *No other instrument catches it because* the prose is
        fluent, plausible and structurally valid; nothing downstream can tell it
        was not grounded.
        """
        runner = _runner(
            annex,
            prompts_root,
            tmp_path,
            json.dumps({"answer_md": "Approvals exist to stop fraud.", "cited_revision_ids": []}),
        )

        assert synthesize(runner, _known(), idempotency_key="k-1") is None

    def test_a_synthesis_citing_a_revision_that_was_not_retrieved_is_discarded(
        self, annex: AnnexRecords, prompts_root: Path, tmp_path: Path
    ) -> None:
        """*Fails when* the citation set is checked for emptiness but not for
        membership. *Matters because* a fabricated `krev_...` is the worst
        output this path can produce: it reads as grounded to every human who
        sees it, and it sends an FDE looking for knowledge the store does not
        hold. *No other instrument catches it because* the id is well-formed, the
        answer is cited, and every superficial check passes."""
        runner = _runner(
            annex,
            prompts_root,
            tmp_path,
            json.dumps(
                {
                    "answer_md": "Approvals exist because of chargebacks.",
                    "cited_revision_ids": [FABRICATED],
                }
            ),
        )

        assert synthesize(runner, _known(), idempotency_key="k-2") is None

    def test_a_partly_fabricated_citation_set_is_discarded_whole(
        self, annex: AnnexRecords, prompts_root: Path, tmp_path: Path
    ) -> None:
        """*Fails when* foreign ids are filtered out and the rest served.
        *Matters because* filtering leaves prose that was written against a
        source the reader can no longer see, attributed to sources that did not
        say it -- worse than either discarding or serving whole, because it
        looks fully checked. *No other instrument catches it because* the
        surviving citations all resolve."""
        runner = _runner(
            annex,
            prompts_root,
            tmp_path,
            json.dumps({"answer_md": "Two reasons.", "cited_revision_ids": [GOOD, FABRICATED]}),
        )

        assert synthesize(runner, _known(GOOD, ALSO_GOOD), idempotency_key="k-3") is None

    def test_a_grounded_synthesis_survives_and_carries_its_citations(
        self, annex: AnnexRecords, prompts_root: Path, tmp_path: Path
    ) -> None:
        """The positive control. Without it every discard test above passes over
        a function that returns `None` unconditionally -- the measurement that
        succeeds by having nothing to measure, which this repository has now
        found seven times."""
        runner = _runner(
            annex,
            prompts_root,
            tmp_path,
            json.dumps(
                {
                    "answer_md": "The approval step exists because chargebacks were disputed.",
                    "cited_revision_ids": [GOOD],
                }
            ),
        )

        result = synthesize(runner, _known(), idempotency_key="k-4")

        assert result is not None
        assert result.cited_revision_ids == (GOOD,)
        assert "chargebacks" in result.answer_md

    def test_an_unknown_answer_is_never_sent_to_a_model(
        self, annex: AnnexRecords, prompts_root: Path, tmp_path: Path
    ) -> None:
        """*Fails when* synthesis runs on an UNKNOWN. *Matters because* an
        UNKNOWN carries no citations, so the only thing a model could do with it
        is invent the answer the store just said it does not have -- and it
        would be ungrounded by construction, so the call is pure cost and pure
        risk. *No other instrument catches it because* `ground` would discard the
        result anyway: the defect is silent spend and a provider round trip on
        every refusal.

        The recorded fake is given **no turns**, so a call would raise rather
        than quietly succeed."""
        runner = _runner(annex, prompts_root, tmp_path)

        assert (
            synthesize(
                runner,
                Answer(question="how do I rotate the API key?", branch=UNKNOWN, citations=()),
                idempotency_key="k-5",
            )
            is None
        )


class TestGroundingInIsolation:
    @pytest.mark.parametrize(
        "output",
        [
            None,
            42,
            "not json at all",
            '{"answer_md": "x"}',
            '{"cited_revision_ids": ["krev_01AAAAAAAAAAAAAAAAAAAAAAAA"]}',
            '{"answer_md": "   ", "cited_revision_ids": ["krev_01AAAAAAAAAAAAAAAAAAAAAAAA"]}',
            '{"answer_md": "x", "cited_revision_ids": "krev_01AAAAAAAAAAAAAAAAAAAAAAAA"}',
            "[]",
        ],
    )
    def test_every_malformed_output_discards(self, output: object) -> None:
        """A model that replied with something unexpected gets the same answer as
        one that replied with nothing: the extractive answer serves."""
        assert ground(output, {GOOD}) is None

    def test_a_fenced_json_reply_is_still_read(self) -> None:
        """*Fails when* the fence strip is removed. *Matters because* CR-52
        records a frontier model fencing its JSON on this repository's own
        conformance run and burning the seam's single retry on it -- so this is
        an observed behaviour, not a hypothetical. *No other instrument catches
        it because* the discard is silent and looks exactly like a model
        declining to answer."""
        fenced = '```json\n{"answer_md": "Because chargebacks.", "cited_revision_ids": ["%s"]}\n```'
        result = ground(fenced % GOOD, {GOOD})

        assert result is not None
        assert result.cited_revision_ids == (GOOD,)

    def test_stripping_a_fence_cannot_smuggle_a_foreign_citation(self) -> None:
        """The fence tolerance is a parsing convenience and never a grounding
        exemption: every citation is checked afterwards, whatever wrapper it
        arrived in."""
        fenced = '```json\n{"answer_md": "x", "cited_revision_ids": ["%s"]}\n```'
        assert ground(fenced % FABRICATED, {GOOD}) is None


class TestWhatIsSentAndWhatIsShown:
    def test_only_served_passages_reach_the_prompt(self) -> None:
        """*Fails when* withheld unverified revisions are sent for synthesis.
        *Matters because* the branch withheld them precisely because no human
        confirmed them (F6) -- a model quoting one would launder unverified
        content into a served answer, past the filter that exists to stop
        exactly that. *No other instrument catches it because* the answer's own
        citations would still all be verified; the unverified text would simply
        be in the prose."""
        answer = Answer(
            question="why?",
            branch=KNOWN,
            citations=(_citation(GOOD),),
            withheld=(FABRICATED,),
        )

        inputs = build_inputs(answer)

        assert GOOD in inputs["passages"]
        assert FABRICATED not in inputs["passages"]
        assert inputs["passage_count"] == 1

    def test_the_rendering_keeps_the_citations_beside_the_prose(self) -> None:
        """*Fails when* a synthesized answer renders without its citations.
        *Matters because* the citations are what a reader checks the prose
        against -- and a grounded paragraph shown without them is
        indistinguishable, to the person reading it, from the confident uncited
        one this build exists to make impossible."""
        answer = _known()
        rendered = render_with(
            answer, Synthesis(answer_md="Because chargebacks.", cited_revision_ids=(GOOD,))
        )

        assert "Because chargebacks." in rendered
        assert GOOD in rendered
        assert "Refund approvals" in rendered

    def test_a_stale_synthesis_still_names_its_cause(self) -> None:
        """Synthesis improves the prose and never suppresses the warning."""
        stale = Answer(
            question="why?",
            branch=STALE,
            citations=(_citation(GOOD),),
            cause="load_bearing_identity_moved",
        )

        rendered = render_with(
            stale, Synthesis(answer_md="Because chargebacks.", cited_revision_ids=(GOOD,))
        )

        assert "STALE because load_bearing_identity_moved" in rendered

    def test_the_prompt_ref_actually_loads(self, prompts_root: Path) -> None:
        """*Fails when* the ref does not resolve to a shipped, well-formed skill.
        *Matters because* a load failure discards **every** synthesis, silently
        and identically to a model declining to answer -- and it did: the first
        draft of `SYNTHESIS_PROMPT_REF` used `ask-001@v1`, the loader resolves a
        ref as a path, and all four invariant-#7 discard tests passed over a
        function that was returning `None` unconditionally. *No other instrument
        catches it because* discarding is the safe outcome, so nothing anywhere
        goes red; only the positive control noticed.

        Asserting the load rather than the files, because "the directory
        exists" is what the broken version also satisfied."""
        from adopt_agent.skills import load_skill

        assert SYNTHESIS_PROMPT_REF == "ask-001/v1"
        loaded = load_skill(SYNTHESIS_PROMPT_REF, root=prompts_root)

        assert loaded.name == "ask-001"
        assert loaded.user_template is not None, "the user template carries the passages"
        assert loaded.output_schema is not None, "the seam enforces the declared shape"
