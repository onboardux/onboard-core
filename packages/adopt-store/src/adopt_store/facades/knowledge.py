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

from adopt_model import Binding, KnowledgeItem, ProbeDefinition
from adopt_model._enums import ItemKind
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, new_id, truncate_to_millisecond
from adopt_scope import Scope
from adopt_store.facades.records import BindingRecords, KnowledgeRecords, ProbeRecords
from adopt_store.revisions import (
    FAMILIES,
    INITIAL_BINDING_FRESHNESS,
    BindingRevisionDraft,
    KnowledgeRevisionDraft,
    ProbeDefinitionRevisionDraft,
    RevisionWriter,
)

__all__ = ["BindingFacade", "KnowledgeFacade", "ProbeFacade"]


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
