"""Build 2's verbs: `adopt ingest`, `adopt bind`, `adopt gaps`, `adopt review`.

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
from typing import Annotated, Any

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store

__all__ = ["bind", "gaps", "ingest", "review"]

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
ConfirmBatchOption = Annotated[
    str | None,
    typer.Option(
        "--confirm-batch",
        help="Confirm every open item in one batch -- the per-document batch confirm.",
    ),
]


def review(
    confirm_item: ConfirmOption = None,
    reject_item: RejectOption = None,
    confirm_batch: ConfirmBatchOption = None,
    scope: ScopeOption = None,
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """The one review queue: suggested bindings now, change items from Build 6.

    With no flag it lists. With one it resolves. Confirming creates the
    bindings the suggestion proposed; rejecting writes nothing but the
    disposition.
    """
    from adopt_knowledge import confirm as confirm_pending
    from adopt_knowledge import reject as reject_pending

    from adopt_cli.commands._knowledge_support import (
        bound_pairs,
        identity_views,
        known_review_items,
        pending_items,
    )
    from adopt_cli.commands._map_support import resolve_scope

    writing = bool(confirm_item or reject_item or confirm_batch)
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
                confirm_batch,
            )
            resolutions: list[dict[str, Any]] = []
            for item, action in targets:
                if action == "rejected":
                    reject_pending(item, reviews=handle.governance())
                    resolutions.append(
                        {"review_item": item.review_item_id, "action": action, "bindings": 0}
                    )
                    continue
                created = confirm_pending(
                    item,
                    reviews=handle.governance(),
                    bindings=handle.bindings(),
                    bound_pairs=bound_pairs(handle),
                    actor_id=actor,
                )
                resolutions.append(
                    {
                        "review_item": item.review_item_id,
                        "action": action,
                        "bindings": len(created),
                    }
                )
            payload = {"resolved": len(resolutions), "resolutions": resolutions}
    finally:
        handle.close()

    emit(payload, as_json=json_output, title="adopt review")


def _targets(
    pending: list[Any],
    known: dict[str, str | None],
    confirm_item: str | None,
    reject_item: str | None,
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

    for candidate in (confirm_item, reject_item):
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
        return [(item, "confirmed") for item in selected]

    target_id = confirm_item or reject_item
    item = by_id.get(target_id or "")
    if item is None:
        raise AdoptError(
            ErrorCode.REVIEW_ITEM_NOT_FOUND,
            message=f"no open review item {target_id!r}",
            hint="Run `adopt review` to list the open queue.",
        )
    return [(item, "confirmed" if confirm_item else "rejected")]


def _queue_payload(pending: list[Any]) -> dict[str, Any]:
    return {
        "open_items": len(pending),
        "batches": sorted({item.review_batch_id for item in pending}),
        "queue": [
            {
                "review_item": item.review_item_id,
                "title": item.title,
                "suggested": len(item.suggestions),
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
    }
