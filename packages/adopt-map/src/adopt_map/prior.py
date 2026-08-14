"""What the store already says about this scope -- the input to F4 and F5.

Idempotence and the move rule both need the same thing: **the state a previous
run left behind**, keyed by URI, with each family's head revision resolved. This
module reads it once per run and hands it to both, because two independent
readers of the same rows are two chances to disagree about which revision is
current.

**It reads through `ScopeLookupRecords` -- the whole-table port (OD-1, OD-2).**
Build 0's facades offer `find_identity_by_uri`, `get_item` and
`list_bindings_for_identity`, and nothing that answers *"every identity in this
scope with its head revision"*; `adopt-store` is protected, so Build 1 cannot add
one. Reading four tables once and indexing them in memory costs one pass where
per-identity lookups would cost three round trips each, and it is the shape the
port actually offers. **This is where OD-1's cost shows**, and it is stated
rather than discovered: a store whose identity set does not fit in memory needs
the facade OD-1's reversal trigger describes.

**The identity family's head is derived, not stored.** Build 0's schema gives
`identity` no `current_revision_id` column, so its head is *"the revision no
other revision supersedes"* (Build 0 contracts §5 obligation 3). The other three
families carry a pointer. Both are resolved here so no caller has to know which
is which -- a caller that assumed a stored head everywhere would read `None` for
every identity and conclude that nothing had ever been observed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from adopt_map.ports import ScopeLookupRecords
from adopt_map.sourceversion import SourceVersion, parse_source_version
from adopt_model import (
    Binding,
    BindingRevision,
    Conflict,
    Identity,
    IdentityRevision,
    KnowledgeItem,
    KnowledgeRevision,
)
from adopt_obs import AdoptError

__all__ = ["PriorIdentity", "PriorState"]

#: The `item_kind` Build 1 owns. A binding to an item of any other kind belongs
#: to another build and is not this one's to compare against.
_SURFACE_KIND: Final[str] = "surface"

#: A conflict still awaiting Build 3's resolver. An identity carrying one is not
#: re-examined by the move rule -- see `PriorState.is_open_conflict`.
_OPEN: Final[str] = "open"


@dataclass(frozen=True, slots=True)
class PriorIdentity:
    """One identity as the store holds it, with every head resolved."""

    identity: Identity
    identity_head: IdentityRevision | None
    item: KnowledgeItem | None
    knowledge_head: KnowledgeRevision | None
    binding: Binding | None
    binding_head: BindingRevision | None

    @property
    def source_version(self) -> SourceVersion | None:
        """The composite this referent was last observed at, or `None`.

        **Read from the knowledge revision, not the identity revision, and that
        is a substrate constraint rather than a preference (B1-CR-48, OD-9).**
        `02` §6 puts `source_version` on `identity_revision` too, and Build 1
        does write it there on every revision it appends -- but the *first*
        identity revision is written by Build 0's `IdentityFacade.observe`, whose
        signature has no `source_version` parameter, and `adopt-store` is
        protected. So a newly created identity's only revision carries a null
        composite, and a rule that compared against it would find "no prior
        composite" for every identity on its second run and write a revision for
        every one of them -- idempotence failing on exactly the run F4 tests.
        The knowledge revision's draft is Build 1's own and always carries the
        composite, so it is the one that can be relied on.

        `None` covers three cases that are one case to a caller: no revision, a
        revision predating the composite (S1.1 wrote `source_version=None`
        deliberately), and a stored value this build cannot parse. All three mean
        *"there is nothing here to compare against"*, and all three correctly
        produce a revision on the next run.
        """
        for candidate in (self.knowledge_head, self.identity_head):
            if candidate is not None and candidate.source_version is not None:
                try:
                    return parse_source_version(candidate.source_version)
                except AdoptError:
                    return None
        return None

    @property
    def status(self) -> str | None:
        return None if self.identity_head is None else self.identity_head.status


class PriorState:
    """Every surface identity in one `(system, environment)`, indexed by URI."""

    def __init__(self, by_uri: dict[str, PriorIdentity], open_conflicts: frozenset[str]) -> None:
        self._by_uri = by_uri
        self._open_conflicts = open_conflicts

    def get(self, uri: str) -> PriorIdentity | None:
        return self._by_uri.get(uri)

    def all(self) -> Sequence[PriorIdentity]:
        """Every prior identity, ordered by URI so a caller's output is stable."""
        return [self._by_uri[uri] for uri in sorted(self._by_uri)]

    def is_open_conflict(self, identity_id: str) -> bool:
        """Whether this identity already carries an unresolved `conflict` row.

        The move rule uses it to stay idempotent: `02` §4.3 row 5 writes a
        conflict when a disappeared identity has zero or two candidates, and an
        identity that disappeared stays disappeared, so a rule that did not check
        would write a fresh conflict row on **every** later run and a second run
        would stop being a no-op (B1-CR-46).
        """
        return identity_id in self._open_conflicts

    @classmethod
    def load(
        cls, lookup: ScopeLookupRecords, *, system_id: str, environment_id: str
    ) -> "PriorState":
        """Read and index the prior state for one `(system, environment)`.

        Args:
            lookup: The whole-table read port.
            system_id: Scope. **Both are required** -- `02` §4.3 scopes move
                matching to one `(system, environment)` pair, and PRD F5.4 makes
                a referent appearing in staging and vanishing from production
                explicitly not a move.
            environment_id: Scope.

        Returns:
            The indexed prior state.
        """
        identities = [
            row
            for row in lookup.table_rows("identity", Identity)
            if row.system_id == system_id and row.environment_id == environment_id
        ]
        in_scope = {row.id for row in identities}

        identity_heads = _derived_heads(
            [
                row
                for row in lookup.table_rows("identity_revision", IdentityRevision)
                if row.identity_id in in_scope
            ]
        )

        items = {
            row.id: row
            for row in lookup.table_rows("knowledge_item", KnowledgeItem)
            if row.kind == _SURFACE_KIND and row.system_id == system_id
        }
        knowledge_revisions = {
            row.id: row for row in lookup.table_rows("knowledge_revision", KnowledgeRevision)
        }
        binding_revisions = {
            row.id: row for row in lookup.table_rows("binding_revision", BindingRevision)
        }

        bindings: dict[str, Binding] = {}
        for row in lookup.table_rows("binding", Binding):
            if row.identity_id not in in_scope or row.item_id not in items:
                continue
            # One item per identity is Q2's adopted default, so a second binding
            # here is a store anomaly rather than a case to handle. Resolved by
            # id so the choice is at least deterministic; `store doctor` is what
            # reports the anomaly.
            current = bindings.get(row.identity_id)
            if current is None or row.id < current.id:
                bindings[row.identity_id] = row

        by_uri: dict[str, PriorIdentity] = {}
        for identity in identities:
            binding = bindings.get(identity.id)
            item = None if binding is None else items.get(binding.item_id)
            by_uri[identity.uri] = PriorIdentity(
                identity=identity,
                identity_head=identity_heads.get(identity.id),
                item=item,
                knowledge_head=_head_of(item, knowledge_revisions),
                binding=binding,
                binding_head=_head_of(binding, binding_revisions),
            )

        open_conflicts = frozenset(
            row.identity_id
            for row in lookup.table_rows("conflict", Conflict)
            if row.disposition == _OPEN and row.identity_id in in_scope
        )
        return cls(by_uri, open_conflicts)


def _derived_heads(revisions: Sequence[IdentityRevision]) -> dict[str, IdentityRevision]:
    """The identity revision no other revision supersedes, per identity.

    A forked chain -- two unsuperseded revisions for one identity -- resolves to
    the newest by `created_at` then `id`, and reports nothing: reporting a fork is
    `store doctor`'s job, and a reader that raised here would make a forked store
    unreadable rather than diagnosable. That is Build 0's own stance in
    `RevisionRecords.derived_identity_head`, followed rather than re-decided.
    """
    superseded = {row.supersedes_revision_id for row in revisions if row.supersedes_revision_id}
    heads: dict[str, IdentityRevision] = {}
    for row in sorted(revisions, key=lambda item: (item.created_at, item.id)):
        if row.id not in superseded:
            heads[row.identity_id] = row
    return heads


def _head_of[TRevision](
    parent: KnowledgeItem | Binding | None, revisions: dict[str, TRevision]
) -> TRevision | None:
    """Follow a stored `current_revision_id` pointer, which has no foreign key."""
    if parent is None or parent.current_revision_id is None:
        return None
    return revisions.get(parent.current_revision_id)
