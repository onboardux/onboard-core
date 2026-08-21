"""The one review queue (v6.1 §6 F5) -- Build 2's half of it.

One surface from this build on: harvest candidates and suggested bindings now,
Build 6's change items and Build 8's managed batches later, all in
`review_batch` / `review_item`. F5 exists because v6.0 had specified two
`adopt review` commands for two builds, and two queues means a reviewer has two
places to remember to look, one of which they will stop looking at.

**Suggestions are derived, never stored** (plan decision D3). `review_item` has
columns for a batch, a knowledge item and a disposition -- and none for a
proposed *identity*, because the schema was designed for change review, not for
this. The obvious repairs are both worse than deriving:

* an additive column re-litigates the §8 schema budget for a value that is a
  pure function of data already in the store; and
* a provisional `binding` row with a `suggested` status is exactly the false
  binding H2 forbids -- `recompute_coverage` would count it, and the honesty
  invariant would be broken by the mechanism meant to protect it.

Re-deriving costs one pass over the document body and has a property the stored
alternatives lack: a registry that grew between ingest and review produces
*current* suggestions. Nothing provisional was written, so nothing has to be
cleaned up when a reviewer takes a week.
"""

from collections.abc import Container, Sequence
from dataclasses import dataclass
from typing import Final

from adopt_knowledge.ingest import EXTRACTOR_NAME_CONFIRMED, INGEST_EXTRACTOR_VERSION
from adopt_knowledge.matchers import IdentityView, Match, name_matches
from adopt_knowledge.ports import BindingWriter, ReviewWriter
from adopt_model._enums import ReviewResolution
from adopt_obs import get_logger

__all__ = ["PendingItem", "confirm", "derive_suggestions", "reject"]

_log = get_logger("adopt_knowledge")

CONFIRMED: Final[ReviewResolution] = "confirmed"
REJECTED: Final[ReviewResolution] = "rejected"


@dataclass(frozen=True, slots=True)
class PendingItem:
    """One unresolved queue entry, with everything a decision needs.

    Assembled by the caller, which is the half that may read a store. The
    suggestions travel on the value rather than being re-derived inside
    `confirm`, so what a reviewer was shown and what confirming acts on are
    provably the same tuple.
    """

    review_item_id: str
    review_batch_id: str
    batch_key: str
    item_id: str
    title: str
    suggestions: tuple[Match, ...]


def derive_suggestions(
    body_md: str,
    identities: Sequence[IdentityView],
    *,
    already_bound: Container[str] = frozenset(),
) -> tuple[Match, ...]:
    """The name matches an item currently has, excluding what is already bound.

    The same function that produced the suggestions at ingest, called again --
    one implementation, so the queue can never offer something the matcher
    would no longer propose.
    """
    return name_matches(body_md, identities, exclude=already_bound)


def confirm(
    item: PendingItem,
    *,
    reviews: ReviewWriter,
    bindings: BindingWriter,
    bound_pairs: frozenset[tuple[str, str]] = frozenset(),
    actor_id: str | None = None,
) -> tuple[Match, ...]:
    """Record the confirmation, then bind what it confirmed.

    **The disposition is recorded first, deliberately.** `resolve` refuses an
    item that is already resolved, so it is the guard that makes double
    confirmation impossible; binding first and stamping second would create the
    bindings and *then* discover the item had already been confirmed once.

    Returns:
        The bindings actually created -- which is not always every suggestion,
        because a pair bound between the ingest and the confirmation is left
        alone rather than raising `REVISION_CHAIN_FORK` on a UNIQUE index.
    """
    reviews.resolve(review_item_id=item.review_item_id, resolution=CONFIRMED)

    created: list[Match] = []
    for match in item.suggestions:
        if (item.item_id, match.identity_id) in bound_pairs:
            continue
        bindings.bind(
            item_id=item.item_id,
            identity_id=match.identity_id,
            # A human looked at this and said yes. That is the same standard of
            # evidence a structural match meets, so the binding is load-bearing
            # on the same terms.
            is_load_bearing=True,
            extractor=EXTRACTOR_NAME_CONFIRMED,
            extractor_version=INGEST_EXTRACTOR_VERSION,
            actor_id=actor_id,
        )
        created.append(match)

    _log.info(
        "review.confirmed",
        review_item=item.review_item_id,
        batch=item.review_batch_id,
        bindings=len(created),
    )
    return tuple(created)


def reject(
    item: PendingItem,
    *,
    reviews: ReviewWriter,
) -> None:
    """Record the rejection. **Nothing is written but the disposition.**

    A rejected suggestion leaves no trace on the knowledge or the binding
    tables, which is the honest outcome: the reviewer said the document is not
    about that identity, and the store should look exactly as it would have had
    the matcher never proposed it.
    """
    reviews.resolve(review_item_id=item.review_item_id, resolution=REJECTED)
    _log.info("review.rejected", review_item=item.review_item_id, batch=item.review_batch_id)
