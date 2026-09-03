"""Critical semantic invariant #5 -- serve-path freshness (v6.1 §4 R6, §6 B3).

*The invariant:* **no code path serves an answer without a freshness
resolution.**

*Fails when* someone adds a fast path that answers from retrieval alone -- the
plausible version being "this item was only just written, it cannot be stale" --
or when the branch stops agreeing with the resolution it was handed.

*Matters because* the failure is silent and lands directly in a client's face: a
confident, cited answer about a system that changed last month. Every other
signal looks healthy. The store is consistent, the citation resolves, the text
is real; only its truth has expired, and nothing in the response says so.

*No other instrument catches it because* a served-without-checking answer is
**indistinguishable** from a correctly-served one whenever the item happens to
be fresh -- which is most of the time, on most stores, in every test anyone
would write by hand. Only a property over generated states, plus an assertion
about the *shape* of the serve function, can see the difference.

**Two halves, and the structural one is the load-bearing half.**

1. *Structural.* `compose` accepts `Resolved` -- a candidate welded to its
   resolution -- and `Resolved.__post_init__` rejects a resolution belonging to
   another item. So "a candidate I did not resolve freshness for" is not a value
   this API can be handed. The test below asserts that shape directly, because a
   future refactor that loosened it back to an optional argument would restore
   the failure while leaving every behavioural test green.
2. *Behavioural.* Over generated mixes of freshness states and verification, the
   branch agrees with the resolutions: KNOWN only over servable states, STALE
   naming a real deciding rule, UNKNOWN only when nothing verified survived.
"""

import collections.abc
import dataclasses
import inspect
import typing

import pytest
from adopt_ask import KNOWN, STALE, UNKNOWN, Candidate, Passage, Resolved, compose
from hypothesis import given
from hypothesis import strategies as st

from adopt_freshness import FreshnessResolution

pytestmark = pytest.mark.property

#: Every value the manifest's `freshness_state` enum declares. Hard-coded here on
#: purpose: if the vocabulary grows, this list stops matching and the test that
#: notices is this one -- which is the moment to decide which side of the branch
#: the new state belongs on, rather than letting it default into KNOWN.
_STATES = ("fresh", "unverified", "stale", "retired", "observation_stale")

#: The two that may serve as KNOWN. See `adopt_ask.branch._SERVES_AS_KNOWN` for
#: why `unverified` is one of them.
_SERVABLE = frozenset({"fresh", "unverified"})


@st.composite
def _resolved_candidates(draw: st.DrawFn) -> tuple[list[Resolved], set[str]]:
    """A retrieval result: N resolved candidates, some verified in the store."""
    count = draw(st.integers(min_value=0, max_value=6))
    resolved: list[Resolved] = []
    verified: set[str] = set()
    for index in range(count):
        state = draw(st.sampled_from(_STATES))
        is_verified = draw(st.booleans())
        revision_id = f"krev_{index}"
        item_id = f"ki_{index}"
        if is_verified:
            verified.add(revision_id)
        resolved.append(
            Resolved(
                candidate=Candidate(
                    passage=Passage(
                        revision_id=revision_id,
                        item_id=item_id,
                        title=f"Item {index}",
                        body_md="Some knowledge.",
                    ),
                    origin="text",
                ),
                freshness=FreshnessResolution(
                    item_id=item_id,
                    state=state,  # type: ignore[arg-type]
                    level="knowledge_revision",
                    deciding_rule=f"rule_{state}",
                ),
            )
        )
    return resolved, verified


@given(_resolved_candidates())
def test_the_branch_always_agrees_with_the_resolutions_it_was_handed(
    case: tuple[list[Resolved], set[str]],
) -> None:
    resolved, verified = case
    answer = compose(resolved, verified, "why?")

    servable = [item for item in resolved if item.candidate.passage.revision_id in verified]
    known_worthy = [item for item in servable if item.freshness.state in _SERVABLE]

    if known_worthy:
        assert answer.branch == KNOWN
        assert {citation.revision_id for citation in answer.citations} == {
            item.candidate.passage.revision_id for item in known_worthy
        }
        assert answer.cause is None
    elif servable:
        assert answer.branch == STALE
        assert answer.cause == servable[0].freshness.deciding_rule
        assert answer.citations
    else:
        assert answer.branch == UNKNOWN
        assert answer.citations == ()

    # Whatever the branch, every citation carries the state and rule of the
    # resolution for its own item -- never a default, never another item's.
    by_item = {item.candidate.passage.item_id: item.freshness for item in resolved}
    for citation in answer.citations:
        resolution = by_item[citation.item_id]
        assert citation.freshness_state == resolution.state
        assert citation.deciding_rule == resolution.deciding_rule


@given(_resolved_candidates())
def test_nothing_unverified_is_ever_cited(case: tuple[list[Resolved], set[str]]) -> None:
    """F6, over generated states: the withheld set and the cited set never overlap."""
    resolved, verified = case
    answer = compose(resolved, verified, "why?")

    cited = {citation.revision_id for citation in answer.citations}
    assert cited <= verified
    assert cited.isdisjoint(answer.withheld)


def test_compose_cannot_be_called_without_resolutions() -> None:
    """The structural half: freshness is not an optional argument of the serve path.

    Asserted against the signature rather than by calling it wrongly, because
    the defect this guards against is a *future* signature -- an added default,
    an overload, an optional mapping -- and only the signature shows that.
    """
    signature = inspect.signature(compose)
    resolved = signature.parameters["resolved"]

    assert resolved.default is inspect.Parameter.empty
    assert typing.get_origin(resolved.annotation) is collections.abc.Sequence
    assert typing.get_args(resolved.annotation) == (Resolved,)
    assert len(signature.parameters) == 3


def test_resolved_pairs_a_candidate_with_a_resolution_and_nothing_else() -> None:
    """`Resolved` may not grow a way to express "no resolution"."""
    fields = {field.name: field for field in dataclasses.fields(Resolved)}

    assert set(fields) == {"candidate", "freshness"}
    for field in fields.values():
        assert field.default is dataclasses.MISSING
        assert field.default_factory is dataclasses.MISSING
    assert typing.get_origin(fields["freshness"].type) is not typing.Union
