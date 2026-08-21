"""The one review queue (v6.1 §6 F5) -- both of its populations.

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

## Two populations, two meanings of "confirm"

One queue does not mean one action, and collapsing them would be the H2 failure
in a new costume -- a reviewer pressing confirm for one reason and getting a
second thing they never looked at.

| Item | What the reviewer is being asked | What confirming does |
|---|---|---|
| **suggestion** (`ingest:`) | "is this document about this identity?" | creates the binding rows |
| **candidate** (`harvest:`) | "is this commit a real decision worth keeping?" | appends a `verified` revision |

A candidate already **has** its bindings -- its commit's files are structural
evidence and bound at harvest (plan D4) -- so confirming one adds no link. A
suggestion's document is already `verified` -- ingest is transcription of a
human's own prose -- so confirming one appends no revision. Which population an
item belongs to is read from `review_batch.batch_key`, whose prefix is stamped
by whatever produced it; that is the field's declared job, and it is how Build 6
and Build 8 will sit in this table without guessing about each other.

**Editing is one mechanic for both**, and it is where `authored` becomes
visible: `--edit` appends a `human_confirmed` revision whose provenance is
`human`. The superseded revision keeps its `artifact_observed` class and its
commit citation untouched, because provenance rows belong to a revision and this
is a new one. That is the whole of how mined and authored stay distinguishable
forever -- not a flag, a chain.
"""

from collections.abc import Container, Sequence
from dataclasses import dataclass, field
from typing import Final

from adopt_knowledge.ingest import EXTRACTOR_NAME_CONFIRMED, INGEST_EXTRACTOR_VERSION
from adopt_knowledge.matchers import IdentityView, Match, name_matches
from adopt_knowledge.ports import BindingWriter, KnowledgeWriter, ReviewWriter
from adopt_model._enums import AuthorityClass, ReviewResolution, SourceType, Verification
from adopt_obs import get_logger

__all__ = [
    "SOURCE_HARVEST",
    "SOURCE_INGEST",
    "Outcome",
    "PendingItem",
    "confirm",
    "derive_suggestions",
    "edit",
    "reject",
    "source_of",
]

_log = get_logger("adopt_knowledge")

CONFIRMED: Final[ReviewResolution] = "confirmed"
CORRECTED: Final[ReviewResolution] = "corrected"
REJECTED: Final[ReviewResolution] = "rejected"

#: `review_batch.batch_key` prefixes. The producer stamps one; this is the only
#: thing that reads it, and adding a population means adding a prefix here.
SOURCE_INGEST: Final[str] = "ingest"
SOURCE_HARVEST: Final[str] = "harvest"

#: A human looked at it and said yes, or wrote it themselves. Either way the
#: authority is theirs and the provenance is `human` -- **never**
#: `artifact_observed`, which is a claim about where text was read from and
#: cannot be acquired after the fact.
_HUMAN_AUTHORITY: Final[AuthorityClass] = "human_confirmed"
_HUMAN_VERIFICATION: Final[Verification] = "verified"
_HUMAN_SOURCE: Final[SourceType] = "human"


def source_of(batch_key: str) -> str:
    """Which population a batch belongs to, from the key its producer stamped."""
    return batch_key.split(":", 1)[0]


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
    #: The text the suggestions were derived from, and what a `confirm` on a
    #: candidate carries forward unchanged into its verified revision.
    body_md: str = ""
    #: The item's current head. `append` enforces it, so a head that moved
    #: between listing and confirming raises `REVISION_CHAIN_FORK` rather than
    #: forking the chain.
    head_revision_id: str | None = None
    #: `knowledge_revision.source_version` of the head -- a commit sha for a
    #: candidate, a body digest for a document. Carried forward so a confirmed
    #: candidate still names the commit it came from.
    source_version: str | None = None
    #: `(source_type, source_ref)` for the proposed revision -- the commit sha
    #: and any ADR path. **This is what a reviewer reads a candidate by**, and
    #: it comes from `provenance` rather than from the body, which is why the
    #: body could stay the author's own words.
    evidence: tuple[tuple[str, str], ...] = ()

    @property
    def source(self) -> str:
        return source_of(self.batch_key)

    @property
    def is_candidate(self) -> bool:
        return self.source == SOURCE_HARVEST


@dataclass(frozen=True, slots=True)
class Outcome:
    """What resolving one item actually did.

    Both fields, always, because the caller renders one envelope for a queue
    holding both populations -- and a payload whose shape depended on which
    branch ran would make `adopt review --json` two contracts.
    """

    resolution: ReviewResolution
    bindings: tuple[Match, ...] = ()
    revision_id: str | None = None
    provenance_ids: tuple[str, ...] = field(default=())


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
    knowledge: KnowledgeWriter | None = None,
    bound_pairs: frozenset[tuple[str, str]] = frozenset(),
    actor_id: str | None = None,
) -> Outcome:
    """Record the confirmation, then act on what it confirmed.

    **The disposition is recorded first, deliberately.** `resolve` refuses an
    item that is already resolved, so it is the guard that makes double
    confirmation impossible; acting first and stamping second would create the
    bindings -- or append the revision -- and *then* discover the item had
    already been confirmed once.

    Args:
        knowledge: Required to confirm a **candidate**, which appends a
            revision. Optional otherwise, so a caller resolving suggestions
            need not hold a writer it will not use.

    Returns:
        An `Outcome`. For a suggestion, the bindings actually created -- which
        is not always every suggestion, because a pair bound between the ingest
        and the confirmation is left alone rather than raising
        `REVISION_CHAIN_FORK` on a UNIQUE index. For a candidate, the id of the
        verified revision the confirmation appended.
    """
    reviews.resolve(review_item_id=item.review_item_id, resolution=CONFIRMED)

    if item.is_candidate:
        if knowledge is None:  # pragma: no cover -- a wiring mistake, not a state
            raise ValueError(
                "confirming a harvest candidate appends a verified revision, so it "
                "needs a knowledge writer. Pass one, or the confirmation would stamp "
                "the queue and leave the knowledge unverified."
            )
        revision_id, provenance_ids = _append_human_revision(
            item,
            knowledge=knowledge,
            body_md=item.body_md,
            source_ref=item.review_item_id,
            actor_id=actor_id,
        )
        _log.info(
            "review.confirmed",
            review_item=item.review_item_id,
            batch=item.review_batch_id,
            revision=revision_id,
        )
        return Outcome(resolution=CONFIRMED, revision_id=revision_id, provenance_ids=provenance_ids)

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
    return Outcome(resolution=CONFIRMED, bindings=tuple(created))


def edit(
    item: PendingItem,
    *,
    reviews: ReviewWriter,
    knowledge: KnowledgeWriter,
    body_md: str,
    source_ref: str,
    actor_id: str | None = None,
) -> Outcome:
    """Correct the knowledge, then record that it was corrected.

    One mechanic for both populations: a mined candidate whose rationale needs
    rewriting and an ingested document whose prose is wrong are the same act --
    a person supplying text. The resolution is `corrected` rather than
    `confirmed` because the reviewer accepted neither what they were shown nor
    nothing at all, and Build 8's confirm-rate trigger has to be able to tell
    the three apart.

    **The new revision is `human_confirmed` with `human` provenance, always.**
    There is no argument that makes it `artifact_observed`, which is the point:
    authored text cannot claim to have been observed in an artifact, and the
    superseded revision keeps its own class and citation because provenance
    belongs to a revision rather than to an item.
    """
    reviews.resolve(review_item_id=item.review_item_id, resolution=CORRECTED)
    revision_id, provenance_ids = _append_human_revision(
        item,
        knowledge=knowledge,
        body_md=body_md,
        source_ref=source_ref,
        actor_id=actor_id,
    )
    _log.info(
        "review.corrected",
        review_item=item.review_item_id,
        batch=item.review_batch_id,
        revision=revision_id,
    )
    return Outcome(resolution=CORRECTED, revision_id=revision_id, provenance_ids=provenance_ids)


def reject(
    item: PendingItem,
    *,
    reviews: ReviewWriter,
) -> Outcome:
    """Record the rejection. **Nothing is written but the disposition.**

    A rejected suggestion leaves no trace on the knowledge or the binding
    tables, which is the honest outcome: the reviewer said the document is not
    about that identity, and the store should look exactly as it would have had
    the matcher never proposed it. A rejected candidate keeps the unverified
    revision it already had -- the mining happened and the record of it is
    evidence -- and stays unverified forever, so it never counts toward
    coverage and is never served as canon.
    """
    reviews.resolve(review_item_id=item.review_item_id, resolution=REJECTED)
    _log.info("review.rejected", review_item=item.review_item_id, batch=item.review_batch_id)
    return Outcome(resolution=REJECTED)


def _append_human_revision(
    item: PendingItem,
    *,
    knowledge: KnowledgeWriter,
    body_md: str,
    source_ref: str,
    actor_id: str | None,
) -> tuple[str, tuple[str, ...]]:
    """Append the revision a human's decision produces, and cite them for it."""
    revision_id = knowledge.append(
        item_id=item.item_id,
        expected_head_id=item.head_revision_id,
        body_md=body_md,
        authority_class=_HUMAN_AUTHORITY,
        verification=_HUMAN_VERIFICATION,
        source_version=item.source_version,
        actor_id=actor_id,
    )
    provenance_id = knowledge.record_provenance(
        revision_id=revision_id,
        source_type=_HUMAN_SOURCE,
        source_ref=source_ref,
    )
    return revision_id, (provenance_id,)
