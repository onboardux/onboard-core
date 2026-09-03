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

import datetime as _dt
from pathlib import Path
from typing import Annotated, Any, Final

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store
from adopt_obs import AdoptError, ErrorCode, format_timestamp

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


AckOption = Annotated[
    str | None,
    typer.Option("--ack", help="Acknowledge one gap by its gap-key: someone owns closing it."),
]
ResolveGapOption = Annotated[
    str | None,
    typer.Option("--resolve", help="Mark one gap resolved by its gap-key."),
]
WaiveOption = Annotated[
    str | None,
    typer.Option("--waive", help="Waive one gap by its gap-key. Requires --until."),
]
UntilOption = Annotated[
    str | None,
    typer.Option("--until", help="Expiry for --waive, as YYYY-MM-DD. Mandatory on a waiver."),
]
OwnerOption = Annotated[
    str | None, typer.Option("--owner", help="Who owns closing the gap being dispositioned.")
]
NoteOption = Annotated[str | None, typer.Option("--note", help="Why, in the reviewer's words.")]

#: `--ack` / `--resolve` / `--waive` -> the `gap_status` value each records.
_DISPOSITIONS: Final[tuple[tuple[str, str], ...]] = (
    ("ack", "acknowledged"),
    ("resolve", "resolved"),
    ("waive", "waived"),
)


def gaps(
    scope: ScopeOption = None,
    ack: AckOption = None,
    resolve_gap: ResolveGapOption = None,
    waive: WaiveOption = None,
    until: UntilOption = None,
    owner: OwnerOption = None,
    note: NoteOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Identities minus covered knowledge, ranked -- the elicitation queue.

    With no flag it lists. With one disposition flag it records what a human
    decided about a single gap and lists the result.

    **Open conflicts are listed alongside the gaps**, and they are a different
    thing: a gap is knowledge nobody has written, a conflict is knowledge
    somebody confirmed that a probe has since seen contradicted. Both belong in
    the same queue because both are work, and separating them would let a
    contradiction sit unread behind a heading nobody opened.

    **Existence stays derived, always.** `recompute_coverage` is the authority
    on whether an identity is uncovered, and nothing here writes its cache. A
    disposition row says only what someone decided to do; the report is the join
    of the two, so a gap that recompute no longer derives disappears from the
    listing whatever its disposition says (v6.1 §6 Build 4).
    """
    from adopt_knowledge import rank_conflicts, rank_gaps

    from adopt_cli.commands._map_support import resolve_scope
    from adopt_coverage import recompute_coverage

    requested = {"ack": ack, "resolve": resolve_gap, "waive": waive}
    chosen = [(flag, status) for flag, status in _DISPOSITIONS if requested[flag] is not None]
    if len(chosen) > 1:
        raise AdoptError(
            ErrorCode.GAP_NOT_FOUND,
            message="pass one of --ack, --resolve or --waive, not several",
            hint="A gap holds one disposition at a time. Recording two in one command "
            "would leave which of them applied depending on argument order.",
        )

    # Read-only when listing, writable only when a disposition was asked for --
    # `adopt review`'s pattern. Listing gaps must never need write access: an
    # FDE reading the queue against a store they hold read-only is the normal
    # case, not an error.
    handle = open_configured_store(store, read_only=not chosen)
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

        disposed: dict[str, Any] | None = None
        if chosen:
            _, status = chosen[0]
            key = requested[chosen[0][0]]
            assert key is not None  # noqa: S101 -- `chosen` is built from non-None values
            disposed = _dispose(
                handle,
                ranked,
                gap_key=key,
                status=status,
                owner=owner,
                note=note,
                until=until,
            )

        payload = _gaps_payload(result, ranked, handle.governance().gap_dispositions())
        payload["conflicts"] = _conflicts_payload(handle, result, rank_conflicts)
        if disposed is not None:
            payload["disposed"] = disposed
    finally:
        handle.close()

    emit(payload, as_json=json_output, title="adopt gaps")


def _dispose(
    handle: Any,
    ranked: tuple[Any, ...],
    *,
    gap_key: str,
    status: str,
    owner: str | None,
    note: str | None,
    until: str | None,
) -> dict[str, Any]:
    """Record one disposition, refusing a key the current recompute did not derive.

    The membership check is what keeps existence derived: a disposition accepted
    for a `gap_key` nothing produces would sit in the table forever, invisible
    to a report that joins onto derived gaps.
    """
    match = next((gap for gap in ranked if gap.gap_key == gap_key), None)
    if match is None:
        raise AdoptError(
            ErrorCode.GAP_NOT_FOUND,
            message=f"no open gap with key {gap_key!r} in this scope",
            hint="Run `adopt gaps` and copy a key from the listing. A gap that has since "
            "been covered is no longer a gap, and its key stops being dispositionable "
            "the moment confirmed knowledge is bound to the identity.",
        )

    row = handle.governance().dispose_gap(
        gap_key=gap_key,
        identity_id=match.identity_id,
        status=status,
        owner_actor_id=owner,
        note=note,
        waived_until=_parse_until(until),
    )
    return {
        "gap_key": row.gap_key,
        "status": row.status,
        "owner": row.owner_actor_id,
        "note": row.note,
        "waived_until": format_timestamp(row.waived_until) if row.waived_until else None,
    }


def _conflicts_payload(handle: Any, result: Any, ranker: Any) -> list[dict[str, Any]]:
    """Open conflicts for the identities the recompute evaluated.

    Read through `table_rows` -- Build 4's report pattern -- so surfacing Bet 4's
    deliverable adds no query path to any realized port. Scoped by the identities
    coverage just evaluated, so a conflict recorded against another system cannot
    appear in this scope's queue.
    """
    from adopt_model import Conflict

    if result is None:
        return []
    uris = {row.identity_id: row.uri for row in result.identities}
    rows = handle.export_records().table_rows("conflict", Conflict)
    return [
        {
            "uri": conflict.uri,
            "kind": conflict.kind,
            "intent_revision": conflict.intent_revision_id,
            "detected_at": format_timestamp(conflict.detected_at),
            # Always `open` -- the ranker returns nothing else. Carried anyway so
            # the shape does not change on the day a disposition write path
            # exists, and so a reader of the JSON never has to assume.
            "disposition": "open",
        }
        for conflict in ranker(rows, uris)
    ]


def _parse_until(value: str | None) -> _dt.datetime | None:
    """`YYYY-MM-DD` as an instant, or `None`.

    Midnight UTC rather than local: a waiver's expiry is compared against store
    timestamps, which are UTC, and a date that meant something different
    depending on who typed it would expire waivers early for half a team.
    """
    if value is None:
        return None
    try:
        parsed = _dt.datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise AdoptError(
            ErrorCode.GAP_WAIVER_NEEDS_UNTIL,
            message=f"--until {value!r} is not a date",
            hint="Write it as YYYY-MM-DD, for example 2026-12-31.",
        ) from error
    return parsed.replace(tzinfo=_dt.UTC)


def _gaps_payload(
    result: Any, ranked: tuple[Any, ...], dispositions: dict[str, Any]
) -> dict[str, Any]:
    return {
        "identities": 0 if result is None else len(result.identities),
        "covered": 0 if result is None else result.covered,
        "uncovered": 0 if result is None else result.uncovered,
        "gaps": [
            {
                "kind": gap.kind,
                "uri": gap.uri,
                "reasons": ", ".join(gap.reasons),
                "gap_key": gap.gap_key,
                # An undisposed gap is `open`: the recompute derived it and
                # nobody has decided anything yet, which is what `open` means.
                "status": (
                    dispositions[gap.gap_key].status if gap.gap_key in dispositions else "open"
                ),
                "owner": (
                    dispositions[gap.gap_key].owner_actor_id
                    if gap.gap_key in dispositions
                    else None
                ),
            }
            for gap in ranked
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
ResolveOption = Annotated[
    str | None,
    typer.Option("--resolve", help="Resolve one refresh change item by id. Needs --action."),
]
ActionOption = Annotated[
    str | None,
    typer.Option(
        "--action",
        help="What the change means for the item: retire | rebind | confirm-current.",
    ),
]
ToOption = Annotated[
    str | None,
    typer.Option(
        "--to",
        help="The successor identity URI for --action rebind. Defaults to the recorded "
        "alias when the referent moved; required otherwise.",
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
    resolve_item: ResolveOption = None,
    action: ActionOption = None,
    to_uri: ToOption = None,
    scope: ScopeOption = None,
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """The one review queue: harvest candidates, suggested bindings and changes.

    With no flag it lists. With one it resolves. What confirming *does* depends
    on which population the item belongs to -- bindings for a suggestion, a
    verified revision for a candidate -- and `adopt_knowledge.review` is where
    that rule lives and is documented.

    **`--resolve --action` is the change population's separate door, and the
    separation is deliberate.** `--confirm` on a suggestion answers "is this
    document about this identity?"; the three change actions answer "the
    referent moved -- what should this note do about it?". One flag meaning both
    is how a reviewer presses a button for one reason and gets a second thing
    they never looked at, which is the failure the two-populations rule in
    `adopt_knowledge.review` exists to prevent.
    """
    from adopt_knowledge import confirm as confirm_pending
    from adopt_knowledge import edit as edit_pending
    from adopt_knowledge import reject as reject_pending

    from adopt_cli.commands._knowledge_support import (
        bound_pairs,
        identity_views,
        known_review_items,
        pending_items,
        refresh_population,
    )
    from adopt_cli.commands._map_support import resolve_scope
    from adopt_cli.commands._remote_support import configured_remote

    _check_resolve_flags(resolve_item, action, to_uri)
    writing = bool(confirm_item or reject_item or edit_item or confirm_batch or resolve_item)
    body_md = _edit_body(edit_item, file)

    # **The remote check happens before the store is opened**, and that ordering
    # is the point: an operated store is a read replica, so opening it writable
    # to resolve an item would be the local write R9 forbids -- and the write
    # would succeed, then vanish at the next `adopt pull`.
    remote = configured_remote()
    if remote is not None:
        emit(
            _remote_review(
                remote,
                confirm_item=confirm_item,
                reject_item=reject_item,
                edit_item=edit_item,
                confirm_batch=confirm_batch,
                resolve_item=resolve_item,
                action=action,
                to_uri=to_uri,
                body_md=body_md,
                actor=actor,
            ),
            as_json=json_output,
            title="adopt review",
        )
        return
    handle = open_configured_store(store, read_only=not writing)
    try:
        resolved = resolve_scope(handle, scope)
        identities = identity_views(handle, resolved)
        pending = pending_items(handle, resolved, identities)

        if resolve_item:
            payload = _resolve_change(
                handle,
                pending,
                known_review_items(handle),
                review_item_id=resolve_item,
                action=action or "",
                to_uri=to_uri,
                actor=actor,
            )
        elif not writing:
            payload = _queue_payload(pending)
            payload.update(_refresh_payload(refresh_population(handle, resolved, pending)))
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


def _remote_review(
    remote: Any,
    *,
    confirm_item: str | None,
    reject_item: str | None,
    edit_item: str | None,
    confirm_batch: str | None,
    resolve_item: str | None,
    action: str | None,
    to_uri: str | None,
    body_md: str,
    actor: str | None,
) -> dict[str, Any]:
    """List or resolve against the plane. One entry per invocation.

    **`--confirm-batch` is refused rather than looped**, and refusing is the
    honest answer rather than a gap. Locally it is one transaction over one
    document's suggestions; over HTTP it would be N requests, and a failure at
    request four leaves four entries resolved and the rest not -- a partial
    batch confirm with no record of where it stopped. The plane offers no batch
    endpoint, so the CLI does not invent one out of a loop.
    """
    from adopt_cli.commands._review_remote import remote_queue, resolve_remote_item
    from adopt_obs import AdoptError, ErrorCode

    if confirm_batch:
        raise AdoptError(
            ErrorCode.REVIEW_ITEM_NOT_FOUND,
            message="--confirm-batch has no operated equivalent",
            hint="Resolve the batch's entries one at a time. A batch confirm over the "
            "network is N requests, and a failure part way through leaves half the "
            "batch decided with nothing recording where it stopped.",
        )

    targets = {
        "confirm": confirm_item,
        "reject": reject_item,
        "edit": edit_item,
    }
    named = [(name, value) for name, value in targets.items() if value]
    if not named and not resolve_item:
        return {"remote": remote.url, **remote_queue(remote)}

    if len(named) + (1 if resolve_item else 0) > 1:
        raise AdoptError(
            ErrorCode.REVIEW_ITEM_NOT_FOUND,
            message="one resolution per invocation against an operated plane",
            hint="Each resolution is its own transaction on the plane. Sending two in "
            "one command would make a partial failure unreportable.",
        )

    if not actor:
        raise AdoptError(
            ErrorCode.REVIEW_ITEM_NOT_FOUND,
            message="--actor names the person resolving this, and is required by the plane",
            hint="A review disposition is the record that a human looked. The token "
            "names the integration, not the reviewer, so the plane will not accept a "
            "resolution that names nobody.",
        )

    if resolve_item:
        return {
            "remote": remote.url,
            **resolve_remote_item(
                remote,
                review_item_id=resolve_item,
                action=action or "",
                actor=actor,
                to_uri=to_uri,
            ),
        }

    verb, item_id = named[0]
    return {
        "remote": remote.url,
        **resolve_remote_item(
            remote,
            review_item_id=item_id or "",
            action=verb,
            actor=actor,
            body_md=body_md if verb == "edit" else None,
        ),
    }


def _check_resolve_flags(resolve_item: str | None, action: str | None, to_uri: str | None) -> None:
    """Refuse an incoherent flag combination before the store is opened.

    **`AdoptError`, not `typer.BadParameter`, and that is a measured choice
    rather than a preference.** The installed typer vendors its own click under
    `typer._click`, so a `typer.BadParameter` raised in a command body is not a
    `click.ClickException` and never reaches `main._exit_code_of` -- it escapes
    as an unhandled exception, exits `1` instead of contracts §13's `2`, and
    renders a rich panel where a `--json` caller was promised the one error
    envelope. Verified in this tree, not assumed.

    **This is now a rule rather than a habit** (CR-79):
    `tests/unit/test_cli_usage_errors.py` refuses any click or typer parser
    exception raised anywhere under `packages/adopt-cli/src`, and
    `scripts/plant_violation.py --kind bad-parameter` watches it failing. The
    note below was written here and read by nobody -- `commands/pack.py`, one
    directory away, acquired a third such raise afterwards.

    The code is `REVIEW_ITEM_NOT_FOUND` for all three refusals, on `_edit_body`'s
    precedent in this same file: reuse the registered code whose subject matches
    -- here, a `--resolve` invocation that cannot be carried out -- and let the
    message name the flags. Build 6 registers no new error code (plan D14).
    """
    from adopt_knowledge import ACTIONS

    from adopt_obs import AdoptError, ErrorCode

    def refuse(message: str, hint: str) -> AdoptError:
        return AdoptError(ErrorCode.REVIEW_ITEM_NOT_FOUND, message=message, hint=hint)

    if action and not resolve_item:
        raise refuse(
            "--action needs --resolve <review-item>",
            "An action with nothing named to act on would resolve whatever the queue "
            "happened to list first.",
        )
    if to_uri and not resolve_item:
        raise refuse(
            "--to has no effect without --resolve --action rebind",
            "Pass the review item and the action it takes. A target with no rebind is a "
            "binding nobody asked for.",
        )
    if resolve_item and not action:
        raise refuse(
            "--resolve needs --action: " + " | ".join(ACTIONS),
            "There is no default: retiring a note and re-pointing it are opposite "
            "decisions, and one of them cannot be undone by appending.",
        )
    if action and action not in ACTIONS:
        raise refuse(
            f"--action {action!r} is not one of: " + " | ".join(ACTIONS),
            "The three actions are what a reviewer can say about a changed referent. "
            "A suggestion or a candidate is answered with --confirm, --reject or --edit.",
        )


def _resolve_change(
    handle: Any,
    pending: list[Any],
    known: dict[str, str | None],
    *,
    review_item_id: str,
    action: str,
    to_uri: str | None,
    actor: str | None,
) -> dict[str, Any]:
    """Run one change action and render what it did to the store.

    The item is looked up in the **open** queue and its causes are read in the
    same breath, so the links an action supersedes or re-affirms are the ones
    the listing showed. `adopt_knowledge.changes` owns every decision from here;
    this function is composition -- which is why the honest `confirm-current`
    sentence is built from `still_stale` rather than restated.
    """
    from adopt_knowledge import (
        ACTION_CONFIRM_CURRENT,
        ACTION_REBIND,
        ACTION_RETIRE,
        confirm_current_item,
        rebind_item,
        retire_item,
    )

    from adopt_cli.commands._knowledge_support import changed_bindings, rebind_target

    item = _change_target(pending, known, review_item_id)
    affected = changed_bindings(handle, item)

    if action == ACTION_RETIRE:
        outcome = retire_item(
            item, reviews=handle.governance(), knowledge=handle.items(), actor_id=actor
        )
        target_uri = None
    elif action == ACTION_REBIND:
        target_id, target_uri = rebind_target(handle, affected, to_uri)
        outcome = rebind_item(
            item,
            reviews=handle.governance(),
            bindings=handle.bindings(),
            affected=affected,
            target_identity_id=target_id,
            target_uri=target_uri,
            actor_id=actor,
        )
    else:
        outcome = confirm_current_item(
            item,
            reviews=handle.governance(),
            knowledge=handle.items(),
            freshener=handle.changes(),
            affected=affected,
            actor_id=actor,
        )
        target_uri = None

    return {
        "resolved": 1,
        "resolutions": [
            {
                "review_item": item.review_item_id,
                "action": outcome.action,
                "resolution": outcome.resolution,
                "source": item.source,
                "item": item.item_id,
                "revision": outcome.revision_id,
                "superseded_bindings": list(outcome.superseded_bindings),
                "new_binding": outcome.new_binding_id,
                "rebound_to": target_uri,
                "freshened_bindings": list(outcome.freshened_bindings),
                # The honest half. An item that stays STALE after a confirmation
                # needs the reason and the way out on screen, or the reviewer
                # concludes the freshness state is broken.
                "still_stale": list(outcome.still_stale),
                "note": _CONFIRM_STILL_STALE_NOTE
                if outcome.action == ACTION_CONFIRM_CURRENT and outcome.still_stale
                else None,
            }
        ],
    }


#: Said when `confirm-current` cannot clear a cause. Not a failure: the referent
#: really is gone, and the two actions that help are named rather than left for
#: the reviewer to rediscover by watching `adopt ask` keep saying STALE.
_CONFIRM_STILL_STALE_NOTE: Final[str] = (
    "the note is confirmed, but these referents are dead or moved, so the item stays "
    "STALE by the source rule -- use --action rebind --to <uri>, or --action retire"
)


def _change_target(pending: list[Any], known: dict[str, str | None], review_item_id: str) -> Any:
    """The open change item that id names.

    Raises:
        AdoptError: ``REVIEW_ITEM_RESOLVED`` when it was already decided and
            ``REVIEW_ITEM_NOT_FOUND`` when it names nothing -- the same two
            sentences `_targets` distinguishes, for the same reason. The
            wrong-population refusal belongs to `adopt_knowledge.changes`, which
            is where the rule about which actions apply to which population is
            written down.
    """
    from adopt_obs import AdoptError, ErrorCode

    for item in pending:
        if item.review_item_id == review_item_id:
            return item
    if review_item_id in known:
        raise AdoptError(
            ErrorCode.REVIEW_ITEM_RESOLVED,
            message=f"review item {review_item_id} is already {known[review_item_id]}",
            hint="A disposition is recorded once. Re-reviewing the same subject means a "
            "new item in a new batch, so the queue keeps what was decided and when.",
        )
    raise AdoptError(
        ErrorCode.REVIEW_ITEM_NOT_FOUND,
        message=f"no open review item {review_item_id!r}",
        hint="Run `adopt review` to list the open queue.",
    )


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


def _refresh_payload(population: dict[str, Any]) -> dict[str, Any]:
    """Build 6's two halves of the queue, flat for the same reason the rest is.

    `causes` explains why a queued item is queued -- one row per (item, changed
    referent), because a note bound to three rebased endpoints has three reasons
    and a reviewer needs all of them. `informational` is everything recorded
    that no human is being asked to act on: new referents, cosmetic edits, and
    changes to referents nobody has written about. It is listed rather than
    counted because "3 informational" tells a reviewer nothing they can check.
    """
    return {
        "causes": [
            {"review_item": review_item_id, **cause}
            for review_item_id, entries in sorted(population["causes"].items())
            for cause in entries
        ],
        "informational": population["informational"],
    }


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
                # The knowledge item the entry is about. Two notes can share a
                # title -- `adopt ingest` takes it from the document's first
                # heading -- so a listing that named only the title would leave
                # a reader unable to tell which of them an entry belongs to.
                "item": item.item_id,
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
