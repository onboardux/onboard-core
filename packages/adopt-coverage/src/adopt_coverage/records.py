"""The storage port `recompute_coverage` reads through.

Declared here rather than imported from `adopt_store`, following the precedent
`adopt_scope.records` set: `no-raw-sqlite` names `adopt_coverage` as a source
module and import-linter follows the chain, so a dependency on `adopt_store`
would reach `sqlite3` transitively and break the contract. A structural protocol
costs one file and keeps this package free of any driver.

**Every method is a read.** The cache write is not on this port -- it lives in
`adopt_coverage.cache`, which is the only place in either repository permitted to
hold the statement (`no-covered-cache-write`).

**The port fetches rows; it never decides.** Each method is one bulk read whose
result is a plain mapping or a sequence of generated models. Pushing any of the
six coverage inputs into SQL would move the authority out of
`recompute_coverage` and into whichever realization ran -- and the property test
that compares the function against an independent reference implementation would
then be comparing two callers of one query.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from adopt_model import Binding, Identity, KnowledgeItem, ObservabilityBoundary

__all__ = ["CoverageRecords"]


class CoverageRecords(Protocol):
    """Row in, decision out. No SQL, connection or cursor crosses this boundary.

    Every method takes the scope the recompute was asked for. `environment_id`
    is optional because `recompute_coverage` is (contracts §6); `None` means
    every environment of the system rather than "the environment that is null",
    and the two readings differ for `knowledge_item`, whose `environment_id` is
    nullable precisely because an item may span environments.
    """

    def identities_in_scope(
        self, *, system_id: str, environment_id: str | None
    ) -> Sequence[Identity]: ...

    def systems_with_identities(self) -> Sequence[str]:
        """Every `system_id` that has at least one identity.

        `store doctor` sweeps coverage across the whole store and has no scope
        argument to work from (implementation spec §4.7: `doctor(store)`). Making
        it ask which systems exist is what stops the sweep silently checking
        nothing when a caller forgets to name one.
        """
        ...

    def head_identity_statuses(
        self, *, system_id: str, environment_id: str | None
    ) -> Mapping[str, str]:
        """`identity_id` -> the status of its head revision.

        `identity` carries no head pointer, so the head is *derived*: the
        revision no other revision supersedes (contracts §5 obligation 3). An
        identity with no revision at all is absent from the mapping rather than
        present with a placeholder -- "no revision" and "a revision saying
        nothing" are different facts and the caller treats them differently.
        """
        ...

    def bindings_in_scope(
        self, *, system_id: str, environment_id: str | None
    ) -> Sequence[Binding]: ...

    def head_binding_statuses(
        self, *, system_id: str, environment_id: str | None
    ) -> Mapping[str, str]:
        """`binding_id` -> the status of its head revision."""
        ...

    def items_in_scope(self, *, system_id: str) -> Sequence[KnowledgeItem]:
        """Scoped by system only.

        `knowledge_item.environment_id` is nullable, so filtering it by
        environment here would silently drop every item that spans environments
        -- which is the population the environment check in `recompute_coverage`
        exists to reason about.
        """
        ...

    def head_item_verifications(self, *, system_id: str) -> Mapping[str, str | None]:
        """`item_id` -> `verification` on its current knowledge revision.

        Absent when the item has no current revision; `None` when the revision
        carries no verification, which the column permits.
        """
        ...

    def audience_counts(self, *, system_id: str) -> Mapping[str, int]:
        """`item_id` -> how many `audience_tag` rows it carries."""
        ...

    def boundaries_for_system(self, *, system_id: str) -> Sequence[ObservabilityBoundary]: ...
