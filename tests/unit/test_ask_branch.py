"""The three-way branch and the boundary that gates it.

**F6 at serve time.** *Fails when* an unverified revision is cited as KNOWN.
*Matters because* the whole promise of this build is that a cited answer is
confirmed knowledge, and an unconfirmed draft served as fact is worse than a
refusal -- the FDE has no way to tell. *No other instrument catches it because*
the index already excludes unverified revisions, so this filter only ever fires
when the index is stale, which is precisely the case nothing else covers.

**STALE names its cause.** *Fails when* a stale answer is served without the
deciding rule. *Matters because* v6.1's contract is "the prior answer *and* what
changed"; without the cause it is an unqualified guess with extra steps. *No
other instrument catches it because* the answer text is identical either way.

**The uncited answer is unrepresentable.** *Fails when* a KNOWN or STALE
`Answer` is constructed with no citations. *Matters because* that is exactly the
unqualified guess the build exists to prevent, and it would reach a client
looking like a confident answer. *No other instrument catches it because* a
caller assembling one by hand bypasses `compose` entirely.

**The boundary refuses rather than trims.** *Fails when* an answer is served
outside the declared observability boundary, or when no boundary is declared at
all. *Matters because* egress policy fails closed everywhere else in this
product, and an assistant is the easiest place to leak client content. *No other
instrument catches it because* the answer is well formed and correctly cited --
only its destination is wrong.
"""

import datetime as _dt

import pytest
from adopt_ask import KNOWN, STALE, UNKNOWN, Answer, Candidate, Passage, Resolved, compose, guard
from adopt_ask.answer import sendable_payload

from adopt_detect import BoundaryView
from adopt_freshness import FreshnessResolution
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope, ScopeNode

pytestmark = pytest.mark.unit

_NOW = _dt.datetime(2026, 8, 22, 9, 0, tzinfo=_dt.UTC)


def _candidate(revision_id: str, item_id: str, *, title: str = "Refunds") -> Candidate:
    return Candidate(
        passage=Passage(
            revision_id=revision_id,
            item_id=item_id,
            title=title,
            body_md="Refunds need a second approver.",
            identity_uris=("onboard-v1://f/e/s/prod/endpoint/-/POST%20%2Fv1%2Forders",),
        ),
        origin="text",
    )


def _resolved(revision_id: str, item_id: str, *, state: str, rule: str = "item_state") -> Resolved:
    return Resolved(
        candidate=_candidate(revision_id, item_id),
        freshness=FreshnessResolution(
            item_id=item_id,
            state=state,  # type: ignore[arg-type]
            level="knowledge_revision",
            deciding_rule=rule,
        ),
    )


def _scope() -> Scope:
    return Scope(
        firm=ScopeNode(id="frm_1", slug="acme"),
        engagement=ScopeNode(id="eng_1", slug="platform"),
        system=ScopeNode(id="sys_1", slug="orders-api"),
        environment=ScopeNode(id="env_1", slug="prod"),
    )


def _boundary(*, permitted: tuple[str, ...] = ("metadata_only",)) -> BoundaryView:
    return BoundaryView(
        boundary_id="ob_1",
        system_id="sys_1",
        environment_id="env_1",
        tier="T2",
        archetype=None,
        knowledge_plane_location="customer",
        control_plane_location="customer",
        permitted_outbound_categories=permitted,
        unavailable_capabilities=(),
        contractual_approval_ref=None,
        declared_at=_NOW,
        decline_recommended=False,
        archetype_floor_violated=False,
    )


# -- the branch --------------------------------------------------------------


def test_verified_and_unstaled_serves_as_known() -> None:
    answer = compose([_resolved("rev_1", "ki_1", state="fresh")], {"rev_1"}, "why?")

    assert answer.branch == KNOWN
    assert [citation.revision_id for citation in answer.citations] == ["rev_1"]
    assert answer.cause is None


def test_initial_unverified_freshness_is_not_staleness() -> None:
    """`INITIAL_ITEM_FRESHNESS` means "no rule fired", not "out of date".

    Reading it as staleness would manufacture false STALE from a creation
    default and, pre-B6, would make KNOWN unreachable for every store.
    """
    answer = compose([_resolved("rev_1", "ki_1", state="unverified")], {"rev_1"}, "why?")

    assert answer.branch == KNOWN


@pytest.mark.parametrize(
    ("state", "rule"),
    [
        ("stale", "load_bearing_identity_moved"),
        ("retired", "item_retired"),
        ("observation_stale", "sensor_health_override"),
    ],
)
def test_staleness_serves_the_prior_answer_and_names_the_cause(state: str, rule: str) -> None:
    answer = compose([_resolved("rev_1", "ki_1", state=state, rule=rule)], {"rev_1"}, "why?")

    assert answer.branch == STALE
    assert answer.cause == rule
    assert answer.citations[0].freshness_state == state
    assert answer.citations[0].body_md  # the prior answer is served, not withheld


def test_an_unverified_revision_never_serves_as_known() -> None:
    """F6 at serve time, checked against the store rather than the index."""
    answer = compose([_resolved("rev_1", "ki_1", state="fresh")], frozenset(), "why?")

    assert answer.branch == UNKNOWN
    assert answer.citations == ()
    assert answer.withheld == ("rev_1",)


def test_fresh_beats_stale_when_both_are_servable() -> None:
    answer = compose(
        [
            _resolved("rev_stale", "ki_1", state="stale", rule="load_bearing_identity_dead"),
            _resolved("rev_fresh", "ki_2", state="fresh"),
        ],
        {"rev_stale", "rev_fresh"},
        "why?",
    )

    assert answer.branch == KNOWN
    assert [citation.revision_id for citation in answer.citations] == ["rev_fresh"]


def test_nothing_retrieved_is_unknown() -> None:
    answer = compose([], frozenset(), "why?")

    assert (answer.branch, answer.citations, answer.withheld) == (UNKNOWN, (), ())


def test_a_resolution_for_another_item_is_refused() -> None:
    """The pairing is the invariant, so a mismatched pair cannot be built."""
    with pytest.raises(ValueError, match="different item"):
        Resolved(
            candidate=_candidate("rev_1", "ki_1"),
            freshness=FreshnessResolution(
                item_id="ki_OTHER",
                state="fresh",
                level="knowledge_revision",
                deciding_rule="item_state",
            ),
        )


@pytest.mark.parametrize(
    ("branch", "citations", "cause"),
    [
        (KNOWN, (), None),
        (STALE, (), "item_state"),
    ],
)
def test_an_uncited_answer_cannot_be_constructed(
    branch: str, citations: tuple[()], cause: str | None
) -> None:
    with pytest.raises(ValueError, match="must cite at least one revision"):
        Answer(question="why?", branch=branch, citations=citations, cause=cause)  # type: ignore[arg-type]


def test_an_unknown_answer_cannot_carry_citations() -> None:
    known = compose([_resolved("rev_1", "ki_1", state="fresh")], {"rev_1"}, "why?")
    with pytest.raises(ValueError, match="cannot carry citations"):
        Answer(question="why?", branch=UNKNOWN, citations=known.citations)


# -- the boundary ------------------------------------------------------------


def test_an_undeclared_boundary_refuses_the_answer() -> None:
    """Fail closed: no boundary is not an unlimited boundary."""
    answer = compose([_resolved("rev_1", "ki_1", state="fresh")], {"rev_1"}, "why?")

    with pytest.raises(AdoptError) as caught:
        guard(answer, None, scope=_scope(), occurred_at=_NOW)

    assert caught.value.code is ErrorCode.ASK_OUTSIDE_BOUNDARY
    assert "no observability boundary" in caught.value.message


def test_a_local_answer_passes_a_metadata_only_boundary() -> None:
    """The content never leaves, so the envelope that would leave carries none."""
    answer = compose([_resolved("rev_1", "ki_1", state="fresh")], {"rev_1"}, "why?")

    guard(answer, _boundary(), scope=_scope(), occurred_at=_NOW)


def test_quoting_content_outside_the_permitted_policy_is_refused() -> None:
    """The boundary is the authority, not the caller's declaration."""
    answer = compose([_resolved("rev_1", "ki_1", state="fresh")], {"rev_1"}, "why?")

    with pytest.raises(AdoptError) as caught:
        guard(
            answer,
            _boundary(),
            scope=_scope(),
            occurred_at=_NOW,
            content_policy="full_content",
        )

    assert caught.value.code is ErrorCode.ASK_OUTSIDE_BOUNDARY
    assert "does not permit" in caught.value.message


def test_a_half_resolved_scope_is_refused() -> None:
    answer = compose([_resolved("rev_1", "ki_1", state="fresh")], {"rev_1"}, "why?")
    partial = Scope(firm=ScopeNode(id="frm_1", slug="acme"))

    with pytest.raises(AdoptError) as caught:
        guard(answer, _boundary(), scope=partial, occurred_at=_NOW)

    assert caught.value.code is ErrorCode.ASK_OUTSIDE_BOUNDARY


def test_the_metadata_only_payload_carries_no_content_field() -> None:
    """The names `find_content_fields` derives are exactly what must be absent."""
    answer = compose([_resolved("rev_1", "ki_1", state="fresh")], {"rev_1"}, "why?")

    payload = sendable_payload(answer, include_content=False)
    rendered = repr(payload)

    assert "question" not in payload
    assert "title" not in rendered
    assert "body_md" not in rendered
    assert payload["citations"][0]["revision_id"] == "rev_1"


def test_the_content_payload_carries_the_answer_when_a_policy_permits_it() -> None:
    """The other side, so the check above cannot pass by emitting nothing."""
    answer = compose([_resolved("rev_1", "ki_1", state="fresh")], {"rev_1"}, "why?")

    payload = sendable_payload(answer, include_content=True)

    assert payload["question"] == "why?"
    assert payload["citations"][0]["body_md"]
