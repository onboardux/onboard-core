"""`adopt ingest --unverified` -- the door for text nobody has vouched for.

*Fails when* an `--unverified` document lands `verified`, counts toward
coverage before a person confirms it, cannot be confirmed through `adopt
review`, or has its binding suggestions asked in the same breath as its truth.

*Matters because* ingest's default -- `verified`, `artifact_observed` -- is
correct only for prose a human already shipped. A coding agent that writes a
Markdown file and ingests it would otherwise turn its own words into canon:
`adopt gaps` stops asking for the knowledge, and `adopt ask` serves the text as
KNOWN. The flag is how an operator (or the agent itself) says so at the door.

*No other instrument catches it because* every row an agent-authored ingest
writes is well formed either way. Coverage recomputes without a disagreement,
the binding is structurally justified, and the gap report looks *better* -- only
an assertion about the verification the run wrote, and about what the queue
does with it, can tell the two stores apart.
"""

from collections.abc import Callable

import pytest
from adopt_knowledge import (
    SOURCE_INGEST,
    SOURCE_INGEST_UNVERIFIED,
    IdentityView,
    IngestReport,
    run_ingest,
)
from adopt_knowledge import confirm as confirm_pending
from adopt_knowledge.documents import Document, body_digest

from adopt_cli.commands._knowledge_support import (
    StoreUnitOfWork,
    bound_pairs,
    pending_items,
    presented_revisions,
    stored_documents,
)
from adopt_coverage import REASON_VERIFICATION_UNVERIFIED, recompute_coverage
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle


def _document(body: str, path: str = "notes/agent-summary.md") -> Document:
    return Document(
        path=path,
        title="Agent summary",
        kind="procedure",
        audiences=("technical",),
        body_md=body,
        digest=body_digest(body),
    )


def _ingest(
    store: SqliteStoreHandle,
    scope: Scope,
    identities: list[IdentityView],
    document: Document,
    *,
    unverified: bool,
) -> IngestReport:
    """One run, wired exactly as `adopt ingest` wires it."""
    return run_ingest(
        [document],
        scope=scope,
        identities=identities,
        stored=stored_documents(store, scope),
        knowledge=store.items(),
        bindings=store.bindings(),
        reviews=store.governance(),
        unit=StoreUnitOfWork(store),
        bound_pairs=bound_pairs(store),
        presented_revisions=presented_revisions(store),
        unverified=unverified,
    )


def _open_items(
    store: SqliteStoreHandle, scope: Scope, identities: list[IdentityView]
) -> dict[str, list[str]]:
    """`population -> [review_item_id]`, as `adopt review` lists the queue."""
    grouped: dict[str, list[str]] = {}
    for item in pending_items(store, scope, identities):
        grouped.setdefault(item.source, []).append(item.review_item_id)
    return grouped


@pytest.mark.unit
class TestUnverifiedIngest:
    def test_an_unverified_document_is_a_gap_until_a_person_confirms_it(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
    ) -> None:
        """**The door, end to end**: unverified, uncovered, queued, then covered on confirm.

        The document names its identity by canonical URI, so the binding is
        structural and lands at once -- the one thing that must *not* follow
        from that is coverage.
        """
        assert s4_scope.system is not None
        add_boundary(system_id=s4_scope.system.id)
        identity = s4_store.identities().observe(
            scope=s4_scope, kind="endpoint", namespace=None, key="POST /v1/refunds"
        )
        views = [IdentityView(identity_id=identity.id, uri=identity.uri, source_paths=())]
        body = f"# Refunds\n\n`{identity.uri}` needs an approver before money moves.\n"

        report = _ingest(s4_store, s4_scope, views, _document(body), unverified=True)

        revision = s4_store.backend.query(
            "SELECT verification, authority_class FROM knowledge_revision"
        )
        assert [(row["verification"], row["authority_class"]) for row in revision] == [
            ("unverified", "human_confirmed")
        ], "agent-assisted text must land unverified, and never claim to be artifact_observed"
        assert report.bindings_created == 1
        assert (
            recompute_coverage(s4_store.coverage_records(), s4_scope.system.id).verdict(identity.id)
            is False
        )
        reasons = (
            recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)
            .identities[0]
            .reasons
        )
        assert REASON_VERIFICATION_UNVERIFIED in reasons

        queue = pending_items(s4_store, s4_scope, views)
        assert [item.source for item in queue] == [SOURCE_INGEST_UNVERIFIED]
        assert queue[0].is_candidate, "confirming must append the verified revision"

        outcome = confirm_pending(
            queue[0],
            reviews=s4_store.governance(),
            bindings=s4_store.bindings(),
            unit=StoreUnitOfWork(s4_store),
            knowledge=s4_store.items(),
            bound_pairs=bound_pairs(s4_store),
        )

        assert outcome.revision_id is not None
        assert (
            recompute_coverage(s4_store.coverage_records(), s4_scope.system.id).verdict(identity.id)
            is True
        )

    def test_its_suggestions_wait_for_the_confirmed_text_and_are_asked_once(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
    ) -> None:
        """One question per queue entry, and neither question asked twice.

        Fails when the verification item and the name-match suggestions land
        together -- one keystroke meaning two things -- or when a re-ingest of
        the unchanged document queues either question again.
        """
        identity = s4_store.identities().observe(
            scope=s4_scope, kind="config_key", namespace=None, key="vault"
        )
        views = [IdentityView(identity_id=identity.id, uri=identity.uri, source_paths=())]
        document = _document("# Keys\n\nRotate the key in the vault, then restart.\n")

        first = _ingest(s4_store, s4_scope, views, document, unverified=True)
        assert _open_items(s4_store, s4_scope, views).keys() == {SOURCE_INGEST_UNVERIFIED}
        assert first.suggestions_deferred == 1, "a held-back suggestion is reported, not lost"

        _ingest(s4_store, s4_scope, views, document, unverified=True)
        assert len(_open_items(s4_store, s4_scope, views)[SOURCE_INGEST_UNVERIFIED]) == 1

        (entry,) = pending_items(s4_store, s4_scope, views)
        confirm_pending(
            entry,
            reviews=s4_store.governance(),
            bindings=s4_store.bindings(),
            unit=StoreUnitOfWork(s4_store),
            knowledge=s4_store.items(),
        )

        _ingest(s4_store, s4_scope, views, document, unverified=True)
        after_confirm = _open_items(s4_store, s4_scope, views)
        assert after_confirm.keys() == {SOURCE_INGEST}, "the confirmed text is now asked about"
        assert len(after_confirm[SOURCE_INGEST]) == 1

        _ingest(s4_store, s4_scope, views, document, unverified=False)
        assert len(_open_items(s4_store, s4_scope, views)[SOURCE_INGEST]) == 1
        revisions = s4_store.backend.query("SELECT COUNT(*) AS n FROM knowledge_revision")
        assert revisions[0]["n"] == 2, "the unverified revision and the confirmed one, no more"
