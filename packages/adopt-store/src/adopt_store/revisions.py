"""The revision write contract (contracts §5), binding on all four families.

`append_revision` is the only mutation, and it inserts. There is no update method
and no delete path anywhere in this module, in the port beneath it, or on any
facade above it -- and `no-revision-update` keeps it that way in both
repositories.

Three things are worth stating before the code, because each is a decision that
looks arbitrary until you know what it prevents.

**`expected_head_id` is optimistic concurrency, not locking.** Implementation
spec §5 says chain safety comes from the expected head rather than from a lock,
because a lock protects a *store* and the invariant is about a *chain*: two
writers appending to two different items must not block each other, and two
writers appending to one item must not both win. A mismatch raises
`REVISION_CHAIN_FORK` naming both ids and writes nothing.

**The new revision and the advanced head commit together.** A revision without a
head advance is an orphan; a head advance without its revision is a dangling
pointer. Both are `doctor` findings, and one transaction is what makes them
unreachable rather than merely unlikely.

**`identity` derives its head.** Source spec §4 gives it no `current_revision_id`
column, so its head is the revision no other revision supersedes (contracts §5
obligation 3). A pointer column was deliberately *not* added to make the four
families look alike: a derived head and a stored head are equally single-headed,
and the schema is the source of record.
"""

import datetime as _dt
from dataclasses import dataclass
from typing import Final

from adopt_model import (
    BindingRevision,
    IdentityRevision,
    KnowledgeItem,
    KnowledgeRevision,
    ProbeDefinitionRevision,
)
from adopt_model._enums import (
    AuthorityClass,
    BindingStatus,
    DiffMethod,
    FreshnessState,
    IdentityStatus,
    ItemKind,
    LocatorRung,
    ProbeDefinitionStatus,
    SafePath,
    Verification,
)
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, new_id, truncate_to_millisecond
from adopt_scope import Scope
from adopt_store.facades.records import KnowledgeRecords, RevisionRecords

__all__ = [
    "FAMILIES",
    "INITIAL_BINDING_FRESHNESS",
    "INITIAL_ITEM_FRESHNESS",
    "RETIRED_ITEM_FRESHNESS",
    "BindingRevisionDraft",
    "Family",
    "IdentityRevisionDraft",
    "KnowledgeRevisionDraft",
    "ProbeDefinitionRevisionDraft",
    "RevisionDraft",
    "RevisionWriter",
    "UnknownFamilyError",
    "family_of",
]


class UnknownFamilyError(ValueError):
    """A parent id whose prefix names no revision family.

    A `ValueError` rather than an `AdoptError`, following the precedent
    `adopt_obs.ids.UnknownPrefixError` set for the same shape of mistake: this is
    a programming error about an id, not a runtime condition an operator can act
    on, and giving it a registry code would put a second meaning on a code that
    already has one.
    """


@dataclass(frozen=True, slots=True)
class Family:
    """One revision family: its tables, its id prefixes, its terminal status.

    Held as data rather than as four near-identical code paths, because the
    families differ in exactly three places and a branch per family is three
    places for them to drift.
    """

    name: str
    parent_table: str
    revision_table: str
    parent_prefix: str
    revision_prefix: str
    parent_column: str
    terminal_status: str | None
    has_head_pointer: bool


#: Keyed by parent id prefix, because that is what a caller hands `retire` and
#: `append_revision`. `identity` is the one without a head pointer (CR-07 note).
FAMILIES: Final[dict[str, Family]] = {
    "idn": Family(
        name="identity",
        parent_table="identity",
        revision_table="identity_revision",
        parent_prefix="idn",
        revision_prefix="irev",
        parent_column="identity_id",
        terminal_status="dead",
        has_head_pointer=False,
    ),
    "ki": Family(
        name="knowledge",
        parent_table="knowledge_item",
        revision_table="knowledge_revision",
        parent_prefix="ki",
        revision_prefix="krev",
        parent_column="item_id",
        # No status column on the revision: the terminal state is the parent's
        # `freshness_state`, which contracts §5 obligation 6 permits.
        terminal_status=None,
        has_head_pointer=True,
    ),
    "bnd": Family(
        name="binding",
        parent_table="binding",
        revision_table="binding_revision",
        parent_prefix="bnd",
        revision_prefix="brev",
        parent_column="binding_id",
        terminal_status="retired",
        has_head_pointer=True,
    ),
    "pd": Family(
        name="probe_definition",
        parent_table="probe_definition",
        revision_table="probe_definition_revision",
        parent_prefix="pd",
        revision_prefix="pdrev",
        parent_column="probe_definition_id",
        # CR-33 added this column precisely so that `retire` is expressible here.
        terminal_status="retired",
        has_head_pointer=True,
    ),
}

#: A new item claims nothing. `unverified` is the honest starting state: `fresh`
#: would assert a verification nobody performed, and resolution is S4's
#: `resolve_freshness`, not a default written at creation. Typed as the generated
#: vocabulary, so a value the manifest does not declare fails at the type check
#: rather than at the CHECK constraint.
INITIAL_ITEM_FRESHNESS: Final[FreshnessState] = "unverified"
RETIRED_ITEM_FRESHNESS: Final[FreshnessState] = "retired"

#: `binding.freshness_state` is per-binding, not only per-item (PRD F8.2), so it
#: has its own name even though it starts at the same value -- S4's propagation
#: rules move the two independently, and one constant serving both would make
#: that change look like a coincidence.
INITIAL_BINDING_FRESHNESS: Final[FreshnessState] = "unverified"


def family_of(parent_id: str) -> Family:
    """The family a parent id belongs to.

    Raises:
        UnknownFamilyError: the prefix names no revision family.
    """
    prefix = parent_id.split("_", 1)[0]
    family = FAMILIES.get(prefix)
    if family is None:
        raise UnknownFamilyError(
            f"{parent_id!r} does not name a revision-family parent row. A parent id is "
            "prefixed `idn_`, `ki_`, `bnd_` or `pd_` (contracts §1.1); an id from any "
            "other table has no revision chain to append to."
        )
    return family


@dataclass(frozen=True, slots=True)
class KnowledgeRevisionDraft:
    """The caller-supplied half of a `knowledge_revision`.

    A draft carries content and provenance and **never** an id, a parent id, a
    `supersedes_revision_id` or a `created_at`: those four are the writer's, and
    a draft that could carry them is a draft through which a caller could forge a
    chain.
    """

    authority_class: AuthorityClass
    body_md: str | None = None
    recipe_json: str | None = None
    verification: Verification | None = None
    confidence: float | None = None
    snapshot_date: _dt.datetime | None = None
    source_version: str | None = None
    classifier_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class IdentityRevisionDraft:
    status: IdentityStatus = "active"
    extractor: str | None = None
    extractor_version: str | None = None
    #: On an `active` revision this is the per-kind attribute digest (v6.1 §6
    #: Build 1). On the `dead` revision `retire` writes, it is the retirement
    #: reason -- so a reader comparing digests must scope the comparison to
    #: `active` revisions.
    source_version: str | None = None
    #: `<path>:<start>-<end>` -- where the extractor saw it.
    source_ref: str | None = None
    confidence: float | None = None
    alias_of_identity_id: str | None = None


@dataclass(frozen=True, slots=True)
class BindingRevisionDraft:
    status: BindingStatus = "active"
    extractor: str | None = None
    extractor_version: str | None = None
    confidence: float | None = None
    locator_rung: LocatorRung | None = None


@dataclass(frozen=True, slots=True)
class ProbeDefinitionRevisionDraft:
    interaction: str
    safe_path: SafePath
    diff_method: DiffMethod
    capability_manifest: str
    status: ProbeDefinitionStatus = "active"
    artifact_signature: str | None = None
    approved_by: str | None = None
    approved_at: _dt.datetime | None = None
    approval_expires_at: _dt.datetime | None = None


RevisionDraft = (
    KnowledgeRevisionDraft
    | IdentityRevisionDraft
    | BindingRevisionDraft
    | ProbeDefinitionRevisionDraft
)


class RevisionWriter:
    """`create_item`, `append_revision`, `retire` -- contracts §5.

    The only writer of any `*_revision` table in the programme.
    """

    def __init__(
        self,
        revisions: RevisionRecords,
        knowledge: KnowledgeRecords,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._revisions = revisions
        self._knowledge = knowledge
        self._clock: Clock = clock if clock is not None else SystemClock()

    def _now(self) -> _dt.datetime:
        """Truncated to the precision the store keeps, so the row returned is the row stored."""
        return truncate_to_millisecond(self._clock.now())

    # -- creation ---------------------------------------------------------

    def create_item(
        self,
        *,
        scope: Scope,
        kind: ItemKind,
        title: str,
        revision: KnowledgeRevisionDraft,
        actor_id: str | None = None,
    ) -> tuple[str, str]:
        """Create a knowledge item and its first revision, head pointer set.

        Args:
            scope: Resolved to at least `system`. `environment` may be absent:
                `knowledge_item.environment_id` is nullable because an item may
                span environments.
            kind: The `item_kind`.
            title: Human title. Content lives in the revision, never here.
            revision: The first revision's content.
            actor_id: Who caused it, where a human or agent did.

        Returns:
            `(item_id, revision_id)`.

        Raises:
            AdoptError: ``SCOPE_VIOLATION`` when the scope does not resolve to a
                system.
        """
        if scope.engagement is None or scope.system is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message=f"a knowledge item needs a system; the scope resolves {scope.depth} level(s)",
                hint="Resolve at least `firm/engagement/system` first. Every row carries "
                "its scope, and row scope is what the plane's RLS policy is expressed "
                "from -- an item without one could not be isolated.",
            )

        created = self._now()
        item_id = new_id(FAMILIES["ki"].parent_prefix)
        revision_id = new_id(FAMILIES["ki"].revision_prefix)

        with self._revisions.transaction():
            self._knowledge.insert_item(
                KnowledgeItem(
                    id=item_id,
                    firm_id=scope.firm.id,
                    engagement_id=scope.engagement.id,
                    system_id=scope.system.id,
                    environment_id=None if scope.environment is None else scope.environment.id,
                    kind=kind,
                    title=title,
                    current_revision_id=None,
                    freshness_state=INITIAL_ITEM_FRESHNESS,
                    created_at=created,
                    updated_at=created,
                )
            )
            self._revisions.insert_knowledge_revision(
                self._knowledge_revision(
                    revision_id=revision_id,
                    item_id=item_id,
                    draft=revision,
                    supersedes=None,
                    created=created,
                    actor_id=actor_id,
                )
            )
            self._revisions.advance_head(FAMILIES["ki"].parent_table, item_id, revision_id)

        return item_id, revision_id

    # -- append -----------------------------------------------------------

    def append_revision(
        self,
        *,
        parent_id: str,
        draft: RevisionDraft,
        expected_head_id: str | None,
        actor_id: str | None = None,
    ) -> str:
        """Append a revision, advancing the head in the same transaction.

        Args:
            parent_id: The parent row. Its prefix selects the family.
            draft: The family's draft type.
            expected_head_id: The head the caller believes is current, or `None`
                when the caller believes there is none.
            actor_id: Who caused it.

        Returns:
            The new revision id.

        Raises:
            AdoptError: ``REVISION_CHAIN_FORK`` when `expected_head_id` is not
                the current head. Nothing is written.
        """
        family = family_of(parent_id)
        created = self._now()
        revision_id = new_id(family.revision_prefix)

        with self._revisions.transaction():
            current = self._current_head(family, parent_id)
            if current != expected_head_id:
                raise AdoptError(
                    ErrorCode.REVISION_CHAIN_FORK,
                    message=f"expected head {expected_head_id!r} but the current head of "
                    f"{parent_id} is {current!r}",
                    hint="Re-read the parent and retry with the current head id. Nothing "
                    "was written: a chain with two heads cannot answer 'what did it "
                    "say then', and no later repair recovers the answer.",
                )
            self._insert(
                family=family,
                revision_id=revision_id,
                parent_id=parent_id,
                draft=draft,
                supersedes=current,
                created=created,
                actor_id=actor_id,
            )
            if family.has_head_pointer:
                self._revisions.advance_head(family.parent_table, parent_id, revision_id)

        return revision_id

    # -- retirement -------------------------------------------------------

    def retire(self, *, parent_id: str, reason: str, actor_id: str | None = None) -> str:
        """Append a terminal-status revision. Never deletes.

        A retired row stays readable, because coverage provenance depends on it
        (PRD F6.7): the question "why was this covered in March" has no answer in
        a store that deletes.

        Args:
            parent_id: The parent to retire.
            reason: Recorded on the revision where the family has somewhere to
                put it, and always in the caller's audit trail.
            actor_id: Who caused it.

        Returns:
            The terminal revision's id.
        """
        family = family_of(parent_id)
        head = self._current_head(family, parent_id)
        revision_id = self.append_revision(
            parent_id=parent_id,
            draft=self._terminal_draft(family, reason),
            expected_head_id=head,
            actor_id=actor_id,
        )
        if family.name == "knowledge":
            # The knowledge family's terminal state is the parent's denormalized
            # freshness (contracts §5 obligation 6): the revision table has no
            # status column, and inventing one would have been a schema change
            # that F8 then had to reconcile against `freshness_state` anyway.
            self._knowledge.set_item_freshness(parent_id, RETIRED_ITEM_FRESHNESS, self._now())
        return revision_id

    def _terminal_draft(self, family: Family, reason: str) -> RevisionDraft:
        if family.name == "identity":
            return IdentityRevisionDraft(status="dead", source_version=reason)
        if family.name == "binding":
            return BindingRevisionDraft(status="retired")
        if family.name == "probe_definition":
            # A terminal probe revision carries the retirement reason as its
            # interaction and the most restrictive safe path, because both
            # columns are NOT NULL and a retired probe must never read as a
            # runnable one if some later item forgets to check the status.
            return ProbeDefinitionRevisionDraft(
                interaction=reason,
                safe_path="mock",
                diff_method="exact",
                capability_manifest="{}",
                status="retired",
            )
        return KnowledgeRevisionDraft(authority_class="human_confirmed", body_md=reason)

    # -- heads ------------------------------------------------------------

    def current_head(self, parent_id: str) -> str | None:
        """The parent's current head revision, stored or derived."""
        return self._current_head(family_of(parent_id), parent_id)

    def _current_head(self, family: Family, parent_id: str) -> str | None:
        if family.has_head_pointer:
            return self._revisions.head_of(family.parent_table, parent_id)
        return self._revisions.derived_identity_head(parent_id)

    # -- row construction -------------------------------------------------

    def _knowledge_revision(
        self,
        *,
        revision_id: str,
        item_id: str,
        draft: KnowledgeRevisionDraft,
        supersedes: str | None,
        created: _dt.datetime,
        actor_id: str | None,
    ) -> KnowledgeRevision:
        return KnowledgeRevision(
            id=revision_id,
            item_id=item_id,
            body_md=draft.body_md,
            recipe_json=draft.recipe_json,
            authority_class=draft.authority_class,
            verification=draft.verification,
            confidence=draft.confidence,
            snapshot_date=draft.snapshot_date,
            source_version=draft.source_version,
            classifier_version_id=draft.classifier_version_id,
            supersedes_revision_id=supersedes,
            created_at=created,
            created_by_actor_id=actor_id,
        )

    def _insert(
        self,
        *,
        family: Family,
        revision_id: str,
        parent_id: str,
        draft: RevisionDraft,
        supersedes: str | None,
        created: _dt.datetime,
        actor_id: str | None,
    ) -> None:
        if isinstance(draft, KnowledgeRevisionDraft):
            self._revisions.insert_knowledge_revision(
                self._knowledge_revision(
                    revision_id=revision_id,
                    item_id=parent_id,
                    draft=draft,
                    supersedes=supersedes,
                    created=created,
                    actor_id=actor_id,
                )
            )
            return
        if isinstance(draft, IdentityRevisionDraft):
            self._revisions.insert_identity_revision(
                IdentityRevision(
                    id=revision_id,
                    identity_id=parent_id,
                    extractor=draft.extractor,
                    extractor_version=draft.extractor_version,
                    source_version=draft.source_version,
                    source_ref=draft.source_ref,
                    confidence=draft.confidence,
                    alias_of_identity_id=draft.alias_of_identity_id,
                    status=draft.status,
                    supersedes_revision_id=supersedes,
                    created_at=created,
                    created_by_actor_id=actor_id,
                )
            )
            return
        if isinstance(draft, BindingRevisionDraft):
            self._revisions.insert_binding_revision(
                BindingRevision(
                    id=revision_id,
                    binding_id=parent_id,
                    extractor=draft.extractor,
                    extractor_version=draft.extractor_version,
                    confidence=draft.confidence,
                    locator_rung=draft.locator_rung,
                    status=draft.status,
                    supersedes_revision_id=supersedes,
                    created_at=created,
                    created_by_actor_id=actor_id,
                )
            )
            return
        self._revisions.insert_probe_definition_revision(
            ProbeDefinitionRevision(
                id=revision_id,
                probe_definition_id=parent_id,
                interaction=draft.interaction,
                safe_path=draft.safe_path,
                diff_method=draft.diff_method,
                status=draft.status,
                capability_manifest=draft.capability_manifest,
                artifact_signature=draft.artifact_signature,
                approved_by=draft.approved_by,
                approved_at=draft.approved_at,
                approval_expires_at=draft.approval_expires_at,
                supersedes_revision_id=supersedes,
                created_at=created,
            )
        )
