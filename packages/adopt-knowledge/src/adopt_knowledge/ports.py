"""The narrow store slices this package is handed, declared structurally.

`adopt_knowledge` never imports `adopt_store`, for the reason `adopt_map`,
`adopt_detect`, `adopt_export` and `adopt_coverage` do not (CR-34, CR-37):
`no-raw-sqlite` names this package as a source module and import-linter follows
indirect chains, so a dependency on the store would reach `sqlite3` and break
the contract. The CLI is the composition root and hands realizations in.

Each protocol is the **smallest** slice that does the job, and the omissions are
the point:

* nothing here can delete or update a `*_revision` row -- there is no such
  method to reach for;
* `BindingWriter` can create and read, and cannot retire: a binding this build
  made wrong is a review decision to reverse, not a row for the matcher to
  quietly withdraw; and
* `ReviewWriter` records a disposition and returns what was decided. It cannot
  *act* on the decision, so the code that binds on confirmation is always the
  caller's and is always visible.
"""

import datetime as _dt
from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from adopt_model import Binding, ReviewItem
from adopt_model._enums import AuthorityClass, ItemKind, ReviewResolution, SourceType, Verification
from adopt_scope import Scope

__all__ = [
    "BindingWriter",
    "DraftStore",
    "KnowledgeWriter",
    "ReviewWriter",
]


class KnowledgeWriter(Protocol):
    """The slice of `KnowledgeFacade` ingest and harvest write through."""

    def record(
        self,
        *,
        scope: Scope,
        kind: ItemKind,
        title: str,
        body_md: str,
        authority_class: AuthorityClass,
        verification: Verification | None = ...,
        confidence: float | None = ...,
        source_version: str | None = ...,
        actor_id: str | None = ...,
    ) -> tuple[str, str]: ...

    def append(
        self,
        *,
        item_id: str,
        expected_head_id: str | None,
        body_md: str,
        authority_class: AuthorityClass,
        verification: Verification | None = ...,
        confidence: float | None = ...,
        source_version: str | None = ...,
        actor_id: str | None = ...,
    ) -> str: ...

    def record_provenance(
        self,
        *,
        revision_id: str,
        source_type: SourceType,
        source_ref: str,
        observed_at: _dt.datetime | None = ...,
    ) -> str: ...

    def tag_audience(self, *, item_id: str, audience: str) -> bool: ...


class BindingWriter(Protocol):
    """The slice of `BindingFacade` the matchers' conclusions go through."""

    def bind(
        self,
        *,
        item_id: str,
        identity_id: str,
        is_load_bearing: bool,
        extractor: str | None = ...,
        extractor_version: str | None = ...,
        confidence: float | None = ...,
        actor_id: str | None = ...,
    ) -> tuple[str, str]: ...

    def for_identity(self, identity_id: str) -> tuple[Binding, ...]: ...


class ReviewWriter(Protocol):
    """The slice of `GovernanceFacade` the queue is opened and resolved through."""

    def open_batch(
        self,
        *,
        system_id: str,
        batch_key: str,
        items: Sequence[tuple[str, str | None]],
        owner_actor_id: str | None = ...,
    ) -> tuple[str, tuple[str, ...]]: ...

    def get_item(self, review_item_id: str) -> ReviewItem | None: ...

    def items_in(self, review_batch_id: str) -> tuple[ReviewItem, ...]: ...

    def resolve(self, *, review_item_id: str, resolution: ReviewResolution) -> ReviewItem: ...


class DraftStore(KnowledgeWriter, BindingWriter, ReviewWriter, Protocol):
    """The three writers **plus the unit of work**, for `drafting` alone.

    One protocol rather than three arguments, for the reason `adopt_ask.capture`
    gives for its own: a draft is one transaction, and a caller that could supply
    the knowledge writer and the binding writer separately could supply two that
    do not share a connection -- which is precisely the half-written store the
    single-transaction shape exists to make impossible. A draft whose revision
    committed and whose binding did not is knowledge bound to nothing: it counts
    toward no coverage, stales on no change, and appears in the review queue as
    a section about an identity it cannot name.

    `transaction` is therefore on the protocol rather than an implementation
    detail of whoever calls it. Harvest and ingest do not need this -- a
    partially written harvest is a candidate a re-harvest recreates -- which is
    why the transactional slice arrives here rather than under every writer.
    """

    def transaction(self) -> AbstractContextManager[None]: ...
