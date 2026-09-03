"""Critical semantic invariant #7 for drafts: ungrounded means **nothing is written**.

v6.1 §4 R6 and §6 Build 4: *a draft citing no store fact is discarded.* Build 3
proved the same rule over `adopt_ask.synthesis`, where a discard costs a reader a
prettier paragraph. Here it costs nothing at all -- and that asymmetry is why
these tests exist separately rather than as a parametrisation of Build 3's.

**A draft that should have been discarded and was not is written down.** It
becomes a `knowledge_revision`, bound to an identity, queued where one keystroke
confirms it, and rendered into a document handed to a client. So every discard
test here asserts on the **store**, not on the return value: `ground` returning
`None` is not the property that matters, an empty `knowledge_revision` table is.
A `None` that still wrote a row would pass a return-value assertion perfectly.

Driven through the real `Runner` against the recorded fake adapter (AI spec §2,
kind `test`) and against a real SQLite store, so the whole seam and the whole
write path are exercised and only the model's reply is scripted.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from adopt_knowledge import (
    DRAFT_ACTOR,
    DRAFT_PROMPT_REF,
    DRAFT_PROVENANCE_PREFIX,
    Draft,
    DraftTarget,
    Fact,
    draft_one,
    ground,
    render_body,
    run_drafting,
)
from adopt_knowledge.drafting import build_inputs, idempotency_key_for, title_for

from adopt_agent import Runner
from adopt_agent.annex import AnnexRecords
from adopt_cli.commands._draft_support import DraftStoreAdapter, already_drafted
from adopt_cli.commands._knowledge_support import StoreUnitOfWork
from adopt_obs import ManualClock
from adopt_store import open_store
from adopt_store.annex import open_annex
from tests.golden.fixture import FIXTURE_START

pytestmark = pytest.mark.unit

_URI = "onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/POST %2Fv1%2Forders"
_SPAN = "src/orders/api.py:12-40"
#: Shaped exactly like a fact key this store could have produced, and belonging
#: to no fact that was sent. The whole point: nothing about the string gives it
#: away, and a reader who saw it cited would go looking for a span that is not
#: there.
_FABRICATED = "src/orders/nowhere.py:1-9"

_FACTS = (
    Fact(key=_URI, text="An endpoint named 'POST /v1/orders', first seen 2026-01-01."),
    Fact(key=_SPAN, text="Observed at src/orders/api.py:12-40 by extractor web-fastapi."),
)


@pytest.fixture
def annex(tmp_path: Path) -> Iterator[AnnexRecords]:
    with open_annex(tmp_path / ".adopt" / "runtime.db") as records:
        yield records


@pytest.fixture
def prompts_root() -> Path:
    """The **real** `prompts/` directory, so the shipped `draft-001` is loaded.

    A synthetic skill would test the seam and leave the actual prompt file
    unexercised -- and a prompt that fails to load makes every draft discard
    silently, which is indistinguishable from a model declining to answer. Build
    3 shipped that defect (`name@vN` instead of `name/vN`) and every one of its
    invariant tests passed over a function returning `None` unconditionally. The
    positive control below is what catches it.
    """
    return Path(__file__).resolve().parents[2] / "prompts"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Any]:
    """A real store with one mapped, uncovered identity -- the drafting case."""
    handle = open_store(tmp_path / "store.db", migrate=True, clock=ManualClock(FIXTURE_START))
    scopes = handle.scope()
    firm = scopes.create_firm(slug="northwind", name="Northwind LLP")
    engagement = scopes.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP rollout")
    system = scopes.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    scopes.create_environment(system_id=system.id, slug="prod", name="Production")
    scope = scopes.resolve("northwind/acme-erp/orders-api/prod")
    handle.identities().observe(scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders")
    yield handle
    handle.close()


@pytest.fixture
def scope(store: Any) -> Any:
    return store.scope().resolve("northwind/acme-erp/orders-api/prod")


@pytest.fixture
def target(store: Any) -> DraftTarget:
    identity_id = str(store.backend.query("SELECT id FROM identity LIMIT 1")[0]["id"])
    return DraftTarget(identity_id=identity_id, uri=_URI, kind="endpoint", facts=_FACTS)


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
        scope_ref="northwind/acme-erp",
        skills_root=prompts_root,
        offline=True,
        adapter_id="fake_recorded",
        endpoint=str(endpoint),
    )


def _reply(body: str = "The endpoint accepts orders.", **overrides: object) -> str:
    payload: dict[str, object] = {
        "body_md": body,
        "cited_facts": [_URI],
        "unknowns": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _counts(store: Any) -> dict[str, int]:
    """Every table a draft would touch. Asserted whole, so nothing lands unseen."""
    return {
        table: int(store.backend.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])  # noqa: S608
        for table in (
            "knowledge_item",
            "knowledge_revision",
            "provenance",
            "binding",
            "audience_tag",
            "review_item",
            "review_batch",
        )
    }


# -- the positive control, first -------------------------------------------


class TestAGroundedDraftLands:
    def test_a_grounded_draft_is_written_bound_provenanced_and_queued(
        self,
        store: Any,
        scope: Any,
        target: DraftTarget,
        annex: AnnexRecords,
        prompts_root: Path,
        tmp_path: Path,
    ) -> None:
        """The positive control, and it comes first for Build 3's reason.

        *Fails when* the whole path writes nothing -- a prompt that will not
        load, a seam that refuses, a transaction that rolls back. *Matters
        because* without it every discard test below passes over a function that
        returns `None` unconditionally, which is this repository's ninth
        measurement-with-nothing-to-measure. *No other instrument catches it
        because* a drafting run that produces no drafts is a legitimate outcome
        and looks identical to one that is broken.
        """
        runner = _runner(annex, prompts_root, tmp_path, _reply())

        report = run_drafting(
            [target],
            runner=runner,
            store=DraftStoreAdapter(store),
            scope=scope,
            audience="client_ops",
        )

        assert len(report.landed) == 1
        outcome = report.landed[0]
        assert outcome.revision_id is not None
        assert outcome.binding_ids  # bound at drafting, so confirm adds no link
        assert report.review_batch_id is not None

        counts = _counts(store)
        assert counts["knowledge_item"] == 1
        assert counts["knowledge_revision"] == 1
        assert counts["binding"] == 1
        assert counts["audience_tag"] == 1
        assert counts["review_item"] == 1

    def test_the_landed_revision_is_unverified_and_authored(
        self,
        store: Any,
        scope: Any,
        target: DraftTarget,
        annex: AnnexRecords,
        prompts_root: Path,
        tmp_path: Path,
    ) -> None:
        """*Fails when* a draft lands `verified`, or claims `artifact_observed`.
        *Matters because* `verified` would make a model's words count toward
        coverage and serve as canon, and `artifact_observed` would claim they
        were read out of the client's repository. *No other instrument catches
        it because* both are single enum values on a row nobody re-reads, and
        the pack would render the result without a banner -- which v6.1 calls
        this build's worst failure."""
        run_drafting(
            [target],
            runner=_runner(annex, prompts_root, tmp_path, _reply()),
            store=DraftStoreAdapter(store),
            scope=scope,
            audience="client_ops",
        )

        row = store.backend.query(
            "SELECT verification, authority_class, created_by_actor_id FROM knowledge_revision"
        )[0]
        assert row["verification"] == "unverified"
        assert row["authority_class"] == "human_confirmed"
        assert row["authority_class"] != "artifact_observed"
        assert row["created_by_actor_id"] == DRAFT_ACTOR

    def test_the_draft_cites_its_prompt_and_every_fact_it_used(
        self,
        store: Any,
        scope: Any,
        target: DraftTarget,
        annex: AnnexRecords,
        prompts_root: Path,
        tmp_path: Path,
    ) -> None:
        """*Fails when* provenance stops naming the prompt or the facts.
        *Matters because* provenance is the only thing that makes a draft
        recognisable later: `build_drafts` reads it to decide what renders under
        an UNVERIFIED banner, and `already_drafted` reads it to decide what a
        second run skips. Losing it would make a draft indistinguishable from a
        harvest candidate. *No other instrument catches it because* the draft
        renders and reads perfectly without a single provenance row."""
        run_drafting(
            [target],
            runner=_runner(annex, prompts_root, tmp_path, _reply(cited_facts=[_URI, _SPAN])),
            store=DraftStoreAdapter(store),
            scope=scope,
            audience="client_ops",
        )

        refs = {
            str(row["source_ref"])
            for row in store.backend.query("SELECT source_ref FROM provenance")
        }
        assert f"{DRAFT_PROVENANCE_PREFIX}prompt:{DRAFT_PROMPT_REF}" in refs
        assert f"{DRAFT_PROVENANCE_PREFIX}fact:{_URI}" in refs
        assert f"{DRAFT_PROVENANCE_PREFIX}fact:{_SPAN}" in refs

    def test_the_unknowns_reach_the_stored_body(
        self,
        store: Any,
        scope: Any,
        target: DraftTarget,
        annex: AnnexRecords,
        prompts_root: Path,
        tmp_path: Path,
    ) -> None:
        """*Fails when* `unknowns` are dropped on the way to the store.
        *Matters because* they are what turns a draft into an elicitation
        prompt: a reviewer who is asked "who is paged when this fails?" can
        answer, where one handed a confident paragraph has to disprove it.
        *No other instrument catches it because* the draft is complete, grounded
        and renders fine without them -- it is just quietly less useful."""
        run_drafting(
            [target],
            runner=_runner(
                annex,
                prompts_root,
                tmp_path,
                _reply(unknowns=["who is paged when this fails?"]),
            ),
            store=DraftStoreAdapter(store),
            scope=scope,
            audience="client_ops",
        )

        body = str(store.backend.query("SELECT body_md FROM knowledge_revision")[0]["body_md"])
        assert "who is paged when this fails?" in body
        assert "Open questions" in body


# -- the four discards, asserted on the store -------------------------------


class TestNothingUngroundedIsEverWritten:
    @pytest.mark.parametrize(
        ("case", "reply"),
        [
            # It cited nothing. Often the model behaving *well* -- the prompt
            # says an empty list is correct when the facts do not support a
            # section -- and it still writes nothing.
            ("no_citations", _reply(cited_facts=[])),
            # The dangerous one: a key shaped exactly like the real ones.
            ("foreign_citation", _reply(cited_facts=[_FABRICATED])),
            # Partly fabricated. Discarded **whole**: filtering the foreign key
            # out would leave prose written against a source the reader can no
            # longer see, attributed to sources that did not say it.
            ("partly_fabricated", _reply(cited_facts=[_URI, _FABRICATED])),
            ("unparseable", "not json at all"),
            ("empty_prose", _reply(body="   ")),
        ],
    )
    def test_a_discarded_draft_leaves_the_store_untouched(
        self,
        case: str,
        reply: str,
        store: Any,
        scope: Any,
        target: DraftTarget,
        annex: AnnexRecords,
        prompts_root: Path,
        tmp_path: Path,
    ) -> None:
        """*Fails when* a discard still writes -- an item without its revision, a
        review item pointing at nothing, a binding to an identity nobody wrote
        about. *Matters because* invariant #7's promise is "never persisted",
        and a `None` return with a committed row satisfies every assertion that
        looks only at the return value. *No other instrument catches it because*
        the run reports zero drafts either way."""
        before = _counts(store)

        report = run_drafting(
            [target],
            runner=_runner(annex, prompts_root, tmp_path, reply),
            store=DraftStoreAdapter(store),
            scope=scope,
            audience="client_ops",
        )

        assert report.landed == ()
        assert report.discarded[0].reason == "discarded"
        assert _counts(store) == before, f"{case} wrote to the store"


class TestGroundingInIsolation:
    @pytest.mark.parametrize(
        "output",
        [
            None,
            42,
            "not json at all",
            '{"body_md": "x"}',
            '{"cited_facts": ["' + _URI + '"]}',
            '{"body_md": "   ", "cited_facts": ["' + _URI + '"]}',
            '{"body_md": "x", "cited_facts": "' + _URI + '"}',
            "[]",
        ],
    )
    def test_every_malformed_output_discards(self, output: object) -> None:
        assert ground(output, {_URI}) is None

    def test_a_fenced_json_reply_is_still_read(self) -> None:
        """CR-52: a frontier model fenced its JSON on this repository's own
        conformance run and burned the seam's single retry on it. Observed
        behaviour, not a hypothetical."""
        fenced = f'```json\n{{"body_md": "Because orders.", "cited_facts": ["{_URI}"]}}\n```'
        result = ground(fenced, {_URI})

        assert result is not None
        assert result.cited_facts == (_URI,)

    def test_stripping_a_fence_cannot_smuggle_a_foreign_citation(self) -> None:
        """The fence tolerance is a parsing convenience and never a grounding
        exemption: every citation is checked afterwards, whatever wrapper it
        arrived in."""
        fenced = f'```json\n{{"body_md": "x", "cited_facts": ["{_FABRICATED}"]}}\n```'
        assert ground(fenced, {_URI}) is None

    def test_a_malformed_unknowns_field_does_not_discard_a_grounded_draft(self) -> None:
        """*Fails when* `unknowns` is treated as a grounding field. *Matters
        because* it carries no claim about the client's system -- throwing away
        a grounded draft over it would trade real content for a formality, while
        every field that *does* carry a claim stays checked."""
        result = ground({"body_md": "x", "cited_facts": [_URI], "unknowns": "oops"}, {_URI})

        assert result is not None
        assert result.unknowns == ()

    def test_an_identity_with_no_facts_is_never_sent_to_a_model(
        self, store: Any, annex: AnnexRecords, prompts_root: Path, tmp_path: Path
    ) -> None:
        """*Fails when* drafting runs on a target with nothing to ground on.
        *Matters because* the only thing a model could do is invent the section
        the store just said it cannot support -- ungrounded by construction, so
        the call is pure cost and pure risk. *No other instrument catches it
        because* `ground` would discard the result anyway: the defect is silent
        spend and a provider round trip on every empty identity.

        The recorded fake is given **no turns**, so a call raises rather than
        quietly succeeding."""
        runner = _runner(annex, prompts_root, tmp_path)

        assert draft_one(runner, DraftTarget("id", _URI, "endpoint"), audience="x") is None


# -- the run: caps, skips and idempotency ----------------------------------


class TestTheRunIsBounded:
    def test_the_cap_stops_the_calls_and_says_so(
        self,
        store: Any,
        scope: Any,
        target: DraftTarget,
        annex: AnnexRecords,
        prompts_root: Path,
        tmp_path: Path,
    ) -> None:
        """*Fails when* `limit` is ignored. *Matters because* a store with four
        hundred uncovered identities would make one flag press four hundred
        model calls -- a bill the operator did not agree to. *No other
        instrument catches it because* the run succeeds; it just costs twenty
        times what it should.

        The fake is scripted with **one** turn, so a second call raises rather
        than passing silently."""
        second = DraftTarget(target.identity_id, _URI + "/x", "endpoint", facts=_FACTS)

        report = run_drafting(
            [target, second],
            runner=_runner(annex, prompts_root, tmp_path, _reply()),
            store=DraftStoreAdapter(store),
            scope=scope,
            audience="client_ops",
            limit=1,
        )

        assert len(report.landed) == 1
        assert [outcome.reason for outcome in report.discarded] == ["capped"]

    def test_an_identity_that_already_has_a_draft_is_skipped(
        self,
        store: Any,
        scope: Any,
        target: DraftTarget,
        annex: AnnexRecords,
        prompts_root: Path,
        tmp_path: Path,
    ) -> None:
        """*Fails when* a second `--draft-missing` re-drafts what the first
        wrote. *Matters because* the cap would then re-bill for the same twenty
        identities forever and never advance through the queue -- and the store
        would fill with parallel drafts of one section. *No other instrument
        catches it because* every individual draft is grounded and valid."""
        adapter = DraftStoreAdapter(store)
        run_drafting(
            [target],
            runner=_runner(annex, prompts_root, tmp_path, _reply()),
            store=adapter,
            scope=scope,
            audience="client_ops",
        )
        before = _counts(store)

        # No turns scripted: a call would raise.
        report = run_drafting(
            [target],
            runner=_runner(annex, prompts_root, tmp_path),
            store=adapter,
            scope=scope,
            audience="client_ops",
            already_drafted=already_drafted(store),
        )

        assert [outcome.reason for outcome in report.outcomes] == ["skipped"]
        assert _counts(store) == before

    def test_the_idempotency_key_covers_the_facts_that_were_sent(self) -> None:
        """*Fails when* the key stops depending on the fact set. *Matters
        because* the seam replays on a repeated key: a store that has learned
        something new about an identity would otherwise replay the old draft
        forever, and the flag would look like it was working. *No other
        instrument catches it because* a replayed run reports a landed draft."""
        one = DraftTarget("id", _URI, "endpoint", facts=_FACTS)
        fewer = DraftTarget("id", _URI, "endpoint", facts=_FACTS[:1])

        assert idempotency_key_for(one) == idempotency_key_for(one)
        assert idempotency_key_for(one) != idempotency_key_for(fewer)


class TestThePromptContract:
    def test_the_inputs_carry_every_fact_with_its_key(self) -> None:
        """The keys are what grounding is checked against, so a fact sent
        without its key is a fact the model cannot legally cite."""
        inputs = build_inputs(DraftTarget("id", _URI, "endpoint", facts=_FACTS), "client_ops")

        assert inputs["fact_count"] == len(_FACTS)
        for fact in _FACTS:
            assert f"[{fact.key}]" in inputs["facts"]
        assert inputs["audience"] == "client_ops"

    def test_the_title_comes_from_the_uri_and_never_from_the_model(self) -> None:
        """*Fails when* a title is taken from model output. *Matters because*
        `select` sorts sections by title, so a model-chosen one would make the
        pack's byte order depend on the model's mood -- and byte-stability is
        the property S4.1 exists to hold."""
        assert title_for(_URI) == "endpoint POST /v1/orders"
        assert title_for("not-a-uri") == "not-a-uri"

    def test_a_draft_with_no_unknowns_renders_no_open_questions_heading(self) -> None:
        assert render_body(Draft(body_md="x", cited_facts=(_URI,))) == "x"


# -- the review round trip: one mechanic, not a third ----------------------


class TestDraftsJoinTheOneQueue:
    def _drafted(
        self,
        store: Any,
        scope: Any,
        target: DraftTarget,
        annex: AnnexRecords,
        prompts_root: Path,
        tmp_path: Path,
    ) -> Any:
        run_drafting(
            [target],
            runner=_runner(annex, prompts_root, tmp_path, _reply()),
            store=DraftStoreAdapter(store),
            scope=scope,
            audience="client_ops",
        )
        from adopt_cli.commands._knowledge_support import identity_views, pending_items

        pending = pending_items(store, scope, identity_views(store, scope))
        assert len(pending) == 1
        return pending[0]

    def test_a_draft_arrives_in_the_same_queue_under_its_own_prefix(
        self,
        store: Any,
        scope: Any,
        target: DraftTarget,
        annex: AnnexRecords,
        prompts_root: Path,
        tmp_path: Path,
    ) -> None:
        """*Fails when* drafts open a batch the queue does not dispatch on.
        *Matters because* v6.1 F5 exists to stop a reviewer having two places to
        look, and a `draft:` batch nothing recognises is a population that
        silently never appears. *No other instrument catches it because* the
        rows are all present and correct -- `adopt review` just does not list
        them."""
        item = self._drafted(store, scope, target, annex, prompts_root, tmp_path)

        assert item.source == "draft"
        assert item.batch_key.startswith("draft:")
        # A draft takes the candidate mechanics: confirming appends a revision
        # rather than creating bindings, because its binding already exists.
        assert item.is_candidate

    def test_confirming_a_draft_appends_a_verified_revision_and_adds_no_binding(
        self,
        store: Any,
        scope: Any,
        target: DraftTarget,
        annex: AnnexRecords,
        prompts_root: Path,
        tmp_path: Path,
    ) -> None:
        """*Fails when* a confirm on a draft creates bindings, or leaves the
        knowledge unverified. *Matters because* the confirm is the **only**
        thing in this product that turns a model's words into canon (v6.1 D11):
        if it does not append a verified revision the draft never counts, and if
        it also binds, one keystroke has meant two things. *No other instrument
        catches it because* the queue records `confirmed` either way."""
        from adopt_knowledge import confirm

        item = self._drafted(store, scope, target, annex, prompts_root, tmp_path)
        bindings_before = _counts(store)["binding"]

        outcome = confirm(
            item,
            reviews=store.governance(),
            bindings=store.bindings(),
            knowledge=store.items(),
            unit=StoreUnitOfWork(store),
        )

        assert outcome.revision_id is not None
        assert outcome.bindings == ()
        assert _counts(store)["binding"] == bindings_before
        head = store.backend.query(
            "SELECT kr.verification AS v FROM knowledge_item ki "
            "JOIN knowledge_revision kr ON kr.id = ki.current_revision_id"
        )[0]
        assert head["v"] == "verified"

    def test_a_confirmed_draft_stops_being_a_draft_for_the_next_pack(
        self,
        store: Any,
        scope: Any,
        target: DraftTarget,
        annex: AnnexRecords,
        prompts_root: Path,
        tmp_path: Path,
    ) -> None:
        """*Fails when* a confirmed draft still renders under an UNVERIFIED
        banner. *Matters because* the demo's fourth line is exactly this -- one
        confirm upgrades the section to confirmed and fresh -- and a pack that
        kept banner-ing confirmed knowledge would teach readers to ignore the
        banner, which is the only thing protecting them from a real draft. *No
        other instrument catches it because* the revision is correct in the
        store; only the pack's *selection* would be wrong.

        Asserted through the composition root's own draft filter rather than
        through the renderer, because that filter is where the mistake would
        live."""
        from adopt_knowledge import confirm

        from adopt_cli.commands._pack_support import build_drafts

        item = self._drafted(store, scope, target, annex, prompts_root, tmp_path)
        system_id = str(scope.system.id)
        environment_id = str(scope.environment.id)

        before = build_drafts(store, system_id=system_id, environment_id=environment_id)
        assert len(before) == 1

        confirm(
            item,
            reviews=store.governance(),
            bindings=store.bindings(),
            knowledge=store.items(),
            unit=StoreUnitOfWork(store),
        )

        after = build_drafts(store, system_id=system_id, environment_id=environment_id)
        assert after == ()

    def test_a_harvest_candidate_is_never_mistaken_for_a_draft(
        self, store: Any, scope: Any
    ) -> None:
        """*Fails when* `build_drafts` filters on `verification` alone. *Matters
        because* a harvest candidate is unverified too, and it is review fodder
        mined from commit messages -- putting every unconfirmed commit into a
        client's handover document is the failure this filter exists to prevent.
        *No other instrument catches it because* the pack would render, stamp
        and banner all of it perfectly correctly."""
        from adopt_cli.commands._pack_support import build_drafts

        item_id, revision_id = store.items().record(
            scope=scope,
            kind="rationale",
            title="Reverted the retry loop",
            body_md="Reverting because the retry storm took the queue down.",
            authority_class="artifact_observed",
            verification="unverified",
        )
        store.items().record_provenance(
            revision_id=revision_id, source_type="commit", source_ref="deadbeef"
        )
        store.items().tag_audience(item_id=item_id, audience="client_ops")

        assert (
            build_drafts(
                store,
                system_id=str(scope.system.id),
                environment_id=str(scope.environment.id),
            )
            == ()
        )
