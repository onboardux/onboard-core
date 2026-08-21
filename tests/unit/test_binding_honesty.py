"""Critical semantic invariant #2 -- binding honesty (v6.1 §4 R6, §6 B2 H2/D9).

*The invariant:* **no binding row exists that a structural match or a human did
not justify.** Name matches are suggestions; they never bind unconfirmed.

*Fails when* a matcher is promoted to auto-bind without its §9 trigger firing,
when a suggestion is written as a provisional binding row, or when confirming is
wired to something other than a person's decision.

*Matters because* this is the only failure in Build 2 that is both silent and
self-reinforcing. A false binding makes `recompute_coverage` report the identity
as covered, so `adopt gaps` stops asking for the knowledge that is genuinely
missing; and every later change to that identity stales a document which never
described it, until the reviewer learns the queue is noise. Neither symptom
points back at the matcher.

*No other instrument catches it because* the store is perfectly consistent
either way. Every row is well formed, every foreign key resolves, coverage
recomputes without a disagreement, and the counts look better -- a falsely bound
system reports *more* coverage than an honest one. Only an assertion about
**which** rows exist can see it.

The fixture document is the adversarial case H2 names by hand: prose stuffed
with `config`, `user`, `handler` and `model`, every one of which is both an
identity key in the seeded registry and an ordinary English word.
"""

from collections.abc import Callable

import pytest
from adopt_knowledge import IdentityView, StoredDocument, match_document, run_ingest
from adopt_knowledge.documents import Document, body_digest

from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

#: Every noun here is a real identity key in the seeded registry below **and** a
#: word an engineer writes without meaning the referent. That collision is the
#: whole problem: no scoring function separates the two readings from text.
NOISY_PROSE = """
# Operating notes

The service reads its config at boot. A user with the admin role can reset
another user's password, which the handler validates before the model is
written. Our model of the domain treats a user as the aggregate root, so the
handler is deliberately thin and the config is loaded once.

Nothing in this document describes any particular endpoint.
"""


def _identity_view(store: SqliteStoreHandle, scope: Scope, key: str, kind: str) -> IdentityView:
    identity = store.identities().observe(
        scope=scope,
        kind=kind,  # type: ignore[arg-type]
        namespace=None,
        key=key,
        extractor="test",
        extractor_version="1",
    )
    return IdentityView(identity_id=identity.id, uri=identity.uri, source_paths=())


def _document(body: str, path: str = "docs/notes.md") -> Document:
    return Document(
        path=path,
        title="Operating notes",
        kind="procedure",
        audiences=("technical",),
        body_md=body,
        digest=body_digest(body),
    )


@pytest.fixture
def noisy_registry(s4_store: SqliteStoreHandle, s4_scope: Scope) -> list[IdentityView]:
    """Identities whose keys are also ordinary English words."""
    return [
        _identity_view(s4_store, s4_scope, "config", "config_key"),
        _identity_view(s4_store, s4_scope, "user", "db_field"),
        _identity_view(s4_store, s4_scope, "handler", "symbol"),
        _identity_view(s4_store, s4_scope, "model", "symbol"),
    ]


def _binding_rows(store: SqliteStoreHandle) -> list[tuple[str, str]]:
    rows = store.backend.query("SELECT item_id, identity_id FROM binding ORDER BY id")
    return [(str(row["item_id"]), str(row["identity_id"])) for row in rows]


@pytest.mark.unit
class TestBindingHonesty:
    def test_a_document_full_of_identity_words_creates_no_binding(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        noisy_registry: list[IdentityView],
    ) -> None:
        """**The invariant.** Four name matches, zero binding rows."""
        report = run_ingest(
            [_document(NOISY_PROSE)],
            scope=s4_scope,
            identities=noisy_registry,
            stored={},
            knowledge=s4_store.items(),
            bindings=s4_store.bindings(),
            reviews=s4_store.governance(),
        )

        assert _binding_rows(s4_store) == []
        assert report.bindings_created == 0
        # The suggestions must genuinely exist, or the assertion above would pass
        # for the wrong reason -- a matcher that found nothing also creates
        # nothing, and would leave this test green while doing no work at all.
        assert report.suggestions == len(noisy_registry)
        assert report.review_batch_id is not None

    def test_the_registry_really_does_collide_with_the_prose(
        self, noisy_registry: list[IdentityView]
    ) -> None:
        """The positive control for the control.

        Without this, `test_a_document_full_of_identity_words_creates_no_binding`
        could pass because the fixture's keys never appeared in the fixture's
        text -- the shape of failure CR-67 found in a gate whose blind output was
        indistinguishable from its passing one.
        """
        outcome = match_document(NOISY_PROSE, noisy_registry)

        assert outcome.structural == ()
        assert {match.uri.rsplit("/", 1)[-1] for match in outcome.suggested} == {
            "config",
            "user",
            "handler",
            "model",
        }

    def test_confirming_is_what_creates_the_binding(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        noisy_registry: list[IdentityView],
    ) -> None:
        """The other half of the invariant: a human's decision *does* bind.

        An invariant that only ever refuses is satisfied by a build that binds
        nothing at all, which would pass the test above and deliver no product.
        """
        from adopt_knowledge import PendingItem, confirm, derive_suggestions

        run_ingest(
            [_document(NOISY_PROSE)],
            scope=s4_scope,
            identities=noisy_registry,
            stored={},
            knowledge=s4_store.items(),
            bindings=s4_store.bindings(),
            reviews=s4_store.governance(),
        )
        review_item = s4_store.backend.query("SELECT id, item_id FROM review_item")[0]
        suggestions = derive_suggestions(NOISY_PROSE, noisy_registry)

        outcome = confirm(
            PendingItem(
                review_item_id=str(review_item["id"]),
                review_batch_id="unused",
                batch_key="ingest:1-of-1",
                item_id=str(review_item["item_id"]),
                title="Operating notes",
                suggestions=suggestions,
            ),
            reviews=s4_store.governance(),
            bindings=s4_store.bindings(),
            actor_id="alice",
        )

        assert len(outcome.bindings) == len(noisy_registry)
        assert outcome.resolution == "confirmed"
        assert outcome.revision_id is None, (
            "a suggestion's document is already verified -- confirming one binds, "
            "and must not append a revision nobody asked for"
        )
        assert len(_binding_rows(s4_store)) == len(noisy_registry)
        extractors = s4_store.backend.query(
            "SELECT DISTINCT extractor, created_by_actor_id FROM binding_revision"
        )
        # The row records *why* it exists and *who* said so. A confirmed name
        # match is a different value from a structural one because the §9
        # promotion trigger has to count the first population without the second.
        assert [str(row["extractor"]) for row in extractors] == ["ingest-name-confirmed"]
        assert [str(row["created_by_actor_id"]) for row in extractors] == ["alice"]

    def test_rejecting_writes_nothing_but_the_disposition(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        noisy_registry: list[IdentityView],
    ) -> None:
        """A rejected suggestion leaves the store as if it had never been made."""
        from adopt_knowledge import PendingItem, reject

        run_ingest(
            [_document(NOISY_PROSE)],
            scope=s4_scope,
            identities=noisy_registry,
            stored={},
            knowledge=s4_store.items(),
            bindings=s4_store.bindings(),
            reviews=s4_store.governance(),
        )
        review_item = s4_store.backend.query("SELECT id, item_id FROM review_item")[0]

        reject(
            PendingItem(
                review_item_id=str(review_item["id"]),
                review_batch_id="unused",
                batch_key="ingest:1-of-1",
                item_id=str(review_item["item_id"]),
                title="Operating notes",
                suggestions=(),
            ),
            reviews=s4_store.governance(),
        )

        assert _binding_rows(s4_store) == []
        resolutions = s4_store.backend.query("SELECT resolution FROM review_item")
        assert [str(row["resolution"]) for row in resolutions] == ["rejected"]

    def test_an_unverified_item_bound_to_an_identity_is_still_a_gap(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        add_boundary: Callable[..., str],
        add_audience: Callable[..., None],
    ) -> None:
        """Honesty's second half, at the coverage layer (v6.1 F6, plan D5).

        A binding can be perfectly justified while the knowledge behind it is
        still a machine's unreviewed guess. Build 2 writes exactly that shape --
        harvest candidates land `unverified` and bind structurally on commit
        evidence -- so the two rules have to compose: a real binding, and no
        coverage until a person has stood behind the content.

        Fails when an unverified candidate is allowed to satisfy the gap report;
        matters because the whole elicitation queue is then a report about work
        nobody did; no other instrument catches it because the binding is
        genuinely legitimate and every row involved is correct.
        """
        from adopt_coverage import REASON_VERIFICATION_UNVERIFIED, recompute_coverage

        assert s4_scope.system is not None
        identity = s4_store.identities().observe(
            scope=s4_scope, kind="endpoint", namespace=None, key="POST /v1/orders"
        )
        item_id, _ = s4_store.items().record(
            scope=s4_scope,
            kind="rationale",
            title="Why refunds need approval",
            body_md="mined from a commit",
            authority_class="artifact_observed",
            verification="unverified",
        )
        add_audience(item_id=item_id)
        add_boundary(system_id=s4_scope.system.id)
        s4_store.bindings().bind(
            item_id=item_id,
            identity_id=identity.id,
            is_load_bearing=True,
            extractor="harvest-commit",
            extractor_version="1",
        )

        result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

        assert result.verdict(identity.id) is False
        assert REASON_VERIFICATION_UNVERIFIED in result.identities[0].reasons


@pytest.mark.unit
class TestIngestIdempotence:
    def test_a_second_ingest_of_an_unchanged_document_writes_nothing(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        noisy_registry: list[IdentityView],
    ) -> None:
        """Idempotence is a property of the write path, as it is for `adopt map`."""
        document = _document(NOISY_PROSE)
        first = run_ingest(
            [document],
            scope=s4_scope,
            identities=noisy_registry,
            stored={},
            knowledge=s4_store.items(),
            bindings=s4_store.bindings(),
            reviews=s4_store.governance(),
        )
        outcome = first.outcomes[0]
        stored = {
            document.path: StoredDocument(
                item_id=outcome.item_id,
                path=document.path,
                head_revision_id=outcome.revision_id,
                digest=document.digest,
            )
        }
        revisions_before = s4_store.backend.query("SELECT COUNT(*) AS n FROM knowledge_revision")

        second = run_ingest(
            [document],
            scope=s4_scope,
            identities=noisy_registry,
            stored=stored,
            knowledge=s4_store.items(),
            bindings=s4_store.bindings(),
            reviews=s4_store.governance(),
            presented_revisions=frozenset({outcome.revision_id or ""}),
        )

        assert second.created == 0
        assert second.updated == 0
        assert second.unchanged == 1
        revisions_after = s4_store.backend.query("SELECT COUNT(*) AS n FROM knowledge_revision")
        assert revisions_after[0]["n"] == revisions_before[0]["n"]

    def test_a_second_ingest_does_not_queue_the_same_document_again(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        noisy_registry: list[IdentityView],
    ) -> None:
        """The queue is idempotent too, and that is a **separate** guarantee.

        Fails when re-ingesting an unchanged tree opens a second batch holding
        the same items; matters because ingest is run repeatedly and a queue
        that grows a duplicate set on every run is one a reviewer stops opening;
        no other instrument catches it because the knowledge and binding tables
        are untouched -- the write path is perfectly idempotent while the
        surface a human actually looks at doubles.

        **Found by running `adopt ingest` twice on a real repository**, not by a
        fixture: the first run's report and the second's were identical in every
        field a test was asserting on, and the duplication was visible only in
        `adopt review`.
        """
        document = _document(NOISY_PROSE)
        first = run_ingest(
            [document],
            scope=s4_scope,
            identities=noisy_registry,
            stored={},
            knowledge=s4_store.items(),
            bindings=s4_store.bindings(),
            reviews=s4_store.governance(),
        )
        outcome = first.outcomes[0]
        stored = {
            document.path: StoredDocument(
                item_id=outcome.item_id,
                path=document.path,
                head_revision_id=outcome.revision_id,
                digest=document.digest,
            )
        }

        second = run_ingest(
            [document],
            scope=s4_scope,
            identities=noisy_registry,
            stored=stored,
            knowledge=s4_store.items(),
            bindings=s4_store.bindings(),
            reviews=s4_store.governance(),
            presented_revisions=frozenset({outcome.revision_id or ""}),
        )

        assert second.review_batch_id is None
        batches = s4_store.backend.query("SELECT COUNT(*) AS n FROM review_batch")
        assert batches[0]["n"] == 1
        items = s4_store.backend.query("SELECT COUNT(*) AS n FROM review_item")
        assert items[0]["n"] == 1
