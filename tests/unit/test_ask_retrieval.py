"""Retrieval: what the index holds, when it rebuilds, and what it will not answer.

Four defects, each with its own reason for existing.

**F6 at index time.** *Fails when* an unverified revision reaches the FTS table.
*Matters because* the index is the fast path for "what may serve as KNOWN", and
an unverified draft in it is a client reading a guess as confirmed truth. *No
other instrument catches it because* `adopt_ask.branch` filters again against the
store, so a leaky index produces perfectly correct answers right up until the
second filter is the one that breaks -- two guards that fail together look like
one guard that works.

**Stamp-driven rebuild.** *Fails when* the index keeps serving after the store
changed. *Matters because* the answer would cite a revision the store has
superseded, which is exactly the rot the product exists to delete. *No other
instrument catches it because* a stale index answers confidently and quickly;
nothing about the response says it was built from a store that no longer exists.

**Term coverage.** *Fails when* a passage sharing one incidental word with the
question is served as an answer. *Matters because* it makes UNKNOWN unreachable,
and a branch that can always find something is the unqualified guess this build
exists to prevent -- arriving through retrieval rather than generation. *No other
instrument catches it because* every such answer is correctly cited, correctly
fresh and structurally perfect; only relevance is wrong, and nothing else
measures relevance.

**URI precedence.** *Fails when* BM25 outranks a passage the question named
outright. *Matters because* a question quoting a canonical URI has stated its
subject, and a tool that answers about something else does not understand its own
address scheme. *No other instrument catches it because* the answer is still a
real citation from the store.
"""

from collections.abc import Callable, Iterator

import pytest
from adopt_ask import Passage, retrieve
from adopt_ask.retrieve import content_terms, covers_question

from adopt_scope import Scope
from adopt_store.annex.search import SqliteSearchRecords, open_search
from adopt_store.api import SqliteStoreHandle

pytestmark = pytest.mark.unit


@pytest.fixture
def search(s4_store: SqliteStoreHandle) -> Iterator[SqliteSearchRecords]:
    with open_search(s4_store.backend, clock=s4_store.clock) as records:
        yield records


@pytest.fixture
def add_item(s4_store: SqliteStoreHandle, s4_scope: Scope) -> Callable[..., tuple[str, str]]:
    """One knowledge item with one revision, verified unless told otherwise."""

    def _add(*, title: str, body: str, verification: str = "verified") -> tuple[str, str]:
        return s4_store.items().record(
            scope=s4_scope,
            kind="procedure",
            title=title,
            body_md=body,
            authority_class="human_confirmed",
            verification=verification,  # type: ignore[arg-type]
        )

    return _add


def test_unverified_revisions_never_reach_the_index(
    search: SqliteSearchRecords, add_item: Callable[..., tuple[str, str]]
) -> None:
    """F6 by construction: the index holds only what may serve as KNOWN."""
    add_item(
        title="Refund approval", body="Refunds need a second approver.", verification="verified"
    )
    add_item(
        title="Draft refund note",
        body="Refunds might need a second approver, unconfirmed.",
        verification="unverified",
    )

    outcome = search.refresh()

    assert outcome.rebuilt is True
    assert outcome.indexed == 1
    titles = {hit.title for hit in search.search("refunds approver", limit=10)}
    assert titles == {"Refund approval"}


def test_refresh_rebuilds_when_absent_then_reports_current(
    search: SqliteSearchRecords, add_item: Callable[..., tuple[str, str]]
) -> None:
    """First refresh builds; a second over an unchanged store does not rebuild."""
    add_item(title="Deploys", body="Roll the deployment after migrations.")

    first = search.refresh()
    second = search.refresh()

    assert (first.rebuilt, first.reason) == (True, "no index")
    assert (second.rebuilt, second.reason) == (False, "current")
    assert second.indexed == 1


def test_refresh_rebuilds_when_the_store_has_changed(
    search: SqliteSearchRecords, add_item: Callable[..., tuple[str, str]]
) -> None:
    """A new revision moves the stamp, and the stamp is what forces the rebuild."""
    add_item(title="Deploys", body="Roll the deployment after migrations.")
    search.refresh()

    add_item(title="Refunds", body="Refunds need a second approver.")
    outcome = search.refresh()

    assert outcome.rebuilt is True
    assert outcome.reason == "store changed since the index was built"
    assert outcome.indexed == 2


def test_force_rebuilds_an_index_that_is_already_current(
    search: SqliteSearchRecords, add_item: Callable[..., tuple[str, str]]
) -> None:
    """`--reindex` is the operator's override for an index they do not trust."""
    add_item(title="Deploys", body="Roll the deployment after migrations.")
    search.refresh()

    outcome = search.refresh(force=True)

    assert (outcome.rebuilt, outcome.reason) == (True, "forced")


def test_a_question_of_only_function_words_retrieves_nothing(
    search: SqliteSearchRecords, add_item: Callable[..., tuple[str, str]]
) -> None:
    """No content terms, no candidates -- never "everything in the store"."""
    add_item(title="Deploys", body="Roll the deployment after migrations.")
    search.refresh()

    assert search.search("how do I?", limit=10) == ()
    assert content_terms("how do I?") == ()


def test_one_shared_word_is_not_an_answer(
    search: SqliteSearchRecords, add_item: Callable[..., tuple[str, str]]
) -> None:
    """The coverage rule: a coincidence is not a topic.

    The runbook mentions the API once; the question is about rotating a key.
    Sharing that single word must not make it a candidate.
    """
    add_item(
        title="Deploying the orders service",
        body="Build the image, then roll the deployment. The orders API restarts last.",
    )
    search.refresh()

    assert retrieve(search, "how do I rotate the API key?") == ()


def test_two_shared_words_are_a_topic(
    search: SqliteSearchRecords, add_item: Callable[..., tuple[str, str]]
) -> None:
    """The other side of the rule, so it cannot pass by refusing everything."""
    add_item(
        title="Rotating the API key",
        body="Generate a new API key in the console, then roll the deployment.",
    )
    search.refresh()

    candidates = retrieve(search, "how do I rotate the API key?")

    assert [candidate.passage.title for candidate in candidates] == ["Rotating the API key"]
    assert candidates[0].origin == "text"


def test_a_named_uri_outranks_a_better_text_match(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    search: SqliteSearchRecords,
    add_item: Callable[..., tuple[str, str]],
) -> None:
    """URI precedence: the question named its subject, so ranking does not decide."""
    bound_item, _ = add_item(title="Endpoint notes", body="Notes about this endpoint.")
    add_item(
        title="Orders endpoint chatter",
        body="orders endpoint orders endpoint orders endpoint notes about the orders endpoint",
    )
    identity = s4_store.identities().observe(
        scope=s4_scope,
        kind="endpoint",
        namespace=None,
        key="POST /v1/orders",
        extractor="test",
        extractor_version="1",
    )
    s4_store.bindings().bind(item_id=bound_item, identity_id=identity.id, is_load_bearing=True)
    search.refresh()

    candidates = retrieve(search, f"what does {identity.uri} do for orders endpoint notes?")

    assert candidates[0].passage.item_id == bound_item
    assert candidates[0].origin == "uri"


def test_retrieve_cuts_at_the_limit(
    search: SqliteSearchRecords, add_item: Callable[..., tuple[str, str]]
) -> None:
    """`ASK_TOP_K` is a ceiling on candidates, and the caller may lower it."""
    for index in range(5):
        add_item(title=f"Refund policy {index}", body="Refunds need a second approver.")
    search.refresh()

    assert len(retrieve(search, "refunds approver policy", limit=2)) == 2


def test_covers_question_needs_every_term_when_the_question_has_one() -> None:
    """A one-word question cannot demand two, so it demands its one."""
    passage = Passage(revision_id="r", item_id="i", title="Refunds", body_md="About refunds.")

    assert covers_question(passage, ("refunds",)) is True
    assert covers_question(passage, ("payouts",)) is False
    assert covers_question(passage, ()) is False
