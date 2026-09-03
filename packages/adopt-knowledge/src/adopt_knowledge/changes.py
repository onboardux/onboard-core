"""The three review actions Build 6's change population takes (v6.1 §6).

`refresh` puts an item in front of a human because something it describes
changed. This module is what the human's answer *does*, and there are exactly
three answers:

| Action | What the reviewer is saying | The store-state consequence |
|---|---|---|
| **retire** | "this note is obsolete" | a terminal knowledge revision; the item resolves `retired` |
| **rebind** | "it followed the referent that replaced it" | the old link appends `moved`; a new binding to the successor |
| **confirm-current** | "it is still true as written" | a `human_confirmed`/`verified` revision; the staled links go `fresh` |

**The resolution enum is the disposition; the store state is the record.**
`confirm-current` stamps `confirmed` and the other two stamp `corrected` (plan
decision D12), which is all three values `review_resolution` has room for -- and
it is deliberately *not* how a reader tells the actions apart. Two of them share
a value, so the honest place to look is what the store now says: an item that is
`retired`, a chain of bindings whose head is `moved` beside a new one, or a
revision a person put their name on. Inventing a fourth enum value to make the
disposition self-describing would have been a schema change (§8 budgets none)
buying a worse record than the one the writes already leave.

**Recorded first, acted second -- the same ordering `review.confirm` uses and
for the same reason.** `resolve` refuses an item that is already resolved, so it
is the guard that makes a double resolution impossible. Acting first would
retire the item, or supersede its bindings, and only *then* discover the entry
had been answered an hour ago by somebody else.

**What `confirm-current` cannot do, it says rather than appears to do.** For a
DEAD or MOVED cause the item stays STALE after the confirmation, because
`resolve_freshness`' *source* rules read the identity's own head status and no
binding-level write reaches them. That is correct -- the referent really is gone
-- and the reviewer is told so by name, with the two actions that can help. A
`confirm-current` that silently left the item stale would look like a broken
button; one that forced it fresh would be the product lying about a deleted
endpoint.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from adopt_knowledge.ingest import EXTRACTOR_NAME_CONFIRMED, INGEST_EXTRACTOR_VERSION
from adopt_knowledge.ports import (
    BindingFreshener,
    BindingSuperseder,
    ItemRetirer,
    KnowledgeWriter,
    ReviewWriter,
    UnitOfWork,
)
from adopt_knowledge.review import (
    CHANGE_POPULATIONS,
    CONFIRMED,
    CORRECTED,
    PendingItem,
    _append_human_revision,
)
from adopt_model._enums import ImpactClass, ReviewResolution
from adopt_obs import AdoptError, ErrorCode, get_logger

__all__ = [
    "ACTIONS",
    "ACTION_CONFIRM_CURRENT",
    "ACTION_REBIND",
    "ACTION_RETIRE",
    "SOURCE_RULED_CLASSES",
    "ChangeOutcome",
    "ChangedBinding",
    "confirm_current_item",
    "rebind_item",
    "retire_item",
    "still_stale_after_confirm",
]

_log = get_logger("adopt_knowledge")

#: The `--action` vocabulary, v6.1 §6 Build 6's demo line, spelled once.
ACTION_RETIRE: Final[str] = "retire"
ACTION_REBIND: Final[str] = "rebind"
ACTION_CONFIRM_CURRENT: Final[str] = "confirm-current"

ACTIONS: Final[tuple[str, ...]] = (ACTION_RETIRE, ACTION_REBIND, ACTION_CONFIRM_CURRENT)

#: The classes `resolve_freshness` stales from the **identity's** head status
#: rather than from the binding row -- so no binding write clears them. Typed
#: from the generated enum, which is the machine-gated spelling authority.
_DEAD: Final[ImpactClass] = "BINDING_DEAD"
_MOVED: Final[ImpactClass] = "BINDING_MOVED"
_SEMANTICS: Final[ImpactClass] = "BINDING_INTACT_SEMANTICS_CHANGED"

SOURCE_RULED_CLASSES: Final[frozenset[str]] = frozenset({_DEAD, _MOVED})

#: What a retirement records as its reason when the reviewer supplied none. The
#: revision needs a body -- the knowledge family carries its terminal state on
#: the parent and its reason in the text -- and "resolved from the queue" is the
#: truthful minimum rather than an empty string that reads as a lost value.
DEFAULT_RETIRE_REASON: Final[str] = "retired from the refresh review queue"


@dataclass(frozen=True, slots=True)
class ChangedBinding:
    """One (item <-> changed identity) link a resolution acts on.

    Assembled by the caller, which is the half that may read a store -- the same
    split `PendingItem` uses, and for the same reason: what the reviewer was
    shown and what the action operates on are provably the same tuple rather
    than two queries run a moment apart.
    """

    binding_id: str
    item_id: str
    identity_id: str
    identity_uri: str
    impact_class: str
    is_load_bearing: bool

    @property
    def is_source_ruled(self) -> bool:
        """Whether staleness comes from the identity rather than from this row."""
        return self.impact_class in SOURCE_RULED_CLASSES


@dataclass(frozen=True, slots=True)
class ChangeOutcome:
    """What resolving one change item did -- every field a store consequence.

    A separate value from `review.Outcome` rather than four more optional fields
    on it: the other three populations answer "is this proposal right?" and
    carry bindings and a revision, while this one answers "what happened to the
    system, and what should the note do about it?". One dataclass covering both
    would have every field optional, and a payload whose meaning depends on
    which half is populated is two contracts wearing one name.
    """

    action: str
    resolution: ReviewResolution
    revision_id: str | None = None
    provenance_ids: tuple[str, ...] = ()
    superseded_bindings: tuple[str, ...] = ()
    new_binding_id: str | None = None
    freshened_bindings: tuple[str, ...] = ()
    #: Referents whose staleness this action could not clear, by URI. Populated
    #: only by `confirm-current`, and reported rather than suppressed: an item
    #: that stays STALE after a confirmation needs the reason on screen.
    still_stale: tuple[str, ...] = ()


def still_stale_after_confirm(affected: Sequence[ChangedBinding]) -> tuple[str, ...]:
    """The referent URIs a `confirm-current` leaves stale, sorted.

    Exposed rather than kept private because the CLI has to say the sentence
    *before* the reviewer commits to the action as well as after it, and two
    implementations of "which causes are source-ruled" would eventually disagree
    about the one thing this build is careful to be honest about.
    """
    return tuple(sorted({link.identity_uri for link in affected if link.is_source_ruled}))


def retire_item(
    item: PendingItem,
    *,
    reviews: ReviewWriter,
    knowledge: ItemRetirer,
    unit: UnitOfWork,
    reason: str = DEFAULT_RETIRE_REASON,
    actor_id: str | None = None,
) -> ChangeOutcome:
    """End the item: append its terminal revision, and stamp the queue `corrected`.

    `corrected` rather than `rejected`, because the reviewer is not saying the
    entry was wrong to appear -- the change was real and the queue was right to
    ask. They are saying the *note* is finished, which is a correction to the
    knowledge (D12).

    **Nothing is deleted, here or anywhere.** The item keeps every revision it
    ever had and stays readable: coverage provenance depends on it (PRD F6.7),
    and `resolve_freshness` reports `retired` -- which is what lets `adopt ask`
    answer "that was withdrawn" instead of falling silent.

    **One transaction, added by T1.3/T1.4** (B2-03). The ordering below is
    unchanged and still deliberate; what was missing was the boundary. A
    resolution that committed and a write that then failed left the queue
    entry stamped and the work undone, and `_resolve` refuses the retry -- so
    the store recorded a human decision that never took effect and nothing
    could correct it.
    """
    _require_change_item(item, ACTION_RETIRE)
    with unit.transaction():
        _resolve(reviews, item, CORRECTED)
        revision_id = knowledge.retire(item_id=item.item_id, reason=reason, actor_id=actor_id)
    _log.info(
        "change.resolved",
        action=ACTION_RETIRE,
        review_item=item.review_item_id,
        batch=item.review_batch_id,
        revision=revision_id,
    )
    return ChangeOutcome(action=ACTION_RETIRE, resolution=CORRECTED, revision_id=revision_id)


def rebind_item(
    item: PendingItem,
    *,
    reviews: ReviewWriter,
    bindings: BindingSuperseder,
    unit: UnitOfWork,
    affected: Sequence[ChangedBinding],
    target_identity_id: str,
    target_uri: str,
    actor_id: str | None = None,
) -> ChangeOutcome:
    """Re-point the item at the referent that replaced the changed one.

    Two writes, in this order and for this reason: every load-bearing link to
    the changed referent appends `moved` -- *replaced*, not withdrawn -- and one
    new binding is created to the target. Superseding first means there is never
    an instant in which the item is anchored to two live links, which is what a
    coverage recount happening between the writes would otherwise see.

    The new binding is **load-bearing**, on `review.confirm`'s precedent: only
    load-bearing links are superseded, and a human who named the successor has
    supplied the same standard of evidence a structural match does.

    Raises:
        AdoptError: ``REVIEW_ITEM_NOT_FOUND`` when the entry is not a change
            item, and ``BIND_TARGET_NOT_FOUND`` when no load-bearing link to a
            changed referent exists to re-point. The second refusal matters:
            without it a rebind on an unaffected item would quietly add a
            binding to a successor and supersede nothing, leaving the item bound
            to both.
    """
    _require_change_item(item, ACTION_REBIND)
    superseded_links = tuple(
        link for link in affected if link.is_load_bearing and link.item_id == item.item_id
    )
    if not superseded_links:
        raise AdoptError(
            ErrorCode.BIND_TARGET_NOT_FOUND,
            message=f"review item {item.review_item_id} has no load-bearing link to a "
            "changed referent, so there is nothing to re-point",
            hint="Rebind replaces a link that a change made wrong. An item whose bindings "
            "are all intact is confirmed or retired, not rebound -- adding the new "
            "binding alone would leave it bound to both referents.",
        )

    superseded: list[str] = []
    with unit.transaction():
        _resolve(reviews, item, CORRECTED)

        for link in sorted(superseded_links, key=lambda row: row.binding_id):
            bindings.supersede(binding_id=link.binding_id, actor_id=actor_id)
            superseded.append(link.binding_id)

        new_binding_id, _ = bindings.bind(
            item_id=item.item_id,
            identity_id=target_identity_id,
            is_load_bearing=True,
            extractor=EXTRACTOR_NAME_CONFIRMED,
            extractor_version=INGEST_EXTRACTOR_VERSION,
            actor_id=actor_id,
        )

    _log.info(
        "change.resolved",
        action=ACTION_REBIND,
        review_item=item.review_item_id,
        batch=item.review_batch_id,
        superseded=len(superseded),
    )
    return ChangeOutcome(
        action=ACTION_REBIND,
        resolution=CORRECTED,
        superseded_bindings=tuple(superseded),
        new_binding_id=new_binding_id,
    )


def confirm_current_item(
    item: PendingItem,
    *,
    reviews: ReviewWriter,
    knowledge: KnowledgeWriter,
    freshener: BindingFreshener,
    unit: UnitOfWork,
    affected: Sequence[ChangedBinding],
    actor_id: str | None = None,
) -> ChangeOutcome:
    """Re-affirm the note as written, and return the links it re-affirmed to `fresh`.

    The revision carries the **current head body unchanged** and is
    `human_confirmed` / `verified` with `human` provenance -- the same append
    `review.confirm` makes for a candidate, through the same helper. There is no
    argument that makes it `artifact_observed`: nothing was re-read from an
    artifact, a person read what we already had and said it still holds.

    Only links whose cause is SEMANTICS-CHANGED are freshened. A DEAD or MOVED
    cause is source-ruled -- `resolve_freshness` reads the identity's own head
    status, which no binding write reaches -- so those URIs come back on the
    outcome as `still_stale` for the caller to name. That is the honest answer
    and not a defect: the referent is gone, and the actions that help are
    `retire` and `rebind`.
    """
    _require_change_item(item, ACTION_CONFIRM_CURRENT)
    with unit.transaction():
        _resolve(reviews, item, CONFIRMED)

        revision_id, provenance_ids = _append_human_revision(
            item,
            knowledge=knowledge,
            body_md=item.body_md,
            source_ref=item.review_item_id,
            actor_id=actor_id,
        )

        reaffirmed = [
            link.binding_id
            for link in affected
            if link.item_id == item.item_id
            and link.is_load_bearing
            and link.impact_class == _SEMANTICS
        ]
        freshened = freshener.freshen_bindings(reaffirmed)

    _log.info(
        "change.resolved",
        action=ACTION_CONFIRM_CURRENT,
        review_item=item.review_item_id,
        batch=item.review_batch_id,
        revision=revision_id,
        freshened=len(freshened),
    )
    return ChangeOutcome(
        action=ACTION_CONFIRM_CURRENT,
        resolution=CONFIRMED,
        revision_id=revision_id,
        provenance_ids=provenance_ids,
        freshened_bindings=freshened,
        still_stale=still_stale_after_confirm(
            [link for link in affected if link.item_id == item.item_id]
        ),
    )


def _require_change_item(item: PendingItem, action: str) -> None:
    """Refuse an action on a population it does not belong to.

    The message names what the item **is** rather than reporting it absent,
    which is `_targets`' own rule (CR-38's precedent): the id was perfectly
    correct and sending its operator to hunt for a typo would be the second
    time this queue made that mistake.

    Raises:
        AdoptError: ``REVIEW_ITEM_NOT_FOUND``, the registered usage code for a
            queue entry an invocation cannot act on. Build 6 adds no error code
            (plan decision D14).
    """
    if item.source not in CHANGE_POPULATIONS:
        raise AdoptError(
            ErrorCode.REVIEW_ITEM_NOT_FOUND,
            message=f"review item {item.review_item_id} belongs to the {item.source!r} "
            f"population, and --action {action} resolves change items",
            hint="A suggestion or a candidate is answered with --confirm, --reject or "
            "--edit. The three change actions describe what happened to a *referent*, "
            "which is not what those items are about.",
        )


def _resolve(reviews: ReviewWriter, item: PendingItem, resolution: ReviewResolution) -> None:
    """Stamp the disposition first, so a second resolution is refused.

    The returned row is deliberately dropped: everything an action operates on
    travels on the `PendingItem` and the `ChangedBinding`s the caller assembled,
    so re-reading it here would introduce a second, later view of the same
    subject -- exactly what `PendingItem`'s docstring says the value exists to
    prevent.
    """
    reviews.resolve(review_item_id=item.review_item_id, resolution=resolution)
