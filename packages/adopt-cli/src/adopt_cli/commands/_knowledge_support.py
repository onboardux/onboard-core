"""Store lookups Build 2's verbs need, assembled in memory.

The same posture as `_map_support`, for the same reason (plan decision D5, now
D7): **every read here goes through `export_records().table_rows`**, the port
`adopt export` already uses, rather than through a new records method. A new
read method is a new `Sqlite*Records` query path, which `adopt-plane`'s
`escape_coverage.py` immediately counts as outstanding -- needing a Postgres
realization and an escape case in a second, closed repository -- and Build 2
would have added a dozen. Filtering a few thousand rows in memory costs nothing
and owes nothing.

The writes are the opposite case and are not here: they go through facades,
which is where the invariants live.
"""

from collections.abc import Sequence
from typing import Protocol

from adopt_knowledge import IdentityView, PendingItem, StoredDocument, derive_suggestions
from pydantic import BaseModel

from adopt_model import (
    Binding,
    Identity,
    IdentityRevision,
    KnowledgeItem,
    KnowledgeRevision,
    Provenance,
    ReviewBatch,
    ReviewItem,
)
from adopt_scope import Scope, ScopeFacade

__all__ = [
    "KnowledgeStoreView",
    "bound_pairs",
    "identity_views",
    "pending_items",
    "presented_revisions",
    "resolve_identity",
    "stored_documents",
]

#: `provenance.source_type` for a document a human wrote, which is what ingest
#: records and therefore what maps an item back to its file.
_INGEST_SOURCE_TYPE = "human"


class _ExportReader(Protocol):
    def table_rows[TModel: BaseModel](
        self, table: str, model_type: type[TModel]
    ) -> Sequence[TModel]: ...


class KnowledgeStoreView(Protocol):
    """The slice of the store handle these helpers read.

    Structural rather than imported: `adopt_cli.store_option` is the only module
    `no-raw-sqlite` exempts, so every other CLI module reaches the store through
    a shape.
    """

    def scope(self) -> ScopeFacade: ...

    def export_records(self) -> _ExportReader: ...


def _rows[TModel: BaseModel](
    handle: KnowledgeStoreView, table: str, model: type[TModel]
) -> list[TModel]:
    return list(handle.export_records().table_rows(table, model))


def _path_of_source_ref(source_ref: str | None) -> str | None:
    """The file half of an `identity_revision.source_ref` (`<path>:<start>-<end>`).

    Rsplit rather than split: a Windows path can carry a drive colon, and a
    span is always last.
    """
    if not source_ref:
        return None
    head, separator, tail = source_ref.rpartition(":")
    if separator and _looks_like_span(tail):
        return head
    return source_ref


def _looks_like_span(candidate: str) -> bool:
    start, separator, end = candidate.partition("-")
    return bool(separator) and start.isdigit() and end.isdigit()


def identity_views(handle: KnowledgeStoreView, scope: Scope) -> list[IdentityView]:
    """Every **active** identity in the environment, with the paths it was seen at.

    Moved and dead identities are excluded: binding a document to a dead
    referent would create coverage for something that no longer exists, and a
    moved one is reached through its alias by the identity that replaced it.
    """
    if scope.environment is None:
        return []
    environment_id = str(scope.environment.id)
    identities = [
        row for row in _rows(handle, "identity", Identity) if row.environment_id == environment_id
    ]
    wanted = {row.id for row in identities}

    paths: dict[str, set[str]] = {}
    for revision in _rows(handle, "identity_revision", IdentityRevision):
        if revision.identity_id not in wanted:
            continue
        path = _path_of_source_ref(revision.source_ref)
        if path:
            paths.setdefault(revision.identity_id, set()).add(path)

    latest_status = _latest_statuses(handle, wanted)
    return [
        IdentityView(
            identity_id=row.id,
            uri=row.uri,
            source_paths=tuple(sorted(paths.get(row.id, set()))),
        )
        for row in identities
        if latest_status.get(row.id, "active") == "active"
    ]


def _latest_statuses(handle: KnowledgeStoreView, wanted: set[str]) -> dict[str, str]:
    """`identity_id -> the status of its newest revision`.

    `identity` carries no head pointer -- the head is derived (contracts §5
    obligation 3) -- and the newest revision by `(created_at, id)` is what the
    revision helpers treat as current. Ties break on the ULID, which is
    monotonic within a millisecond.
    """
    newest: dict[str, tuple[str, str, str]] = {}
    for revision in _rows(handle, "identity_revision", IdentityRevision):
        if revision.identity_id not in wanted:
            continue
        stamp = (revision.created_at.isoformat(), revision.id, revision.status)
        current = newest.get(revision.identity_id)
        if current is None or stamp[:2] > current[:2]:
            newest[revision.identity_id] = stamp
    return {identity_id: stamp[2] for identity_id, stamp in newest.items()}


def stored_documents(handle: KnowledgeStoreView, scope: Scope) -> dict[str, StoredDocument]:
    """`path -> StoredDocument` for everything ingest has already written.

    What makes a second `adopt ingest` over an unchanged tree write nothing.
    The link from an item back to its file is the `provenance` row ingest wrote,
    which is why provenance is recorded on every revision rather than only on
    the first: an item whose provenance was optional would be an item a re-run
    could not recognise, and the re-run would create a duplicate.
    """
    if scope.system is None:
        return {}
    system_id = str(scope.system.id)
    items = {
        row.id: row
        for row in _rows(handle, "knowledge_item", KnowledgeItem)
        if row.system_id == system_id
    }
    revisions = {
        row.id: row
        for row in _rows(handle, "knowledge_revision", KnowledgeRevision)
        if row.item_id in items
    }

    paths: dict[str, str] = {}
    for provenance in _rows(handle, "provenance", Provenance):
        revision = revisions.get(provenance.revision_id)
        if revision is None or provenance.source_type != _INGEST_SOURCE_TYPE:
            continue
        paths.setdefault(revision.item_id, provenance.source_ref)

    stored: dict[str, StoredDocument] = {}
    for item_id, path in paths.items():
        item = items[item_id]
        head = revisions.get(item.current_revision_id or "")
        stored[path] = StoredDocument(
            item_id=item_id,
            path=path,
            head_revision_id=item.current_revision_id,
            digest=head.source_version if head is not None else None,
        )
    return stored


def bound_pairs(handle: KnowledgeStoreView) -> frozenset[tuple[str, str]]:
    """Every `(item_id, identity_id)` that already has a binding.

    Checked before a create, because `idx_binding_pair` is UNIQUE and the facade
    raises `REVISION_CHAIN_FORK` on a second one -- which would turn an
    idempotent re-ingest into a failure on its second run.
    """
    return frozenset((row.item_id, row.identity_id) for row in _rows(handle, "binding", Binding))


def known_review_items(handle: KnowledgeStoreView) -> dict[str, str | None]:
    """`review_item_id -> its resolution`, open items included as `None`.

    Lets a command tell "there is no such item" from "that one was already
    decided". Without it the queue lookup, which only holds open items, reports
    a resolved id as absent -- the operator's id was right, and the message
    would send them looking for a typo that is not there.
    """
    return {row.id: row.resolution for row in _rows(handle, "review_item", ReviewItem)}


def presented_revisions(handle: KnowledgeStoreView) -> frozenset[str]:
    """Every revision id a `review_item` has already carried.

    What keeps the queue idempotent across re-ingests. Resolved items count as
    much as open ones: a reviewer who rejected a document's suggestions has
    answered for that text, and re-proposing it on the next run would be the
    tool nagging rather than reporting. New text means a new revision, which is
    absent from this set and is therefore presented.
    """
    return frozenset(
        row.proposed_revision_id
        for row in _rows(handle, "review_item", ReviewItem)
        if row.proposed_revision_id is not None
    )


def resolve_identity(handle: KnowledgeStoreView, uri: str) -> Identity | None:
    """An identity by URI, following an alias to the identity that replaced it.

    A moved identity's old URI still resolves forever (Build 0), so `adopt bind`
    against a path that has since moved binds to the live referent rather than
    reporting it absent.
    """
    identities = {row.uri: row for row in _rows(handle, "identity", Identity)}
    found = identities.get(uri)
    if found is None:
        return None
    by_id = {row.id: row for row in identities.values()}
    seen: set[str] = set()
    current = found
    while current.id not in seen:
        seen.add(current.id)
        alias = _alias_target(handle, current.id)
        if alias is None or alias not in by_id:
            return current
        current = by_id[alias]
    return current


def _alias_target(handle: KnowledgeStoreView, identity_id: str) -> str | None:
    """The identity this one was moved to, from its newest revision."""
    newest: tuple[str, str, str | None] | None = None
    for revision in _rows(handle, "identity_revision", IdentityRevision):
        if revision.identity_id != identity_id:
            continue
        stamp = (revision.created_at.isoformat(), revision.id, revision.alias_of_identity_id)
        if newest is None or stamp[:2] > newest[:2]:
            newest = stamp
    return newest[2] if newest is not None else None


def pending_items(
    handle: KnowledgeStoreView,
    scope: Scope,
    identities: Sequence[IdentityView],
) -> list[PendingItem]:
    """Every unresolved queue entry, with its suggestions **re-derived now**.

    Plan decision D3: nothing provisional was stored, so this is where a
    suggestion comes back into existence. A registry that grew since the ingest
    therefore produces current suggestions, and one that shrank produces fewer
    -- in both cases what the reviewer sees is what the matcher would say today.
    """
    if scope.system is None:
        return []
    system_id = str(scope.system.id)
    batches = {
        row.id: row
        for row in _rows(handle, "review_batch", ReviewBatch)
        if row.system_id == system_id
    }
    items = {row.id: row for row in _rows(handle, "knowledge_item", KnowledgeItem)}
    revisions = {row.id: row for row in _rows(handle, "knowledge_revision", KnowledgeRevision)}
    already_bound = bound_pairs(handle)

    pending: list[PendingItem] = []
    for review_item in _rows(handle, "review_item", ReviewItem):
        batch = batches.get(review_item.review_batch_id)
        if batch is None or review_item.resolution is not None:
            continue
        item = items.get(review_item.item_id)
        if item is None:
            continue
        body = _body_of(review_item, item, revisions)
        pending.append(
            PendingItem(
                review_item_id=review_item.id,
                review_batch_id=batch.id,
                batch_key=batch.batch_key,
                item_id=item.id,
                title=item.title,
                suggestions=derive_suggestions(
                    body,
                    identities,
                    already_bound=frozenset(
                        identity_id
                        for bound_item, identity_id in already_bound
                        if bound_item == item.id
                    ),
                ),
            )
        )
    return sorted(pending, key=lambda entry: entry.review_item_id)


def _body_of(
    review_item: ReviewItem,
    item: KnowledgeItem,
    revisions: dict[str, KnowledgeRevision],
) -> str:
    """The text a suggestion was derived from.

    `proposed_revision_id` is preferred over the item's current head, because it
    records **what the reviewer was shown**. If the document changed after the
    batch opened, re-deriving from the new text would silently move the subject
    of the review.
    """
    candidate = revisions.get(review_item.proposed_revision_id or "")
    if candidate is None:
        candidate = revisions.get(item.current_revision_id or "")
    return (candidate.body_md if candidate is not None else None) or ""
