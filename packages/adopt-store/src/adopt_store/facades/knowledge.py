"""`KnowledgeFacade`, `BindingFacade`, `ProbeFacade` -- the three parent creators.

Each creates a parent row and its first revision through the one `RevisionWriter`
and then gets out of the way: content lives in revisions, and every later change
is an `append_revision`. **None of the three exposes an update or a delete on a
`*_revision` table**, which is the append-only guarantee expressed as an absent
method rather than as a rule.

`BindingFacade.create` requires `is_load_bearing` to be passed explicitly. The
column defaults to `1` in the schema so that a writer which forgets errs toward
staleness (PRD F8.3), but a facade that also defaulted it would let the forgetting
happen silently and the default would stop being a safety net and start being the
normal path.
"""

import datetime as _dt
from collections.abc import Sequence

from adopt_model import (
    AudienceTag,
    Binding,
    KnowledgeItem,
    ProbeDefinition,
    Provenance,
    ReviewBatch,
    ReviewItem,
)
from adopt_model._enums import (
    AuthorityClass,
    ItemKind,
    ReviewResolution,
    SourceType,
    Verification,
)
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, new_id, truncate_to_millisecond
from adopt_scope import Scope
from adopt_store.facades.records import (
    BindingRecords,
    KnowledgeRecords,
    ProbeRecords,
    ReviewRecords,
)
from adopt_store.revisions import (
    FAMILIES,
    INITIAL_BINDING_FRESHNESS,
    BindingRevisionDraft,
    KnowledgeRevisionDraft,
    ProbeDefinitionRevisionDraft,
    RevisionWriter,
)

__all__ = ["BindingFacade", "GovernanceFacade", "KnowledgeFacade", "ProbeFacade"]


class KnowledgeFacade:
    """`Store.items()` -- contracts §10.3."""

    def __init__(self, records: KnowledgeRecords, writer: RevisionWriter) -> None:
        self._records = records
        self._writer = writer

    def create(
        self,
        *,
        scope: Scope,
        kind: ItemKind,
        title: str,
        revision: KnowledgeRevisionDraft,
        actor_id: str | None = None,
    ) -> tuple[str, str]:
        """Create an item and its first revision. Returns `(item_id, revision_id)`."""
        return self._writer.create_item(
            scope=scope, kind=kind, title=title, revision=revision, actor_id=actor_id
        )

    def get(self, item_id: str) -> KnowledgeItem | None:
        return self._records.get_item(item_id)

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
        """Create an item from primitives. Returns `(item_id, revision_id)`.

        The door for packages that may not import `adopt_store` -- exactly the
        shape and the reason `IdentityFacade.observe` has one. `adopt_knowledge`
        declares a narrow writer protocol and is handed this facade
        structurally; a draft-typed signature would force it to depend on this
        package, and through it on `sqlite3`, which `no-raw-sqlite` forbids.

        `create` remains for callers that already hold a `KnowledgeRevisionDraft`.
        Both go through the one `RevisionWriter`, so there is still exactly one
        path that writes a `knowledge_revision`.
        """
        return self._writer.create_item(
            scope=scope,
            kind=kind,
            title=title,
            revision=KnowledgeRevisionDraft(
                authority_class=authority_class,
                body_md=body_md,
                verification=verification,
                confidence=confidence,
                source_version=source_version,
            ),
            actor_id=actor_id,
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
        """Append a revision from primitives, enforcing `expected_head_id`.

        Raises:
            AdoptError: ``REVISION_CHAIN_FORK`` when the head moved under the
                caller. Nothing is written.
        """
        return self._writer.append_revision(
            parent_id=item_id,
            draft=KnowledgeRevisionDraft(
                authority_class=authority_class,
                body_md=body_md,
                verification=verification,
                confidence=confidence,
                source_version=source_version,
            ),
            expected_head_id=expected_head_id,
            actor_id=actor_id,
        )

    def record_provenance(
        self,
        *,
        revision_id: str,
        source_type: SourceType,
        source_ref: str,
        observed_at: _dt.datetime | None = None,
    ) -> str:
        """Record where one revision's claim came from. Returns the row id.

        Provenance is written *about* a revision that already exists, so it is
        appended and never amended. `source_type` is the manifest's enum, and
        the distinction it carries is the one v6.1 §6 Build 2 rests on: a mined
        field cites its commit, and anything a human or a model added is
        `human` and can never claim to have been observed in an artifact.
        """
        row_id = new_id("prov")
        self._records.insert_provenance(
            Provenance(
                id=row_id,
                revision_id=revision_id,
                source_type=source_type,
                source_ref=source_ref,
                observed_at=observed_at,
            )
        )
        return row_id

    def tag_audience(self, *, item_id: str, audience: str) -> bool:
        """Tag an item with one audience. `True` when the tag was new.

        Re-tagging is a no-op rather than an error: ingest is idempotent by
        design, and a second run over an unchanged document must not fail on the
        `(item_id, audience)` primary key it wrote the first time.
        """
        if audience in set(self._records.audiences_for_item(item_id)):
            return False
        self._records.insert_audience_tag(AudienceTag(item_id=item_id, audience=audience))
        return True

    def audiences(self, item_id: str) -> tuple[str, ...]:
        return tuple(self._records.audiences_for_item(item_id))


def _batch_resolution(outcomes: Sequence[ReviewResolution | None]) -> ReviewResolution:
    """What a whole batch's outcome was, given its items'.

    A batch closes as `confirmed` only when every item was confirmed and as
    `rejected` only when every item was rejected. **Anything else is
    `corrected`** -- the reviewer neither accepted the batch as offered nor
    threw it away. Summarising a mixed session as `confirmed` would be the
    review-queue equivalent of a green build with a failing test in it.
    """
    distinct = {outcome for outcome in outcomes if outcome is not None}
    if distinct == {"confirmed"}:
        return "confirmed"
    if distinct == {"rejected"}:
        return "rejected"
    return "corrected"


class GovernanceFacade:
    """`review_batch` / `review_item` -- the one review queue (v6.1 §6 F5).

    The queue holds **dispositions, never knowledge**. A batch names what a
    human was shown in one sitting; an item names one knowledge row inside it
    and, once resolved, what the human decided. Confirming is what creates a
    binding or appends a verified revision, and that work belongs to the caller
    -- this facade records the decision and nothing else, so a reviewer's
    disposition can never be manufactured by the code that acts on it.
    """

    def __init__(
        self,
        records: ReviewRecords,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._records = records
        self._clock: Clock = clock if clock is not None else SystemClock()

    def _now(self) -> _dt.datetime:
        return truncate_to_millisecond(self._clock.now())

    def open_batch(
        self,
        *,
        system_id: str,
        batch_key: str,
        items: Sequence[tuple[str, str | None]],
        owner_actor_id: str | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        """Open a batch over `(item_id, proposed_revision_id)` pairs.

        Returns:
            `(review_batch_id, review_item_ids)`.

        Raises:
            ValueError: When no items are supplied. An empty batch opens, shows
                a reviewer nothing and can never be resolved. This is a caller
                mistake rather than a runtime condition -- a run that produced
                nothing to review says so and opens no batch -- so it is refused
                the way an unregistered table name is, and carries no error code
                a client could receive.
        """
        if not items:
            raise ValueError(
                "a review batch needs at least one item. A run with nothing to review "
                "reports that plainly rather than queueing an empty session."
            )

        opened = self._now()
        batch_id = new_id("rb")
        item_ids: list[str] = []

        with self._records.transaction():
            self._records.insert_batch(
                ReviewBatch(
                    id=batch_id,
                    system_id=system_id,
                    batch_key=batch_key,
                    item_count=len(items),
                    owner_actor_id=owner_actor_id,
                    opened_at=opened,
                )
            )
            for item_id, proposed_revision_id in items:
                review_item_id = new_id("ri")
                self._records.insert_item(
                    ReviewItem(
                        id=review_item_id,
                        review_batch_id=batch_id,
                        item_id=item_id,
                        proposed_revision_id=proposed_revision_id,
                    )
                )
                item_ids.append(review_item_id)

        return batch_id, tuple(item_ids)

    def get_batch(self, review_batch_id: str) -> ReviewBatch | None:
        return self._records.get_batch(review_batch_id)

    def get_item(self, review_item_id: str) -> ReviewItem | None:
        return self._records.get_item(review_item_id)

    def items_in(self, review_batch_id: str) -> tuple[ReviewItem, ...]:
        return tuple(self._records.items_in_batch(review_batch_id))

    def resolve(self, *, review_item_id: str, resolution: ReviewResolution) -> ReviewItem:
        """Stamp one item's disposition, closing its batch when it was the last.

        Returns:
            The item **as it was before resolution**, which is what a caller
            acting on the decision needs: the proposed revision and the item it
            belongs to.

        Raises:
            AdoptError: ``REVIEW_ITEM_NOT_FOUND`` when the id names nothing, and
                ``REVIEW_ITEM_RESOLVED`` when it is already stamped. A second
                resolution is refused rather than applied, because confirming
                twice would bind twice and rejecting a confirmed item would
                leave a binding whose review says it was rejected.
        """
        item = self._records.get_item(review_item_id)
        if item is None:
            raise AdoptError(
                ErrorCode.REVIEW_ITEM_NOT_FOUND,
                message=f"no review item {review_item_id!r}",
                hint="Run `adopt review` to list the open queue. Ids are per item, not "
                "per knowledge row.",
            )
        if item.resolution is not None:
            raise AdoptError(
                ErrorCode.REVIEW_ITEM_RESOLVED,
                message=f"review item {review_item_id} is already {item.resolution}",
                hint="A disposition is recorded once. Re-reviewing the same subject means "
                "a new item in a new batch, so the queue keeps what was decided and "
                "when.",
            )

        with self._records.transaction():
            self._records.set_item_resolution(review_item_id, resolution)
            outcomes = [
                row.resolution for row in self._records.items_in_batch(item.review_batch_id)
            ]
            if all(outcome is not None for outcome in outcomes):
                self._records.set_batch_resolution(
                    item.review_batch_id, _batch_resolution(outcomes), self._now()
                )

        return item


class BindingFacade:
    """`Store.bindings()` -- contracts §10.3."""

    def __init__(
        self,
        records: BindingRecords,
        writer: RevisionWriter,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._records = records
        self._writer = writer
        self._clock: Clock = clock if clock is not None else SystemClock()

    def _now(self) -> _dt.datetime:
        return truncate_to_millisecond(self._clock.now())

    def create(
        self,
        *,
        item_id: str,
        identity_id: str,
        is_load_bearing: bool,
        revision: BindingRevisionDraft | None = None,
        actor_id: str | None = None,
    ) -> tuple[str, str]:
        """Bind an item to an identity, with its first revision.

        Args:
            item_id: The knowledge item.
            identity_id: The identity it describes.
            is_load_bearing: **Required.** Whether a change to this identity
                stales the item (PRD F8.3).
            revision: Provenance for the first `binding_revision`.
            actor_id: Who caused it.

        Returns:
            `(binding_id, revision_id)`.

        Raises:
            AdoptError: ``REVISION_CHAIN_FORK`` when the pair is already bound --
                `idx_binding_pair` is `UNIQUE`, and a second binding for one pair
                would give the item two chains describing one relationship.
        """
        existing = self._records.find_binding(item_id, identity_id)
        if existing is not None:
            raise AdoptError(
                ErrorCode.REVISION_CHAIN_FORK,
                message=f"{item_id} is already bound to {identity_id} as {existing.id}",
                hint="Append a revision to the existing binding instead. One pair has one "
                "binding, and its history is the chain.",
            )

        created = self._now()
        binding_id = new_id(FAMILIES["bnd"].parent_prefix)

        with self._records.transaction():
            self._records.insert_binding(
                Binding(
                    id=binding_id,
                    item_id=item_id,
                    identity_id=identity_id,
                    current_revision_id=None,
                    is_load_bearing=is_load_bearing,
                    freshness_state=INITIAL_BINDING_FRESHNESS,
                    created_at=created,
                )
            )
            revision_id = self._writer.append_revision(
                parent_id=binding_id,
                draft=revision if revision is not None else BindingRevisionDraft(),
                expected_head_id=None,
                actor_id=actor_id,
            )

        return binding_id, revision_id

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
        """Bind from primitives -- the door for packages that cannot import a draft.

        The same door, for the same reason, as `KnowledgeFacade.record`.

        **`locator_rung` is deliberately not offered here.** Contracts §9 fixes
        that column's meaning as the *semantic locator hierarchy* -- how a
        rendered referent is located, product id through fragile selector -- and
        Build 2's tiers (a written URI, a resolved path, a confirmed name) are
        not points on it. `extractor` already means "what produced this
        observation", so the tier is recorded there and the rung stays NULL
        rather than being given a second, private meaning that Build 4's recipe
        work would later have to unpick.
        """
        return self.create(
            item_id=item_id,
            identity_id=identity_id,
            is_load_bearing=is_load_bearing,
            revision=BindingRevisionDraft(
                extractor=extractor,
                extractor_version=extractor_version,
                confidence=confidence,
            ),
            actor_id=actor_id,
        )

    def get(self, binding_id: str) -> Binding | None:
        return self._records.get_binding(binding_id)

    def for_identity(self, identity_id: str) -> tuple[Binding, ...]:
        return tuple(self._records.list_bindings_for_identity(identity_id))

    def retire(self, *, binding_id: str, reason: str, actor_id: str | None = None) -> str:
        """Append a `retired` revision. The binding stays readable, because
        coverage provenance depends on it (PRD F6.7)."""
        return self._writer.retire(parent_id=binding_id, reason=reason, actor_id=actor_id)


class ProbeFacade:
    """`Store.probes()` -- contracts §10.3.

    Build 0 owns the probe *tables* and the revision family; probe execution,
    manifest validation and approval enforcement are items 8 and S6. This facade
    is therefore deliberately thin: it creates a definition and appends revisions,
    and it judges nothing.
    """

    def __init__(
        self,
        records: ProbeRecords,
        writer: RevisionWriter,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._records = records
        self._writer = writer
        self._clock: Clock = clock if clock is not None else SystemClock()

    def _now(self) -> _dt.datetime:
        return truncate_to_millisecond(self._clock.now())

    def create(
        self,
        *,
        scope: Scope,
        name: str,
        revision: ProbeDefinitionRevisionDraft,
        schedule_cron: str | None = None,
        actor_id: str | None = None,
    ) -> tuple[str, str]:
        """Create a probe definition and its first revision.

        Raises:
            AdoptError: ``SCOPE_VIOLATION`` when the scope lacks a system or an
                environment -- both are `NOT NULL` on the table, because a probe
                runs against one environment of one system or against nothing.
        """
        if scope.system is None or scope.environment is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message="a probe definition needs a system and an environment",
                hint="Resolve the scope to `firm/engagement/system/environment`. A probe "
                "that does not say which environment it runs against is how a "
                "sandbox probe ends up pointed at production.",
            )

        created = self._now()
        probe_id = new_id(FAMILIES["pd"].parent_prefix)

        with self._records.transaction():
            self._records.insert_probe_definition(
                ProbeDefinition(
                    id=probe_id,
                    system_id=scope.system.id,
                    environment_id=scope.environment.id,
                    name=name,
                    current_revision_id=None,
                    schedule_cron=schedule_cron,
                    created_at=created,
                )
            )
            revision_id = self._writer.append_revision(
                parent_id=probe_id, draft=revision, expected_head_id=None, actor_id=actor_id
            )

        return probe_id, revision_id

    def get(self, probe_definition_id: str) -> ProbeDefinition | None:
        return self._records.get_probe_definition(probe_definition_id)

    def retire(self, *, probe_definition_id: str, reason: str, actor_id: str | None = None) -> str:
        """Append a `retired` revision (CR-33)."""
        return self._writer.retire(parent_id=probe_definition_id, reason=reason, actor_id=actor_id)
