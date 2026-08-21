"""Build 2's verbs: `adopt ingest`, `adopt harvest`, `adopt bind`, `adopt gaps`, `adopt review`.

**Every `adopt_knowledge` import happens inside a command body**, exactly as
`map_command` does it and for the same reason: v6.1 §2.1 requires new verbs to
register lazily so `CLI_COLD_START_MS` holds, and that budget is already over on
a developer machine. `adopt version` must not pay for a YAML parser and three
matchers it never runs.

**The human tables here carry few columns on purpose** (plan decision D8). S1.2
recorded that `--report`'s eight-column listing is unreadable at 80 columns --
`rich` truncates every field, including the URIs, which are the point. Fixing
the shared renderer is not this sprint's work, so these listings pick the
narrowest useful set and let the `--json` envelope, which is the contract, carry
everything.
"""

from pathlib import Path
from typing import Annotated, Any, Final

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store

__all__ = ["bind", "gaps", "harvest", "ingest", "review"]

PathsArgument = Annotated[
    list[Path],
    typer.Argument(help="Documents or directories to ingest. Markdown and text."),
]
ScopeOption = Annotated[
    str | None,
    typer.Option("--scope", help="firm/engagement/system/environment. Defaults to the store's."),
]
AudienceOption = Annotated[
    str | None,
    typer.Option(
        "--audience",
        help="Override the audience for every document in this run, beating frontmatter.",
    ),
]
ActorOption = Annotated[
    str | None,
    typer.Option("--actor", help="Who is running this. Recorded on every revision written."),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def ingest(
    paths: PathsArgument,
    scope: ScopeOption = None,
    audience: AudienceOption = None,
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Turn documents into knowledge, bound to the identities they refer to."""
    from adopt_knowledge import discover, run_ingest

    from adopt_cli.commands._knowledge_support import (
        bound_pairs,
        identity_views,
        presented_revisions,
        stored_documents,
    )
    from adopt_cli.commands._map_support import resolve_scope

    handle = open_configured_store(store, read_only=False)
    try:
        resolved = resolve_scope(handle, scope)
        documents = discover(paths, root=Path.cwd(), audience=audience)
        report = run_ingest(
            documents,
            scope=resolved,
            identities=identity_views(handle, resolved),
            stored=stored_documents(handle, resolved),
            knowledge=handle.items(),
            bindings=handle.bindings(),
            reviews=handle.governance(),
            bound_pairs=bound_pairs(handle),
            presented_revisions=presented_revisions(handle),
            actor_id=actor,
        )
        payload = _ingest_payload(report)
    finally:
        handle.close()

    emit(payload, as_json=json_output, title="adopt ingest")


def _ingest_payload(report: Any) -> dict[str, Any]:
    """The §14 envelope for an ingest run."""
    return {
        "documents": len(report.outcomes),
        "created": report.created,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "bindings_created": report.bindings_created,
        "suggestions": report.suggestions,
        "review_batch": report.review_batch_id,
        "review_items": list(report.review_item_ids),
        "unknown_audiences": list(report.unknown_audiences),
        "ingested": [
            {
                "path": outcome.path,
                "status": outcome.status,
                "item": outcome.item_id,
                "bound": len(outcome.bound),
                "suggested": len(outcome.suggested),
            }
            for outcome in report.outcomes
        ],
        # Flat rather than nested under each document: a binding names a URI,
        # and a URI in a column of a table that also holds a path is the
        # truncation D8 exists to avoid.
        "bindings": [
            {"path": outcome.path, "uri": match.uri, "tier": match.tier}
            for outcome in report.outcomes
            for match in outcome.bound
        ],
        "ambiguous_paths": sorted(
            {path for outcome in report.outcomes for path in outcome.ambiguous_paths}
        ),
    }


SinceOption = Annotated[
    str,
    typer.Option(
        "--since",
        help="Mine commits reachable from HEAD but not from this ref. A tag, branch or sha.",
    ),
]
RepoArgument = Annotated[
    Path,
    typer.Argument(help="The checkout whose history to mine. Defaults to the working directory."),
]
AllowNetworkOption = Annotated[
    bool,
    typer.Option(
        "--allow-network",
        help="Not implemented. Forge enrichment is deferred; harvest reads local history only.",
    ),
]


def harvest(
    since: SinceOption,
    path: RepoArgument = Path(),
    scope: ScopeOption = None,
    allow_network: AllowNetworkOption = False,
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Mine local git history into decision candidates -- unverified, with evidence."""
    from adopt_knowledge.gitlog import head_sha, read_commits
    from adopt_knowledge.harvest import batch_key, decision_record_titles, mine, run_harvest

    from adopt_cli.commands._knowledge_support import (
        bound_pairs,
        harvested_commits,
        identity_views,
    )
    from adopt_cli.commands._map_support import resolve_scope

    _refuse_network(allow_network)

    root = path.resolve()
    head = head_sha(root)
    commits = read_commits(root, since=since)
    candidates = mine(
        commits,
        decision_titles=decision_record_titles(
            root, [record for commit in commits for record in commit.files]
        ),
    )

    handle = open_configured_store(store, read_only=False)
    try:
        resolved = resolve_scope(handle, scope)
        report = run_harvest(
            candidates,
            scope=resolved,
            identities=identity_views(handle, resolved),
            known=harvested_commits(handle, resolved),
            knowledge=handle.items(),
            bindings=handle.bindings(),
            reviews=handle.governance(),
            key=batch_key(since, head),
            bound_pairs=bound_pairs(handle),
            actor_id=actor,
        )
        payload = _harvest_payload(report, since=since, head=head, commits=len(commits))
    finally:
        handle.close()

    emit(payload, as_json=json_output, title="adopt harvest")


def _refuse_network(allow_network: bool) -> None:
    """`--allow-network` is declared and refused, rather than absent.

    v6.1 §6 F7 names forge enrichment as the one networked half of harvest, and
    the plan defers it behind a named trigger. Declaring the flag and refusing
    it beats omitting it: an operator who read the architecture and typed it
    gets a sentence naming the deferral, where an unknown-option error would
    read as their mistake. **The refusal is the offline default speaking**, and
    it is the same posture `adopt map` takes -- nothing in Build 2 opens a
    socket.
    """
    if not allow_network:
        return
    from adopt_obs import AdoptError, ErrorCode

    raise AdoptError(
        ErrorCode.ADOPT_OFFLINE_DENIED,
        message="--allow-network is not implemented for harvest",
        hint="Harvest mines local history only (v6.1 §6 F7). Forge enrichment -- pull "
        "request bodies and review threads -- is deferred until a real engagement's "
        "decision history is unreachable locally. Fetch the branches you want mined "
        "and re-run without the flag.",
    )


def _harvest_payload(report: Any, *, since: str, head: str, commits: int) -> dict[str, Any]:
    """The §14 envelope for a harvest run."""
    return {
        "since": since,
        "head": head,
        "commits_read": commits,
        "candidates": len(report.candidates),
        "created": len(report.created),
        "already_known": len(report.known),
        "bindings_created": len(report.bound),
        "review_batch": report.review_batch_id,
        "review_items": list(report.review_item_ids),
        "ambiguous_paths": list(report.ambiguous_paths),
        # Signals rather than bodies: what a reader needs is why each commit
        # qualified, and a commit message in a table is the truncation D8 exists
        # to avoid. The bodies are in the store, under review.
        "mined": [
            {
                "sha": candidate.sha,
                "title": candidate.title,
                "signals": list(candidate.signal_names),
                "files": len(candidate.files),
            }
            for candidate in report.candidates
        ],
        "bindings": [{"uri": match.uri, "evidence": match.evidence} for match in report.bound],
    }


UriArgument = Annotated[str, typer.Argument(help="The identity URI to bind to.")]
ItemArgument = Annotated[str, typer.Argument(help="The knowledge item id.")]
NotLoadBearingOption = Annotated[
    bool,
    typer.Option(
        "--not-load-bearing",
        help="A change to this identity does not stale the item. The default is "
        "load-bearing, so a caller who says nothing errs toward staleness.",
    ),
]


def bind(
    knowledge_id: ItemArgument,
    uri: UriArgument,
    not_load_bearing: NotLoadBearingOption = False,
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Bind a knowledge item to an identity by hand.

    For the links no heuristic finds. A binding made here is human-justified by
    construction, which is the same standing a confirmed suggestion has.
    """
    from adopt_knowledge.ingest import EXTRACTOR_MANUAL, INGEST_EXTRACTOR_VERSION

    from adopt_cli.commands._knowledge_support import resolve_identity
    from adopt_obs import AdoptError, ErrorCode

    handle = open_configured_store(store, read_only=False)
    try:
        item = handle.items().get(knowledge_id)
        if item is None:
            raise AdoptError(
                ErrorCode.BIND_TARGET_NOT_FOUND,
                message=f"no knowledge item {knowledge_id!r}",
                hint="Run `adopt ingest` first, or take the id from its output. Item ids "
                "are minted by the store and never by a caller.",
            )
        identity = resolve_identity(handle, uri)
        if identity is None:
            raise AdoptError(
                ErrorCode.BIND_TARGET_NOT_FOUND,
                message=f"no identity at {uri!r}",
                hint="Run `adopt map` first. A moved identity's old URI still resolves, so "
                "this is genuine absence rather than a stale address.",
            )
        binding_id, revision_id = handle.bindings().bind(
            item_id=knowledge_id,
            identity_id=identity.id,
            is_load_bearing=not not_load_bearing,
            extractor=EXTRACTOR_MANUAL,
            extractor_version=INGEST_EXTRACTOR_VERSION,
            actor_id=actor,
        )
        payload = {
            "binding": binding_id,
            "revision": revision_id,
            "item": knowledge_id,
            "identity": identity.id,
            "uri": identity.uri,
            "is_load_bearing": not not_load_bearing,
            # Stated because the alias is silent otherwise: binding to a moved
            # identity's old address is correct and surprising, and the operator
            # should see which referent they actually bound.
            "resolved_from": uri,
        }
    finally:
        handle.close()

    emit(payload, as_json=json_output, title="adopt bind")


def gaps(
    scope: ScopeOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Identities minus covered knowledge, ranked -- the elicitation queue.

    Read-only. `recompute_coverage` is the authority on whether an identity is
    covered and nothing here writes its cache; dispositions arrive in Build 4.
    """
    from adopt_knowledge import rank_gaps

    from adopt_cli.commands._map_support import resolve_scope
    from adopt_coverage import recompute_coverage

    handle = open_configured_store(store)
    try:
        resolved = resolve_scope(handle, scope)
        if resolved.system is None:
            ranked: tuple[Any, ...] = ()
            result = None
        else:
            result = recompute_coverage(
                handle.coverage_records(),
                str(resolved.system.id),
                str(resolved.environment.id) if resolved.environment is not None else None,
            )
            ranked = rank_gaps(result.identities)
        payload = _gaps_payload(result, ranked)
    finally:
        handle.close()

    emit(payload, as_json=json_output, title="adopt gaps")


def _gaps_payload(result: Any, ranked: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "identities": 0 if result is None else len(result.identities),
        "covered": 0 if result is None else result.covered,
        "uncovered": 0 if result is None else result.uncovered,
        "gaps": [
            {"kind": gap.kind, "uri": gap.uri, "reasons": ", ".join(gap.reasons)} for gap in ranked
        ],
    }


ConfirmOption = Annotated[
    str | None, typer.Option("--confirm", help="Confirm one review item by id.")
]
RejectOption = Annotated[str | None, typer.Option("--reject", help="Reject one review item by id.")]
EditOption = Annotated[
    str | None,
    typer.Option("--edit", help="Correct one review item by id. Needs --file."),
]
EditFileOption = Annotated[
    Path | None,
    typer.Option("--file", help="The corrected body for --edit. Markdown or text."),
]
ConfirmBatchOption = Annotated[
    str | None,
    typer.Option(
        "--confirm-batch",
        help="Confirm every open item in one batch -- the per-document batch confirm.",
    ),
]

CONFIRM: Final[str] = "confirmed"
CORRECT: Final[str] = "corrected"
REJECT: Final[str] = "rejected"


def review(
    confirm_item: ConfirmOption = None,
    reject_item: RejectOption = None,
    edit_item: EditOption = None,
    file: EditFileOption = None,
    confirm_batch: ConfirmBatchOption = None,
    scope: ScopeOption = None,
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """The one review queue: harvest candidates and suggested bindings, together.

    With no flag it lists. With one it resolves. What confirming *does* depends
    on which population the item belongs to -- bindings for a suggestion, a
    verified revision for a candidate -- and `adopt_knowledge.review` is where
    that rule lives and is documented.
    """
    from adopt_knowledge import confirm as confirm_pending
    from adopt_knowledge import edit as edit_pending
    from adopt_knowledge import reject as reject_pending

    from adopt_cli.commands._knowledge_support import (
        bound_pairs,
        identity_views,
        known_review_items,
        pending_items,
    )
    from adopt_cli.commands._map_support import resolve_scope

    writing = bool(confirm_item or reject_item or edit_item or confirm_batch)
    body_md = _edit_body(edit_item, file)
    handle = open_configured_store(store, read_only=not writing)
    try:
        resolved = resolve_scope(handle, scope)
        identities = identity_views(handle, resolved)
        pending = pending_items(handle, resolved, identities)

        if not writing:
            payload = _queue_payload(pending)
        else:
            targets = _targets(
                pending,
                known_review_items(handle),
                confirm_item,
                reject_item,
                edit_item,
                confirm_batch,
            )
            resolutions: list[dict[str, Any]] = []
            for item, action in targets:
                if action == REJECT:
                    outcome = reject_pending(item, reviews=handle.governance())
                elif action == CORRECT:
                    outcome = edit_pending(
                        item,
                        reviews=handle.governance(),
                        knowledge=handle.items(),
                        body_md=body_md,
                        source_ref=str(file),
                        actor_id=actor,
                    )
                else:
                    outcome = confirm_pending(
                        item,
                        reviews=handle.governance(),
                        bindings=handle.bindings(),
                        knowledge=handle.items(),
                        bound_pairs=bound_pairs(handle),
                        actor_id=actor,
                    )
                resolutions.append(
                    {
                        "review_item": item.review_item_id,
                        "action": outcome.resolution,
                        "source": item.source,
                        "bindings": len(outcome.bindings),
                        "revision": outcome.revision_id,
                    }
                )
            payload = {"resolved": len(resolutions), "resolutions": resolutions}
    finally:
        handle.close()

    emit(payload, as_json=json_output, title="adopt review")


def _edit_body(edit_item: str | None, file: Path | None) -> str:
    """The corrected text, read **before** the store is opened.

    Read first on purpose: an unreadable `--file` must refuse before anything is
    stamped, or the queue records a correction whose text never arrived. The
    ingest refusal code is reused because it is exactly the same sentence about
    exactly the same kind of input -- a document path the operator named that
    cannot be read.
    """
    from adopt_obs import AdoptError, ErrorCode

    if edit_item and file is None:
        raise AdoptError(
            ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE,
            message="--edit needs --file",
            hint="Write the corrected body to a file and pass it. The text is a knowledge "
            "revision, so it is supplied as a document rather than typed at a prompt.",
        )
    if file is None:
        return ""
    if edit_item is None:
        raise AdoptError(
            ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE,
            message="--file has no effect without --edit",
            hint="Pass --edit <review-item-id> with it. A file supplied to a confirm or a "
            "reject would be silently ignored, which is how a correction gets lost.",
        )
    try:
        return file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise AdoptError(
            ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE,
            message=f"{str(file)!r} could not be read as UTF-8 text",
            hint="A correction is refused rather than recorded empty: an item stamped "
            "`corrected` whose revision says nothing is worse than an unresolved one.",
        ) from error


def _targets(
    pending: list[Any],
    known: dict[str, str | None],
    confirm_item: str | None,
    reject_item: str | None,
    edit_item: str | None,
    confirm_batch: str | None,
) -> list[tuple[Any, str]]:
    """Which pending items an invocation acts on, and how.

    Raises:
        AdoptError: ``REVIEW_ITEM_RESOLVED`` when the id names an item that was
            already decided, and ``REVIEW_ITEM_NOT_FOUND`` when it names nothing
            at all. **Two sentences, deliberately**, and the distinction is
            checked here rather than left to the facade: the queue lookup holds
            only open items, so a resolved id would otherwise be reported as
            absent -- sending an operator whose id was perfectly correct to look
            for a typo. The facade keeps its own guard for library callers.
    """
    from adopt_obs import AdoptError, ErrorCode

    by_id = {item.review_item_id: item for item in pending}

    for candidate in (confirm_item, reject_item, edit_item):
        if candidate and candidate not in by_id and candidate in known:
            raise AdoptError(
                ErrorCode.REVIEW_ITEM_RESOLVED,
                message=f"review item {candidate} is already {known[candidate]}",
                hint="A disposition is recorded once. Re-reviewing the same subject means "
                "a new item in a new batch, so the queue keeps what was decided and "
                "when.",
            )

    if confirm_batch:
        selected = [item for item in pending if item.review_batch_id == confirm_batch]
        if not selected:
            raise AdoptError(
                ErrorCode.REVIEW_ITEM_NOT_FOUND,
                message=f"no open items in batch {confirm_batch!r}",
                hint="Run `adopt review` to list open batches. A batch whose items are all "
                "resolved is closed and is not listed.",
            )
        return [(item, CONFIRM) for item in selected]

    target_id = confirm_item or reject_item or edit_item
    item = by_id.get(target_id or "")
    if item is None:
        raise AdoptError(
            ErrorCode.REVIEW_ITEM_NOT_FOUND,
            message=f"no open review item {target_id!r}",
            hint="Run `adopt review` to list the open queue.",
        )
    if confirm_item:
        return [(item, CONFIRM)]
    return [(item, CORRECT if edit_item else REJECT)]


def _queue_payload(pending: list[Any]) -> dict[str, Any]:
    """The queue, with each population carrying what makes it reviewable.

    A candidate's `evidence` is its `provenance` rows -- the commit sha, and any
    decision record the commit touched. A suggestion's is its matched URIs. Both
    are listed flat rather than nested for D8's reason: a URI sharing a row with
    a title is a URI `rich` truncates, and the URI is the point.
    """
    return {
        "open_items": len(pending),
        "batches": sorted({item.review_batch_id for item in pending}),
        "candidates": sum(1 for item in pending if item.is_candidate),
        "suggested_items": sum(1 for item in pending if not item.is_candidate),
        "queue": [
            {
                "review_item": item.review_item_id,
                "source": item.source,
                "title": item.title,
                "suggested": len(item.suggestions),
                "evidence": len(item.evidence),
            }
            for item in pending
        ],
        "suggestions": [
            {
                "review_item": item.review_item_id,
                "uri": match.uri,
                "evidence": match.evidence,
            }
            for item in pending
            for match in item.suggestions
        ],
        "evidence": [
            {
                "review_item": item.review_item_id,
                "source_type": source_type,
                "source_ref": source_ref,
            }
            for item in pending
            for source_type, source_ref in item.evidence
        ],
    }
