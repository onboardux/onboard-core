"""The store slices a pack is assembled from, declared structurally.

`adopt_handover` never imports `adopt_store`, for the reason `adopt_map`,
`adopt_knowledge`, `adopt_detect`, `adopt_export` and `adopt_coverage` do not
(CR-34, CR-37): `no-raw-sqlite` names this package as a source module and
import-linter follows indirect chains, so a dependency on the store would reach
`sqlite3` and break the contract. The CLI is the composition root and hands
realizations in.

**Every protocol here is read-only, and that is the package's whole posture.**
There is no writer slice to reach for, because a pack is a rendering: it selects
what the store already holds and composes bytes. The one thing Build 4 *writes*
-- a gap disposition -- goes through `GovernanceFacade` from the CLI, and
Build 4's drafts go through `adopt_knowledge`. Assembly that could write would
be assembly that could quietly promote what it rendered.
"""

import datetime as _dt
from collections.abc import Sequence
from typing import Protocol

__all__ = [
    "BoundaryReader",
    "BoundaryView",
    "FreshnessReader",
    "GapView",
    "IdentityReader",
    "IdentityView",
    "KnowledgeReader",
    "KnowledgeView",
]


class KnowledgeView(Protocol):
    """One knowledge revision as a pack section needs it.

    Structural rather than the generated `KnowledgeRevision`, because a section
    needs its item's title and audience alongside the revision's body, and no
    single generated row carries both. The caller assembles the view; this
    package never joins.
    """

    @property
    def item_id(self) -> str: ...
    @property
    def revision_id(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def body_md(self) -> str: ...
    @property
    def verification(self) -> str | None:
        """`verified` | `unverified` | `conflicted`, or `None` when unset.

        `None` is treated as unverified by `stamp_for`, never as verified: a
        revision whose marker was never written is a revision nobody confirmed.
        """
        ...

    @property
    def created_at(self) -> _dt.datetime:
        """The revision's own timestamp -- the date a section is stamped with.

        Never a render-time clock. A pack must be byte-identical given the same
        revisions, and a "generated at" date is the one field that would make
        every run differ.
        """
        ...

    @property
    def identity_uris(self) -> tuple[str, ...]:
        """The identities this revision's item is bound to, for the sidecar."""
        ...

    @property
    def audiences(self) -> tuple[str, ...]: ...


class IdentityView(Protocol):
    """One identity, as the inventory section lists it."""

    @property
    def uri(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def covered(self) -> bool: ...


class GapView(Protocol):
    """One uncovered identity and the human disposition of it, if any.

    `status` is `None` when nobody has dispositioned the gap. The gap still
    exists -- existence is derived by `recompute_coverage()` and this view is
    the join, so an undisposed gap renders as `open` and a disposition for a gap
    the recompute no longer derives is simply absent from the list.
    """

    @property
    def uri(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def gap_key(self) -> str: ...
    @property
    def reasons(self) -> tuple[str, ...]: ...
    @property
    def status(self) -> str | None: ...
    @property
    def owner_actor_id(self) -> str | None: ...
    @property
    def note(self) -> str | None: ...
    @property
    def waived_until(self) -> _dt.datetime | None: ...


class BoundaryView(Protocol):
    """The declared observability boundary, embedded so a pack states its limits."""

    @property
    def tier(self) -> str: ...
    @property
    def covered(self) -> str | None: ...
    @property
    def not_covered(self) -> str | None: ...
    @property
    def permitted_outbound_categories(self) -> Sequence[str]: ...
    @property
    def declared_at(self) -> _dt.datetime: ...
    @property
    def contractual(self) -> bool: ...


class KnowledgeReader(Protocol):
    """Confirmed knowledge for one system, already joined to titles and bindings."""

    def knowledge_for_pack(self) -> Sequence[KnowledgeView]: ...


class IdentityReader(Protocol):
    """The identity inventory the overview section renders."""

    def identities_for_pack(self) -> Sequence[IdentityView]: ...


class FreshnessReader(Protocol):
    """The freshness verdict for one knowledge item.

    A port rather than a passed-in value because a section stamps per revision
    and the resolution is `adopt_freshness`'s to make. Returning a bare string
    keeps this package free of that package's types -- the pack renders what it
    is told, and the authority stays where it is.
    """

    def freshness_of(self, item_id: str) -> str: ...


class BoundaryReader(Protocol):
    def boundary_for_pack(self) -> BoundaryView | None: ...
