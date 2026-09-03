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
from contextlib import AbstractContextManager
from typing import Any, Protocol, cast

from adopt_knowledge import (
    ChangedBinding,
    IdentityView,
    PendingItem,
    StoredDocument,
    derive_suggestions,
)
from adopt_knowledge.review import CHANGE_POPULATIONS, SOURCE_INGEST, source_of
from pydantic import BaseModel

from adopt_model import (
    Binding,
    BindingRevision,
    ChangeEvent,
    Identity,
    IdentityRevision,
    KnowledgeItem,
    KnowledgeRevision,
    Provenance,
    ReviewBatch,
    ReviewItem,
)
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope, ScopeFacade

__all__ = [
    "KnowledgeStoreView",
    "bound_pairs",
    "changed_bindings",
    "harvested_commits",
    "identity_views",
    "pending_items",
    "presented_revisions",
    "rebind_target",
    "refresh_population",
    "resolve_identity",
    "stored_documents",
]

#: `provenance.source_type` for a document a human wrote, which is what ingest
#: records and therefore what maps an item back to its file.
_INGEST_SOURCE_TYPE = "human"
#: `provenance.source_type` harvest records, and therefore what maps a candidate
#: back to the commit it was mined from -- the key a re-harvest recognises.
_HARVEST_SOURCE_TYPE = "commit"


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

    def changes(self) -> Any: ...


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


class StoreUnitOfWork:
    """Realizes `adopt_knowledge.UnitOfWork` over one open store handle.

    `_draft_support.DraftStoreAdapter`'s pattern and its reason (CR-36): the CLI
    is the composition root, `adopt_knowledge` never imports `adopt_store`, and
    the cast is where the handle's loosely-typed boundary meets the protocol.

    Every facade the callers pass comes from this same handle, which caches them
    over one connection -- so the transaction really does enclose the item, the
    revision, the provenance rows, the audience tags and the bindings. Five
    independent connections would make the boundary decorative.
    """

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def transaction(self) -> AbstractContextManager[None]:
        return cast("AbstractContextManager[None]", self._handle.backend.transaction())


def in_scope(item: KnowledgeItem, scope: Scope) -> bool:
    """Whether `item` belongs to the scope Build 2's reads were asked about.

    **The system alone was the filter until T1.3, and that was wrong in the one
    way that corrupts canon rather than merely hiding it.** `stored_documents`,
    `harvested_commits` and `pending_items` all matched on `system_id` only, so
    an ingest run under `.../orders-api/staging` recognised the **prod** item at
    the same relative path as "already stored" and appended its revision to that
    item's chain. No staging item was ever created, and the prod item's current
    body became the staging document's text -- silently, exiting `0`, with the
    revision chain reading as an ordinary edit.

    The rule is `identity_views`' rule, with one addition the schema forces:

    * The environment must match exactly, as it does for an identity.
    * `knowledge_item.environment_id` is **nullable** (`schema/canonical.yaml`:
      *"an item may span environments"*) while `identity.environment_id` is
      `NOT NULL`, so `identity_views` never had to answer this question. An
      item with no environment spans all of them and is therefore in scope for
      every environment of its system -- excluding it instead would make a
      re-ingest create a duplicate of it on every run, which is the idempotence
      these three readers exist to provide.
    * A scope that resolves no environment falls back to the system, because
      there is no environment to be wrong about. That is today's behaviour for
      exactly the case where today's behaviour was right.
    """
    if str(item.system_id) != str(scope.system.id if scope.system is not None else ""):
        return False
    if scope.environment is None or item.environment_id is None:
        return True
    return str(item.environment_id) == str(scope.environment.id)


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
    items = {
        row.id: row
        for row in _rows(handle, "knowledge_item", KnowledgeItem)
        if in_scope(row, scope)
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


def harvested_commits(handle: KnowledgeStoreView, scope: Scope) -> dict[str, str]:
    """`commit sha -> item_id` for every candidate harvest has already written.

    **This is harvest's idempotence**, and it is deliberately the same mechanism
    ingest uses: the link from an item back to the thing it was mined from is
    the `provenance` row, not a column on the item. One rule, one place, and a
    candidate that lost its provenance would be a candidate a re-harvest creates
    again -- which is why the provenance write sits in the same loop iteration
    as the revision it cites.
    """
    if scope.system is None:
        return {}
    items = {
        row.id for row in _rows(handle, "knowledge_item", KnowledgeItem) if in_scope(row, scope)
    }
    revisions = {
        row.id: row.item_id
        for row in _rows(handle, "knowledge_revision", KnowledgeRevision)
        if row.item_id in items
    }

    known: dict[str, str] = {}
    for provenance in _rows(handle, "provenance", Provenance):
        if provenance.source_type != _HARVEST_SOURCE_TYPE:
            continue
        item_id = revisions.get(provenance.revision_id)
        if item_id is not None:
            known.setdefault(provenance.source_ref, item_id)
    return known


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
    """Every unresolved queue entry, with what each population needs to be read.

    Plan decision D3: nothing provisional was stored, so **suggestions come
    back into existence here**. A registry that grew since the ingest therefore
    produces current suggestions, and one that shrank produces fewer -- in both
    cases what the reviewer sees is what the matcher would say today.

    **Candidates are not given suggestions**, and the asymmetry is deliberate.
    A harvest candidate already carries the bindings its commit's files
    justified, and a name match inside a commit message is weak evidence about
    a question the reviewer was never asked -- offering it beside "is this a
    real decision?" is how one keystroke comes to mean two things. What a
    candidate carries instead is its `provenance`: the sha, and any decision
    record the commit touched.
    """
    if scope.system is None:
        return []
    system_id = str(scope.system.id)
    batches = {
        row.id: row
        for row in _rows(handle, "review_batch", ReviewBatch)
        if row.system_id == system_id
    }
    # Scope-filtered, not read whole: a batch is found by `system_id` and its
    # items were not filtered at all, so a staging reviewer saw prod's queue
    # entries and could confirm a binding into the wrong environment.
    items = {
        row.id: row
        for row in _rows(handle, "knowledge_item", KnowledgeItem)
        if in_scope(row, scope)
    }
    revisions = {row.id: row for row in _rows(handle, "knowledge_revision", KnowledgeRevision)}
    already_bound = bound_pairs(handle)
    evidence = _evidence_by_revision(handle)

    pending: list[PendingItem] = []
    for review_item in _rows(handle, "review_item", ReviewItem):
        batch = batches.get(review_item.review_batch_id)
        if batch is None or review_item.resolution is not None:
            continue
        item = items.get(review_item.item_id)
        if item is None:
            continue
        revision = _revision_of(review_item, item, revisions)
        body = (revision.body_md if revision is not None else None) or ""
        head = revisions.get(item.current_revision_id or "")
        is_ingest = source_of(batch.batch_key) == SOURCE_INGEST
        pending.append(
            PendingItem(
                review_item_id=review_item.id,
                review_batch_id=batch.id,
                batch_key=batch.batch_key,
                item_id=item.id,
                title=item.title,
                # **Derived from the head, never from the reviewed body** --
                # critical invariant #2 (B2-04). A queue entry keys on
                # `(item_id, revision_id)`, so an entry opened for revision 1
                # stays open when a re-ingest appends revision 2. Deriving from
                # revision 1 offered a `refund` suggestion the *current*
                # document no longer contains, and confirming it wrote a binding
                # nothing justified -- a coverage row for a claim the item does
                # not make, and a stale alarm on every later change to that
                # endpoint.
                #
                # `body_md` below still carries what the reviewer was shown, so
                # the *subject* of the review is unmoved (`_revision_of`'s rule
                # stands). Only the proposals track the head, which is the one
                # thing a confirmation turns into a row.
                suggestions=derive_suggestions(
                    (head.body_md if head is not None else None) or body,
                    identities,
                    already_bound=frozenset(
                        identity_id
                        for bound_item, identity_id in already_bound
                        if bound_item == item.id
                    ),
                )
                if is_ingest
                else (),
                body_md=body,
                head_revision_id=item.current_revision_id,
                source_version=head.source_version if head is not None else None,
                evidence=evidence.get(revision.id, ()) if revision is not None else (),
            )
        )
    return sorted(pending, key=lambda entry: entry.review_item_id)


def refresh_population(
    handle: KnowledgeStoreView, scope: Scope, pending: Sequence[PendingItem]
) -> dict[str, Any]:
    """The change population's two halves: causes per queued item, and the rest.

    **Why the rest exists at all.** v6.1 requires that *nothing is silent*: every
    class lands in the review queue. But `review_item.item_id` is a NOT NULL
    foreign key to `knowledge_item`, so a class whose subject nobody has written
    about -- a new endpoint, a cosmetic edit, a deleted referent with no note --
    has no row it could occupy (plan decision D6). Those render here, read from
    `classification`, in the queue surface and outside the actionable list.

    The alternative repairs are both worse: an additive schema column
    re-litigates §8's budget for a value already derivable, and a stub knowledge
    item would fabricate canon to hold a pointer -- inventing a note about an
    endpoint precisely because nobody wrote one.
    """
    if scope.system is None:
        return {"causes": {}, "informational": []}

    open_batches = {
        item.review_batch_id: item.batch_key
        for item in pending
        if source_of(item.batch_key) in CHANGE_POPULATIONS
    }
    batch_keys = set(open_batches.values()) | _unresolved_refresh_batch_keys(handle, scope)
    if not batch_keys:
        return {"causes": {}, "informational": []}

    identities = {row.id: row.uri for row in _rows(handle, "identity", Identity)}
    bound_items = _items_by_identity(handle)
    item_of_review = {item.review_item_id: item.item_id for item in pending}

    causes: dict[str, list[dict[str, str]]] = {}
    informational: list[dict[str, str]] = []
    for batch_key in sorted(batch_keys):
        for row in handle.changes().classifications_in(batch_key):
            entry = {
                "uri": identities.get(row.identity_id, row.identity_id),
                "class": str(row.class_),
                "evidence": row.evidence,
            }
            owners = bound_items.get(row.identity_id, frozenset())
            targets = [
                review_item_id
                for review_item_id, item_id in item_of_review.items()
                if item_id in owners
            ]
            if targets and str(row.class_) != _RENDER_ONLY:
                for review_item_id in targets:
                    causes.setdefault(review_item_id, []).append(entry)
            else:
                informational.append({**entry, "note": _informational_note(str(row.class_))})

    return {"causes": causes, "informational": informational}


#: The informational class. Recorded and rendered like every other -- nothing is
#: silent -- but never queued: a reviewer cannot act on "a comment moved".
_RENDER_ONLY = "BINDING_INTACT_RENDER_ONLY"


def _informational_note(impact_class: str) -> str:
    """Why this change is being shown rather than asked about."""
    if impact_class == _RENDER_ONLY:
        return "cosmetic: the file changed, this referent did not"
    if impact_class == "UNBOUND_NEW":
        return "new referent; no knowledge covers it yet -- it appears in `adopt gaps`"
    return "no load-bearing knowledge is bound to this referent"


def _unresolved_refresh_batch_keys(handle: KnowledgeStoreView, scope: Scope) -> set[str]:
    """Refresh batch keys whose run produced no queue entry at all.

    A run whose every finding was informational opens no batch -- `open_batch`
    refuses an empty one -- so its classifications would be invisible to this
    surface if the keys were read only from `pending`. They are read from the
    events instead, which is the record that the run happened.
    """
    system_id = str(scope.system.id) if scope.system is not None else None
    resolved = {
        row.batch_key
        for row in _rows(handle, "review_batch", ReviewBatch)
        if row.resolution is not None and row.batch_key
    }
    return {
        str(row.batch_key)
        for row in _rows(handle, "change_event", ChangeEvent)
        if row.batch_key
        and row.system_id == system_id
        and source_of(str(row.batch_key)) in CHANGE_POPULATIONS
        and str(row.batch_key) not in resolved
    }


def _items_by_identity(handle: KnowledgeStoreView) -> dict[str, frozenset[str]]:
    """`identity_id -> the items load-bearingly bound to it`.

    Load-bearing only, matching `resolve_freshness`' rule (PRD F8.3) and the
    write path's queue rule: a queue that used a different predicate from the
    freshness it explains would show an item as needing review while
    `adopt ask` still served it as fresh.
    """
    grouped: dict[str, set[str]] = {}
    for binding in _rows(handle, "binding", Binding):
        if binding.is_load_bearing:
            grouped.setdefault(binding.identity_id, set()).add(binding.item_id)
    return {identity_id: frozenset(items) for identity_id, items in grouped.items()}


def _evidence_by_revision(
    handle: KnowledgeStoreView,
) -> dict[str, tuple[tuple[str, str], ...]]:
    """`revision_id -> ((source_type, source_ref), ...)`, sorted.

    Sorted because it is rendered: two runs over one store must produce one
    listing, the same rule the export writer applies to rows.
    """
    collected: dict[str, list[tuple[str, str]]] = {}
    for provenance in _rows(handle, "provenance", Provenance):
        collected.setdefault(provenance.revision_id, []).append(
            (provenance.source_type, provenance.source_ref)
        )
    return {revision_id: tuple(sorted(rows)) for revision_id, rows in collected.items()}


def _revision_of(
    review_item: ReviewItem,
    item: KnowledgeItem,
    revisions: dict[str, KnowledgeRevision],
) -> KnowledgeRevision | None:
    """The revision a queue entry is *about*.

    `proposed_revision_id` is preferred over the item's current head, because it
    records **what the reviewer was shown**. If the document changed after the
    batch opened, re-deriving from the new text would silently move the subject
    of the review.

    **This governs the body, and deliberately not the suggestions** (B2-04).
    What a reviewer reads must not change under them; what a confirmation
    *writes* must be justified by the document as it stands, because the binding
    outlives the review and the coverage row it creates is read against the head.
    `pending_items` therefore derives suggestions from the head and takes the
    body from here.
    """
    candidate = revisions.get(review_item.proposed_revision_id or "")
    if candidate is None:
        candidate = revisions.get(item.current_revision_id or "")
    return candidate


#: Binding head statuses a review action may still act on. A link already
#: superseded by an earlier rebind, or retired, is **not** one: superseding it
#: again would append a second `moved` revision saying the same thing, and
#: freshening it would re-affirm a link no longer anchoring anything.
_ACTIONABLE_BINDING_STATUSES = frozenset({"active"})


def changed_bindings(handle: KnowledgeStoreView, item: PendingItem) -> tuple[ChangedBinding, ...]:
    """The (item <-> changed identity) links one queue entry is about.

    **Read from `classification`, not from the queue entry.** `review_item` says
    which item a reviewer must look at and nothing about why -- the why is the
    run's classifications, keyed by identity (plan D6). Joining them here is what
    lets an action supersede exactly the links a change made wrong and leave the
    item's other bindings alone: a note bound to three endpoints, one of which
    moved, keeps the two that did not.

    Ordered by binding id so two invocations agree, for the reason every listing
    in this file is ordered.
    """
    if source_of(item.batch_key) not in CHANGE_POPULATIONS:
        return ()

    uris = {row.id: row.uri for row in _rows(handle, "identity", Identity)}
    classified: dict[str, str] = {}
    for row in handle.changes().classifications_in(item.batch_key):
        classified.setdefault(row.identity_id, str(row.class_))

    statuses = _binding_head_statuses(handle)
    links = [
        ChangedBinding(
            binding_id=binding.id,
            item_id=binding.item_id,
            identity_id=binding.identity_id,
            identity_uri=uris.get(binding.identity_id, binding.identity_id),
            impact_class=classified[binding.identity_id],
            is_load_bearing=binding.is_load_bearing,
        )
        for binding in _rows(handle, "binding", Binding)
        if binding.item_id == item.item_id
        and binding.identity_id in classified
        and statuses.get(binding.id, "active") in _ACTIONABLE_BINDING_STATUSES
    ]
    return tuple(sorted(links, key=lambda link: link.binding_id))


def _binding_head_statuses(handle: KnowledgeStoreView) -> dict[str, str]:
    """`binding_id -> its head revision's status`.

    By the parent's head pointer, which the binding family carries -- unlike
    `identity`, whose head is derived. A binding whose pointer is unset has no
    revision to read a status from and is treated as `active`, the value the
    schema gives a fresh row.
    """
    revisions = {row.id: row for row in _rows(handle, "binding_revision", BindingRevision)}
    statuses: dict[str, str] = {}
    for binding in _rows(handle, "binding", Binding):
        head = revisions.get(binding.current_revision_id or "")
        if head is not None:
            statuses[binding.id] = str(head.status)
    return statuses


def rebind_target(
    handle: KnowledgeStoreView, affected: Sequence[ChangedBinding], to_uri: str | None
) -> tuple[str, str]:
    """`(identity_id, uri)` for a rebind: the given `--to`, or the recorded alias.

    **The default only exists where the store already knows the answer.** A
    MOVED classification carries a successor -- `IdentityFacade.move` wrote
    `alias_of_identity_id` on the old identity's `moved` revision -- so asking a
    reviewer to retype a URI the map already resolved is asking them to make a
    typo. Every other class has no successor to infer: an endpoint that was
    deleted, or whose parameters changed, does not name what replaced it, and
    guessing one would be the tool inventing a link a human never approved.

    Raises:
        AdoptError: ``BIND_TARGET_NOT_FOUND`` when `--to` names no identity,
            when no successor is recorded and none was given, and when the
            affected referents moved to **different** successors -- the last
            because "rebind to which one?" is a question only the reviewer can
            answer, and picking the first would be a silent choice about where
            a note now belongs.
    """
    if to_uri:
        found = resolve_identity(handle, to_uri)
        if found is None:
            raise AdoptError(
                ErrorCode.BIND_TARGET_NOT_FOUND,
                message=f"no identity matches {to_uri!r}",
                hint="Run `adopt map --report` to list the referents in scope. A rebind "
                "target has to be something the map has actually seen -- binding to a "
                "URI nobody observed would create coverage for a referent that may "
                "not exist.",
            )
        return found.id, found.uri

    successors = {
        target
        for link in affected
        if (target := _alias_target(handle, link.identity_id)) is not None
    }
    uris = {row.id: row.uri for row in _rows(handle, "identity", Identity)}
    if len(successors) == 1:
        identity_id = next(iter(successors))
        return identity_id, uris.get(identity_id, identity_id)

    raise AdoptError(
        ErrorCode.BIND_TARGET_NOT_FOUND,
        message=(
            "the changed referents name several successors, so --to is required"
            if successors
            else "no successor is recorded for this change, so --to is required"
        ),
        hint="Pass `--to <uri>` naming the referent this note now describes. Only a "
        "MOVED referent records where it went; a deleted or semantically changed "
        "one does not, and inferring a target would be the tool inventing a link.",
    )
