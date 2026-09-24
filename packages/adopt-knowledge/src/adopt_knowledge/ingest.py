"""`adopt ingest` -- documents become knowledge, bound honestly.

The run does four things per document and refuses to do a fifth:

1. **Writes or updates the item.** Keyed on the document's path, so a second
   run over an unchanged tree writes nothing at all. Idempotence is a property
   of the comparison the writer makes, exactly as `adopt map`'s is a property
   of `observe` being keyed on the URI.
2. **Records provenance** for the revision it wrote -- `human`, because a
   document in a repository is something a person wrote, and the path, so the
   claim can be traced back to the file it came from.
3. **Tags the audience**, from frontmatter, an operator override, or the path.
4. **Binds what the document structurally refers to**, and queues what it
   merely names.

The fifth thing -- binding a name match -- is the one this build exists to
refuse. See `matchers` for why, and `test_binding_honesty` for the assertion
that it stays refused.

**Verification: an ingested document lands `verified`** (plan decision D2).
This is transcription of prose a human already wrote and shipped in their own
repository, not a machine's inference about their system. Harvest candidates
are the opposite case and land `unverified`. Getting this backwards in either
direction breaks something real: mark ingest unverified and `adopt gaps`
reports a documented system as entirely uncovered; mark harvest verified and
mined guesses become canon nobody agreed to.

**`--unverified` is the door for text nobody has vouched for** -- above all,
anything a coding agent wrote or helped write. D2's premise is that a document
is a human's own shipped prose, and an agent that writes a Markdown file and
ingests it breaks the premise while every row still looks correct: the revision
lands `verified` and `artifact_observed`, `recompute_coverage` counts it, and
`adopt ask` serves it as KNOWN. Nothing downstream can tell. So the operator
says so at the door, and the run then writes exactly what Build 4's drafting
writes for authored text -- `human_confirmed` authority (authored, never
observed), `unverified`, `human` provenance citing the path -- and queues every
revision it wrote in an `ingest-unverified:` batch, whose confirmation appends
the `verified` revision a person agreed to. Until then the document counts
toward no coverage, serves no answer and reaches no pack.

**An unverified document is asked one question at a time.** Its name-match
suggestions are not queued in the same run: "is this text true?" comes first,
and "which identities is it about?" is asked by the next ingest of the confirmed
document. Structural matches still bind at once, as they always do -- the
evidence for them is structural whoever wrote the text, and a binding to
unverified knowledge counts toward nothing until the knowledge is confirmed.
The residual is harvest's: the revisions commit one document per unit and the
batch commits after them, so a run that dies between the two leaves revisions
no queue entry names, and a re-run of the unchanged document does not queue
them again.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from adopt_knowledge.documents import Document, unknown_audiences
from adopt_knowledge.matchers import IdentityView, Match, match_document
from adopt_knowledge.ports import BindingWriter, KnowledgeWriter, ReviewWriter, UnitOfWork
from adopt_model._enums import AuthorityClass, SourceType, Verification
from adopt_obs import get_logger
from adopt_scope import Scope

__all__ = [
    "INGEST_EXTRACTOR_VERSION",
    "UNVERIFIED_BATCH_PREFIX",
    "DocumentOutcome",
    "IngestReport",
    "StoredDocument",
    "run_ingest",
]

_log = get_logger("adopt_knowledge")

#: Bumped when the matchers' rules change. It travels on every binding this
#: module writes, so a later build can tell a binding made by these rules from
#: one made by their successor -- the same discipline the attribute digest
#: applies to extractor versions.
INGEST_EXTRACTOR_VERSION: Final[str] = "1"

#: What `binding_revision.extractor` records, per tier. A confirmed name match
#: is deliberately a *different* value from a structural one: "a human agreed"
#: and "the document said so outright" are different grounds, and the §9
#: promotion trigger needs to count the first without the second.
EXTRACTOR_URI: Final[str] = "ingest-structural-uri"
EXTRACTOR_PATH: Final[str] = "ingest-structural-path"
EXTRACTOR_NAME_CONFIRMED: Final[str] = "ingest-name-confirmed"
EXTRACTOR_MANUAL: Final[str] = "manual"

_EXTRACTOR_FOR_TIER: Final[Mapping[str, str]] = {
    "uri": EXTRACTOR_URI,
    "path": EXTRACTOR_PATH,
}

#: An ingested document is prose observed in the client's own artifact.
_INGEST_AUTHORITY: Final[AuthorityClass] = "artifact_observed"
_INGEST_VERIFICATION: Final[Verification] = "verified"
_INGEST_SOURCE_TYPE: Final[SourceType] = "human"

#: `--unverified`: authored, not observed, and nobody has agreed with it yet --
#: `drafting.DRAFT_AUTHORITY`'s pair, for `drafting`'s reason. `artifact_observed`
#: is a claim about where text was read from, and for text an agent wrote it
#: would be the one false field every later reader trusts.
_UNVERIFIED_AUTHORITY: Final[AuthorityClass] = "human_confirmed"
_UNVERIFIED_VERIFICATION: Final[Verification] = "unverified"

#: The `review_batch.batch_key` prefix an `--unverified` run stamps. Declared
#: here, where it is produced, and imported by `review` -- which imports this
#: module already -- so the queue's vocabulary and the producer's spelling are
#: one value rather than two strings that agree today.
UNVERIFIED_BATCH_PREFIX: Final[str] = "ingest-unverified"

CREATED: Final[str] = "created"
UPDATED: Final[str] = "updated"
UNCHANGED: Final[str] = "unchanged"


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """An already-ingested document, as idempotence needs to see it."""

    item_id: str
    path: str
    head_revision_id: str | None
    #: The body digest the last ingest recorded, from
    #: `knowledge_revision.source_version`.
    digest: str | None


@dataclass(frozen=True, slots=True)
class DocumentOutcome:
    """What happened to one document, and what it referred to."""

    path: str
    item_id: str
    status: str
    revision_id: str | None = None
    bound: tuple[Match, ...] = ()
    suggested: tuple[Match, ...] = ()
    ambiguous_paths: tuple[str, ...] = ()


@dataclass(slots=True)
class IngestReport:
    """The run, as the CLI renders it."""

    outcomes: list[DocumentOutcome] = field(default_factory=list)
    review_batch_id: str | None = None
    review_item_ids: tuple[str, ...] = ()
    unknown_audiences: tuple[str, ...] = ()
    #: Whether this run wrote its revisions `unverified` (`--unverified`).
    unverified: bool = False
    #: The `ingest-unverified:` batch holding every revision an unverified run
    #: wrote -- a second batch beside the suggestion one, because it asks a
    #: different question and confirming it does a different thing.
    verification_batch_id: str | None = None
    verification_item_ids: tuple[str, ...] = ()
    #: Name-match suggestions found on revisions this run wrote unverified, and
    #: therefore **not** queued: they are asked about the confirmed text, by the
    #: next ingest of it. Reported so a reader of the envelope sees a count that
    #: is waiting, not a count that vanished.
    suggestions_deferred: int = 0

    @property
    def created(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == CREATED)

    @property
    def updated(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == UPDATED)

    @property
    def unchanged(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == UNCHANGED)

    @property
    def bindings_created(self) -> int:
        return sum(len(outcome.bound) for outcome in self.outcomes)

    @property
    def suggestions(self) -> int:
        return sum(len(outcome.suggested) for outcome in self.outcomes)


def run_ingest(
    documents: Sequence[Document],
    *,
    scope: Scope,
    identities: Sequence[IdentityView],
    stored: Mapping[str, StoredDocument],
    knowledge: KnowledgeWriter,
    bindings: BindingWriter,
    reviews: ReviewWriter,
    unit: UnitOfWork,
    bound_pairs: frozenset[tuple[str, str]] = frozenset(),
    presented_revisions: frozenset[str] = frozenset(),
    actor_id: str | None = None,
    unverified: bool = False,
) -> IngestReport:
    """Ingest documents, bind structurally, queue the rest.

    Args:
        documents: In the order they will be written; `discover` sorts them.
        scope: Where the items land. Resolved to at least a system.
        identities: The registry the matchers see.
        stored: `path -> StoredDocument` for documents already ingested, which
            is what makes a re-run write nothing.
        bound_pairs: `(item_id, identity_id)` pairs that already have a binding.
            Checked before creating one, because `idx_binding_pair` is UNIQUE
            and a second create raises `REVISION_CHAIN_FORK` -- which would turn
            an idempotent re-ingest into a failure on the second run.
        presented_revisions: Revision ids already carried by a `review_item`.
            **This is what makes the queue idempotent**, and it is not the same
            question as whether the knowledge changed -- see below.
        unit: The transaction boundary. **One document is one unit**, not one
            run: an ingest that wrote a document's item, revision and provenance
            and then failed before its audience tag left a document a retry
            reports as `unchanged` -- so the audience was gone permanently and
            the pack that selects by audience silently omitted it (B2-02). Per
            document rather than per run because a transaction spanning forty
            files holds a write lock for the length of the ingest, and rolling
            back thirty-nine sound documents because the fortieth was unreadable
            is not atomicity anybody asked for.
        actor_id: Recorded on every revision this run writes.
        unverified: Write every revision `unverified` with authored authority,
            and queue each one for a person to confirm. See the module docstring.

    Returns:
        An `IngestReport`. **One review batch per run**, not one per document:
        v6.1 §6 B6 makes coalescing the queue's defining property, and a
        reviewer who ingested forty files should sit down once. An unverified
        run can open a second, one per question: the suggestion batch for
        documents it left unchanged, the verification batch for what it wrote.
    """
    report = IngestReport(
        unknown_audiences=unknown_audiences(documents),
        unverified=unverified,
    )
    pending_review: list[tuple[str, str | None]] = []
    pending_verification: list[tuple[str, str | None]] = []

    for document in documents:
        with unit.transaction():
            outcome = _ingest_one(
                document,
                scope=scope,
                identities=identities,
                stored=stored.get(document.path),
                knowledge=knowledge,
                bindings=bindings,
                bound_pairs=bound_pairs,
                actor_id=actor_id,
                unverified=unverified,
            )
        report.outcomes.append(outcome)
        wrote_unverified = unverified and outcome.status in (CREATED, UPDATED)
        if wrote_unverified:
            pending_verification.append((outcome.item_id, outcome.revision_id))
            report.suggestions_deferred += len(outcome.suggested)
        # Queue a document's suggestions once per *revision*. Idempotence of the
        # write path is not enough here and the difference is worth stating: a
        # re-ingest of an unchanged tree writes no knowledge and no bindings,
        # but without this check it opens a second batch holding the same
        # items -- and a queue that grows a duplicate set on every run is a
        # queue a reviewer stops opening. Keying on the revision means a
        # document whose *text* changed is presented again, which is the case
        # where there is genuinely something new to look at.
        #
        # A revision this run wrote unverified is presented for verification
        # instead, and its suggestions wait for the confirmed revision: the
        # next ingest sees that one unpresented and asks about it then.
        if (
            outcome.suggested
            and not wrote_unverified
            and outcome.revision_id not in presented_revisions
        ):
            pending_review.append((outcome.item_id, outcome.revision_id))

    if pending_verification:
        # Its own unit and its own batch, for the suggestion batch's reasons.
        with unit.transaction():
            batch_id, item_ids = reviews.open_batch(
                system_id=str(scope.system.id) if scope.system is not None else "",
                batch_key=(
                    f"{UNVERIFIED_BATCH_PREFIX}:"
                    f"{len(pending_verification)}-of-{len(report.outcomes)}"
                ),
                items=pending_verification,
                owner_actor_id=actor_id,
            )
        report.verification_batch_id = batch_id
        report.verification_item_ids = item_ids

    if pending_review:
        # Its own unit: the batch coalesces the whole run (v6.1 B6), so it
        # cannot sit inside any one document's transaction -- and a batch that
        # opened without its items would be a queue entry pointing at nothing.
        with unit.transaction():
            batch_id, item_ids = reviews.open_batch(
                system_id=str(scope.system.id) if scope.system is not None else "",
                batch_key=_batch_key(report),
                items=pending_review,
                owner_actor_id=actor_id,
            )
        report.review_batch_id = batch_id
        report.review_item_ids = item_ids

    _log.info(
        "ingest.completed",
        documents=len(report.outcomes),
        created=report.created,
        updated=report.updated,
        unchanged=report.unchanged,
        bindings=report.bindings_created,
        suggestions=report.suggestions,
        unverified=len(report.verification_item_ids),
    )
    return report


def _batch_key(report: IngestReport) -> str:
    """A stable name for what the reviewer is looking at.

    `review_batch.batch_key` is the queue's coalescing key. Naming it after the
    source (`ingest`) and the run's shape lets Build 6's change batches and
    Build 8's managed batches sit in the same table without either having to
    guess what produced the other.
    """
    suggested = sum(1 for outcome in report.outcomes if outcome.suggested)
    return f"ingest:{suggested}-of-{len(report.outcomes)}"


def _ingest_one(
    document: Document,
    *,
    scope: Scope,
    identities: Sequence[IdentityView],
    stored: StoredDocument | None,
    knowledge: KnowledgeWriter,
    bindings: BindingWriter,
    bound_pairs: frozenset[tuple[str, str]],
    actor_id: str | None,
    unverified: bool,
) -> DocumentOutcome:
    authority = _UNVERIFIED_AUTHORITY if unverified else _INGEST_AUTHORITY
    verification = _UNVERIFIED_VERIFICATION if unverified else _INGEST_VERIFICATION
    if stored is None:
        item_id, revision_id = knowledge.record(
            scope=scope,
            kind=document.kind,
            title=document.title,
            body_md=document.body_md,
            authority_class=authority,
            verification=verification,
            source_version=document.digest,
            actor_id=actor_id,
        )
        status = CREATED
    elif stored.digest == document.digest:
        # Nothing to write. The matchers still run below, because the registry
        # may have grown since the last ingest: a document that was ingested
        # before `adopt map` found the endpoint it describes should bind to it
        # now, without the operator having to edit the file to make something
        # happen.
        item_id, revision_id, status = stored.item_id, None, UNCHANGED
    else:
        item_id = stored.item_id
        revision_id = knowledge.append(
            item_id=stored.item_id,
            expected_head_id=stored.head_revision_id,
            body_md=document.body_md,
            authority_class=authority,
            verification=verification,
            source_version=document.digest,
            actor_id=actor_id,
        )
        status = UPDATED

    if revision_id is not None:
        knowledge.record_provenance(
            revision_id=revision_id,
            source_type=_INGEST_SOURCE_TYPE,
            source_ref=document.path,
        )
        for audience in document.audiences:
            knowledge.tag_audience(item_id=item_id, audience=audience)

    matched = match_document(
        document.body_md,
        identities,
        already_bound=frozenset(
            identity_id for stored_item, identity_id in bound_pairs if stored_item == item_id
        ),
    )

    bound: list[Match] = []
    for match in matched.structural:
        if (item_id, match.identity_id) in bound_pairs:
            continue
        bindings.bind(
            item_id=item_id,
            identity_id=match.identity_id,
            # v6.1 §6 B2: a structural match binds load-bearing. The column
            # defaults true for the same reason -- a writer that forgets should
            # err toward staleness, never toward false confidence.
            is_load_bearing=True,
            extractor=_EXTRACTOR_FOR_TIER[match.tier],
            extractor_version=INGEST_EXTRACTOR_VERSION,
            actor_id=actor_id,
        )
        bound.append(match)

    return DocumentOutcome(
        path=document.path,
        item_id=item_id,
        status=status,
        revision_id=revision_id
        if revision_id is not None
        else stored.head_revision_id
        if stored is not None
        else None,
        bound=tuple(bound),
        suggested=matched.suggested,
        ambiguous_paths=matched.ambiguous_paths,
    )
