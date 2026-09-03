"""One logical write is one transaction, and a retry after a failure completes it.

*Fails when* ingest, harvest, a review action or a change resolution chains
facade calls that each commit. *Matters because* the partial state is
**permanently uncorrectable rather than merely wrong**: idempotence keys on the
provenance row, so a document whose item, revision and provenance committed and
whose audience tag did not is reported `unchanged` by every retry, and the
audience is gone for good -- the pack that selects by audience silently omits it.
The review half is worse: `resolve` refuses an item that is already resolved, so
a confirmation whose binding write failed leaves the queue saying a human
confirmed an action that never occurred, and no retry can fix it. *No other
instrument catches either because* the happy path is unaffected -- every existing
test writes both halves successfully, and the store is perfectly consistent
afterwards.

Found by the independent Build 2 review (B2-02, B2-03) and reproduced here by
planting a failure at one write boundary per unit. `DraftStore` had a
transaction from the start; the reasoning that said harvest and ingest did not
need one -- *"a partially written harvest is a candidate a re-harvest
recreates"* -- is the claim these tests disprove.
"""

from collections.abc import Callable
from typing import Any

import pytest
from adopt_knowledge import IdentityView, run_ingest
from adopt_knowledge.documents import Document, body_digest
from adopt_knowledge.harvest import Candidate, run_harvest
from adopt_knowledge.review import PendingItem, confirm

from adopt_cli.commands._knowledge_support import StoreUnitOfWork
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle


class PlantedFailure(RuntimeError):
    """Deliberate, and its own class so a real bug cannot be mistaken for it."""


def _count(store: SqliteStoreHandle, table: str) -> int:
    rows = store.backend.query(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608
    return int(rows[0]["n"])


def _document(body: str, path: str = "docs/notes.md") -> Document:
    return Document(
        path=path,
        title="Operating notes",
        kind="procedure",
        audiences=("technical",),
        body_md=body,
        digest=body_digest(body),
    )


def _fail_once_in(target: Any, name: str, monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Plant a failure in one method; return the undo.

    The plant is **after** the writes that precede it in the sequence rather than
    at the start, because the defect is the boundary between two committed
    writes -- a method that refused before writing anything would leave a clean
    store whether or not a transaction existed.
    """
    original = getattr(target, name)

    def planted(*args: object, **kwargs: object) -> None:
        raise PlantedFailure(f"planted in {name}")

    monkeypatch.setattr(target, name, planted)

    def undo() -> None:
        monkeypatch.setattr(target, name, original)

    return undo


@pytest.mark.unit
def test_a_failed_audience_tag_leaves_no_half_written_document(
    s4_store: SqliteStoreHandle, s4_scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2-02, reproduced.

    `tag_audience` is the last write in a document's unit and the one the review
    planted: before T1.4 the item, its revision and its provenance had already
    committed when it raised, so the store held a document with no audience that
    no retry would ever complete.
    """
    items = s4_store.items()
    undo = _fail_once_in(items, "tag_audience", monkeypatch)

    with pytest.raises(PlantedFailure):
        run_ingest(
            [_document("# Notes\n\nSomething true.\n")],
            scope=s4_scope,
            identities=[],
            stored={},
            knowledge=items,
            bindings=s4_store.bindings(),
            reviews=s4_store.governance(),
            unit=StoreUnitOfWork(s4_store),
        )

    assert _count(s4_store, "knowledge_item") == 0
    assert _count(s4_store, "knowledge_revision") == 0
    assert _count(s4_store, "provenance") == 0
    undo()


@pytest.mark.unit
def test_the_retry_after_that_failure_writes_the_whole_document(
    s4_store: SqliteStoreHandle, s4_scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half that makes the rollback worth having.

    Before T1.4 this second run reported `unchanged`: `stored_documents` found
    the item by its committed provenance row and the digest matched, so nothing
    was rewritten and the audience stayed missing for ever.
    """
    from adopt_cli.commands._knowledge_support import stored_documents

    items = s4_store.items()
    undo = _fail_once_in(items, "tag_audience", monkeypatch)
    with pytest.raises(PlantedFailure):
        run_ingest(
            [_document("# Notes\n\nSomething true.\n")],
            scope=s4_scope,
            identities=[],
            stored={},
            knowledge=items,
            bindings=s4_store.bindings(),
            reviews=s4_store.governance(),
            unit=StoreUnitOfWork(s4_store),
        )
    undo()

    report = run_ingest(
        [_document("# Notes\n\nSomething true.\n")],
        scope=s4_scope,
        identities=[],
        stored=stored_documents(s4_store, s4_scope),
        knowledge=s4_store.items(),
        bindings=s4_store.bindings(),
        reviews=s4_store.governance(),
        unit=StoreUnitOfWork(s4_store),
    )

    assert report.created == 1, "the retry must run as a first attempt, not report `unchanged`"
    assert _count(s4_store, "audience_tag") == 1


@pytest.mark.unit
def test_a_failed_harvest_binding_leaves_no_candidate_behind(
    s4_store: SqliteStoreHandle, s4_scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same shape for harvest, whose idempotence keys on the `commit` row.

    A candidate whose item and provenance committed and whose audience tag did
    not is a candidate every later `adopt harvest --since` reports as *known*.
    """
    items = s4_store.items()
    undo = _fail_once_in(items, "tag_audience", monkeypatch)

    with pytest.raises(PlantedFailure):
        run_harvest(
            [
                Candidate(
                    sha="a" * 40,
                    title="Use the outbox pattern",
                    body_md="Because the acquirer required it.",
                    authored_at="2026-09-01T00:00:00Z",
                    signals=(),
                    decision_records=(),
                    files=(),
                )
            ],
            scope=s4_scope,
            identities=[],
            known={},
            knowledge=items,
            bindings=s4_store.bindings(),
            reviews=s4_store.governance(),
            unit=StoreUnitOfWork(s4_store),
            key="harvest:test",
        )

    assert _count(s4_store, "knowledge_item") == 0
    assert _count(s4_store, "provenance") == 0
    undo()


def _queued_suggestion(
    store: SqliteStoreHandle, scope: Scope
) -> tuple[PendingItem, list[IdentityView]]:
    """One ingested document, one name-matched identity, one open queue entry."""
    identity = store.identities().observe(
        scope=scope,
        kind="endpoint",
        namespace=None,
        key="refund",
        extractor="test",
        extractor_version="1",
    )
    views = [IdentityView(identity_id=identity.id, uri=identity.uri, source_paths=())]
    report = run_ingest(
        [_document("# Refunds\n\nThe refund step needs a human.\n")],
        scope=scope,
        identities=views,
        stored={},
        knowledge=store.items(),
        bindings=store.bindings(),
        reviews=store.governance(),
        unit=StoreUnitOfWork(store),
    )
    assert report.review_batch_id is not None, "the fixture must actually queue something"

    from adopt_cli.commands._knowledge_support import identity_views, pending_items

    pending = pending_items(store, scope, identity_views(store, scope))
    assert len(pending) == 1
    return pending[0], views


@pytest.mark.unit
def test_a_confirmation_whose_binding_fails_leaves_the_entry_re_confirmable(
    s4_store: SqliteStoreHandle, s4_scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2-03, and the reason it is worse than the ingest half.

    `resolve` refuses an item that is already resolved. Before T1.4 the
    disposition committed before the binding was attempted, so a failed bind
    left the queue saying `confirmed` with no binding to show for it **and** no
    way to try again. The item must come back open.
    """
    item, _ = _queued_suggestion(s4_store, s4_scope)
    bindings = s4_store.bindings()
    undo = _fail_once_in(bindings, "bind", monkeypatch)

    with pytest.raises(PlantedFailure):
        confirm(
            item,
            reviews=s4_store.governance(),
            bindings=bindings,
            unit=StoreUnitOfWork(s4_store),
        )
    undo()

    rows = s4_store.backend.query("SELECT resolution FROM review_item")
    assert [row["resolution"] for row in rows] == [None], "the disposition outlived its own action"
    assert _count(s4_store, "binding") == 0


@pytest.mark.unit
def test_the_re_confirmation_then_succeeds(
    s4_store: SqliteStoreHandle, s4_scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control, and the acceptance line: the reviewer's second attempt works."""
    item, _ = _queued_suggestion(s4_store, s4_scope)
    bindings = s4_store.bindings()
    undo = _fail_once_in(bindings, "bind", monkeypatch)
    with pytest.raises(PlantedFailure):
        confirm(
            item,
            reviews=s4_store.governance(),
            bindings=bindings,
            unit=StoreUnitOfWork(s4_store),
        )
    undo()

    outcome = confirm(
        item,
        reviews=s4_store.governance(),
        bindings=s4_store.bindings(),
        unit=StoreUnitOfWork(s4_store),
    )

    assert outcome.bindings, "the retry bound nothing, so the entry was still unusable"
    assert _count(s4_store, "binding") == 1
