"""The composition root for drafting: store rows -> facts, and a `DraftStore`.

Both halves of what `adopt_knowledge.drafting` cannot do for itself. It holds no
dialect (`no-raw-sqlite` names it a source module), so it is handed the facts it
grounds on and the writers it commits through -- and both are assembled here,
where the store may be read.

**Every read goes through `export_records().table_rows`**, the port `adopt
export` already uses, exactly as `_knowledge_support` does and for the reason it
records: a new read method is a new `Sqlite*Records` query path, which
`adopt-plane`'s `escape_coverage.py` immediately counts as outstanding -- needing
a Postgres realization and an escape case in a second, closed repository.
Drafting would have added four. Filtering a few thousand rows in memory costs
nothing and owes nothing.

**What a fact is, and why the key matters more than the text.** Grounding checks
the model's citations against the keys supplied here, so every key has to be
something a human can resolve afterwards: a canonical URI, a `krev_` revision id,
a `path:start-end` span an extractor recorded. A key invented for the prompt's
convenience would make a surviving citation unresolvable and the whole check a
formality -- the draft would pass grounding and the reader would still have
nothing to check it against.

**Nothing here opens a file in the client's repository.** The store already holds
what the mapper observed; re-reading source at draft time would put file contents
on a wire the boundary never agreed to, which v6.1 forbids outright.
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, cast

from adopt_knowledge import DRAFT_PROVENANCE_PREFIX, Fact
from adopt_knowledge.drafting import DraftTarget

from adopt_const import DRAFT_FACT_BODY_MAX_CHARS
from adopt_model import (
    Binding,
    Identity,
    IdentityRevision,
    KnowledgeItem,
    KnowledgeRevision,
    Provenance,
)
from adopt_model._enums import (
    AuthorityClass,
    ItemKind,
    ReviewResolution,
    SourceType,
    Verification,
)
from adopt_scope import Scope

__all__ = [
    "DraftStoreAdapter",
    "already_drafted",
    "draft_revision_ids",
    "target_for",
    "targets_for",
]


def _rows[TModel](handle: Any, table: str, model: type[TModel]) -> list[TModel]:
    return list(handle.export_records().table_rows(table, model))


def draft_revision_ids(handle: Any) -> frozenset[str]:
    """Every revision id a drafting run wrote, read from its `provenance` rows.

    **Provenance is what makes a draft recognisable**, and it is deliberately not
    a column. A harvest candidate is `unverified` too, and it is review fodder
    rather than handover content -- so "unverified" cannot be the test, or every
    unconfirmed commit in the repository would land in the client's document.
    What distinguishes a draft is that a drafting run wrote it, and the row
    recording that is written in the same transaction as the revision.
    """
    return frozenset(
        row.revision_id
        for row in _rows(handle, "provenance", Provenance)
        if row.source_ref.startswith(DRAFT_PROVENANCE_PREFIX)
    )


def already_drafted(handle: Any) -> frozenset[str]:
    """Identity ids whose bound knowledge already has an **unverified draft head**.

    What keeps a second `--draft-missing` from re-billing for work already done.
    The head is what matters rather than any revision in the chain: a draft that
    was confirmed has a `verified` head, its identity is covered, and it is no
    longer a gap at all -- so it never reaches this function. A draft nobody has
    looked at yet has an unverified head and is skipped, which is what advances
    the cap through the queue.
    """
    drafts = draft_revision_ids(handle)
    heads = {
        row.current_revision_id: row.id
        for row in _rows(handle, "knowledge_item", KnowledgeItem)
        if row.current_revision_id is not None
    }
    verified = {
        row.id
        for row in _rows(handle, "knowledge_revision", KnowledgeRevision)
        if row.verification == "verified"
    }
    drafted_items = {
        item_id
        for revision_id, item_id in heads.items()
        if revision_id in drafts and revision_id not in verified
    }
    return frozenset(
        row.identity_id for row in _rows(handle, "binding", Binding) if row.item_id in drafted_items
    )


def targets_for(handle: Any, gaps: Sequence[Any]) -> list[DraftTarget]:
    """A `DraftTarget` per ranked gap, in the order the gaps were ranked.

    Order is carried through untouched because it decides who gets the budget
    when there are more gaps than `DRAFT_MAX_PER_RUN` allows -- and `rank_gaps`
    is deterministic, so two runs over one store draft the same identities.
    """
    facts = _facts_by_identity(handle)
    return [
        DraftTarget(
            identity_id=gap.identity_id,
            uri=gap.uri,
            kind=gap.kind,
            facts=facts.get(gap.identity_id, ()),
        )
        for gap in gaps
    ]


def target_for(handle: Any, identity: Identity) -> DraftTarget:
    """One target for `adopt draft <uri>`, covered or not.

    Deliberately not filtered against the gap list: an operator who names a URI
    has asked for a draft of *that* identity, and refusing because it already has
    confirmed knowledge would make the verb unusable for the case it is best at
    -- a section somebody wants rewritten from the facts.
    """
    facts = _facts_by_identity(handle).get(str(identity.id), ())
    return DraftTarget(
        identity_id=str(identity.id),
        uri=str(identity.uri),
        kind=str(identity.identity_kind),
        facts=facts,
    )


def _facts_by_identity(handle: Any) -> dict[str, tuple[Fact, ...]]:
    """`identity_id -> the facts the store holds about it`, deterministically.

    Three kinds, and the ordering is fixed so two runs build the same prompt --
    which is what makes the seam's idempotency key mean anything.
    """
    identities = {row.id: row for row in _rows(handle, "identity", Identity)}
    collected: dict[str, list[Fact]] = {}

    for identity in identities.values():
        collected[identity.id] = [
            Fact(
                key=str(identity.uri),
                text=f"A {identity.identity_kind} named {identity.local_key!r}"
                + (f" in namespace {identity.namespace!r}" if identity.namespace else "")
                + f", first seen {identity.first_seen.isoformat()}.",
            )
        ]

    for revision in sorted(
        _rows(handle, "identity_revision", IdentityRevision), key=lambda row: row.id
    ):
        if revision.identity_id not in collected or not revision.source_ref:
            continue
        collected[revision.identity_id].append(
            Fact(
                key=str(revision.source_ref),
                text=f"Observed at {revision.source_ref} by extractor "
                f"{revision.extractor or 'unknown'} "
                f"version {revision.extractor_version or 'unknown'}.",
            )
        )

    for identity_id, knowledge in _bound_knowledge(handle).items():
        if identity_id in collected:
            collected[identity_id].extend(knowledge)

    return {identity_id: tuple(facts) for identity_id, facts in collected.items()}


def _bound_knowledge(handle: Any) -> dict[str, list[Fact]]:
    """Knowledge already bound to each identity, keyed by its revision id.

    **Unverified knowledge is included and is labelled as such.** A harvest
    candidate mined from a commit is a real observation about this identity, and
    withholding it would make the model draft from less than the store knows --
    but a draft that leaned on it must be traceable to something a reviewer can
    weigh, so the fact text says what it is. The label costs a few tokens and
    buys the reviewer the one thing they need: knowing which sentence came from
    a confirmed source.
    """
    items = {row.id: row for row in _rows(handle, "knowledge_item", KnowledgeItem)}
    heads = {row.current_revision_id for row in items.values() if row.current_revision_id}
    revisions = {
        row.item_id: row
        for row in _rows(handle, "knowledge_revision", KnowledgeRevision)
        if row.id in heads
    }

    by_identity: dict[str, list[Fact]] = {}
    for binding in sorted(_rows(handle, "binding", Binding), key=lambda row: row.id):
        revision = revisions.get(binding.item_id)
        item = items.get(binding.item_id)
        if revision is None or item is None:
            continue
        body = (revision.body_md or "").strip()
        if len(body) > DRAFT_FACT_BODY_MAX_CHARS:
            body = body[:DRAFT_FACT_BODY_MAX_CHARS] + " [...truncated]"
        status = "confirmed" if revision.verification == "verified" else "not yet confirmed"
        by_identity.setdefault(binding.identity_id, []).append(
            Fact(
                key=str(revision.id),
                text=f"Bound {item.kind} ({status}) titled {item.title!r}: {body}",
            )
        )
    return by_identity


class DraftStoreAdapter:
    """Realizes `adopt_knowledge.DraftStore` over one open store handle.

    Every facade below comes from the same handle, which caches them over one
    connection -- so `transaction()` really does enclose the item, the revision,
    the provenance rows, the binding and the audience tag. That is not
    incidental: `run_drafting` promises that a discarded draft persists nothing
    and a landed one persists everything, and the promise is only true because
    these are not five independent connections.

    The casts are the manifest's `Literal` enums meeting `adopt_knowledge`'s
    protocol. The values themselves are `drafting`'s own constants, so a cast can
    only widen a name this repository generated.
    """

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def transaction(self) -> AbstractContextManager[None]:
        return cast("AbstractContextManager[None]", self._handle.backend.transaction())

    def record(
        self,
        *,
        scope: Scope,
        kind: ItemKind,
        title: str,
        body_md: str,
        authority_class: AuthorityClass,
        verification: Verification | None = None,
        confidence: float | None = None,
        source_version: str | None = None,
        actor_id: str | None = None,
    ) -> tuple[str, str]:
        return cast(
            "tuple[str, str]",
            self._handle.items().record(
                scope=scope,
                kind=kind,
                title=title,
                body_md=body_md,
                authority_class=authority_class,
                verification=verification,
                confidence=confidence,
                source_version=source_version,
                actor_id=actor_id,
            ),
        )

    def append(
        self,
        *,
        item_id: str,
        expected_head_id: str | None,
        body_md: str,
        authority_class: AuthorityClass,
        verification: Verification | None = None,
        confidence: float | None = None,
        source_version: str | None = None,
        actor_id: str | None = None,
    ) -> str:
        return cast(
            "str",
            self._handle.items().append(
                item_id=item_id,
                expected_head_id=expected_head_id,
                body_md=body_md,
                authority_class=authority_class,
                verification=verification,
                confidence=confidence,
                source_version=source_version,
                actor_id=actor_id,
            ),
        )

    def record_provenance(
        self,
        *,
        revision_id: str,
        source_type: SourceType,
        source_ref: str,
        observed_at: Any = None,
    ) -> str:
        return cast(
            "str",
            self._handle.items().record_provenance(
                revision_id=revision_id,
                source_type=source_type,
                source_ref=source_ref,
                observed_at=observed_at,
            ),
        )

    def tag_audience(self, *, item_id: str, audience: str) -> bool:
        return bool(self._handle.items().tag_audience(item_id=item_id, audience=audience))

    def bind(
        self,
        *,
        item_id: str,
        identity_id: str,
        is_load_bearing: bool,
        extractor: str | None = None,
        extractor_version: str | None = None,
        confidence: float | None = None,
        actor_id: str | None = None,
    ) -> tuple[str, str]:
        return cast(
            "tuple[str, str]",
            self._handle.bindings().bind(
                item_id=item_id,
                identity_id=identity_id,
                is_load_bearing=is_load_bearing,
                extractor=extractor,
                extractor_version=extractor_version,
                confidence=confidence,
                actor_id=actor_id,
            ),
        )

    def for_identity(self, identity_id: str) -> tuple[Binding, ...]:
        return cast("tuple[Binding, ...]", self._handle.bindings().for_identity(identity_id))

    def open_batch(
        self,
        *,
        system_id: str,
        batch_key: str,
        items: Sequence[tuple[str, str | None]],
        owner_actor_id: str | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        return cast(
            "tuple[str, tuple[str, ...]]",
            self._handle.governance().open_batch(
                system_id=system_id,
                batch_key=batch_key,
                items=items,
                owner_actor_id=owner_actor_id,
            ),
        )

    def get_item(self, review_item_id: str) -> Any:
        return self._handle.governance().get_item(review_item_id)

    def items_in(self, review_batch_id: str) -> Any:
        return self._handle.governance().items_in(review_batch_id)

    def resolve(self, *, review_item_id: str, resolution: ReviewResolution) -> Any:
        return self._handle.governance().resolve(
            review_item_id=review_item_id, resolution=resolution
        )
