"""`adopt refresh` -- Build 6's verb: what changed, what it means, who must look.

**Exit 4 is the interesting one, and it is a finding rather than a failure.**
A refresh that reaches the tree, holds every invariant and observes that the
system moved has *worked*: exit 4 is degraded-with-findings, the same reading
`adopt probe diff` and `adopt map --check-expected` already carry. Exit 1 is
reserved for a run that could not do its job -- an extractor that raised, which
makes the map incomplete in an unknown way and therefore makes every absence
below it unreliable evidence. A clean run exits 0 and writes nothing at all.

**Every `adopt_map` and `adopt_store.annex` import is inside the body.** v6.1
§2.1 requires new verbs to register lazily so `CLI_COLD_START_MS` holds; the
budget is already tight, and `adopt version` must not pay for eleven extractors
and a hashing pass it never uses.

**`--no-probes` skips the probe half.** With probes defined and a baseline set,
a refresh re-runs them: that is the "(+ re-probe if probes exist)" half of
v6.1's demo line, and it is the only part of the verb that opens a socket. The
flag exists because a refresh in CI, or against a system whose sandbox is down,
should still be able to answer the artifact question -- and because an FDE who
wants the map diff and nothing else should not have to unset a baseline to get
it. Probe I/O stays behind `adopt_probe.runner` under the existing `probe-io`
contract; refresh opens nothing itself.
"""

from pathlib import Path
from typing import Annotated, Any

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store

__all__ = ["refresh"]

PathArgument = Annotated[
    Path, typer.Argument(help="Repository root to re-map. Defaults to the working directory.")
]
ScopeOption = Annotated[
    str | None,
    typer.Option("--scope", help="firm/engagement/system/environment. Defaults to the store's."),
]
PacksOption = Annotated[
    str | None,
    typer.Option(
        "--packs",
        help="Comma-separated pack names, overriding archetype selection. Identities whose "
        "extractor does not run are exempt from death, never retired.",
    ),
]
NoProbesOption = Annotated[
    bool,
    typer.Option(
        "--no-probes",
        help="Skip the probe re-run. The map diff still runs; nothing opens a socket.",
    ),
]
NetworkOption = Annotated[
    bool,
    typer.Option(
        "--allow-network",
        help="Permit the model adapter for a probe's `prompt` steps. An `http` step "
        "always reaches its declared hosts; this governs the agent seam only.",
    ),
]
ActorOption = Annotated[str | None, typer.Option("--actor", help="Who ran it, for the record.")]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def refresh(
    path: PathArgument = Path(),
    scope: ScopeOption = None,
    packs: PacksOption = None,
    no_probes: NoProbesOption = False,
    allow_network: NetworkOption = False,
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Re-map the repository, classify what changed, and queue the review."""
    # Lazy by design -- see the module docstring.
    from adopt_map import SourceTree, registry, run_map, select_packs
    from adopt_map.diff import CLASS_DEAD

    from adopt_cli.commands._map_support import resolve_scope, stored_identities, system_archetype
    from adopt_cli.commands._refresh_support import (
        ProbeDelta,
        current_file_state,
        diff_for,
        probe_delta,
        record_refresh,
        refresh_batch_key,
    )
    from adopt_cli.store_option import configured_file_state

    tree = SourceTree.scan(path)
    batch_key = refresh_batch_key()
    # Not migrating, for `adopt map`'s reason: a command asked to read a
    # repository must not upgrade a client's database as a side effect.
    handle = open_configured_store(store, read_only=False)
    try:
        resolved = resolve_scope(handle, scope)
        selected = select_packs(
            system_archetype(handle, resolved),
            override=[name.strip() for name in packs.split(",")] if packs else None,
            available=registry(),
        )
        scope_ref = resolved.path()

        # Everything comparative is read **before** the run: afterwards every
        # new identity is stored and every changed file has been re-read.
        before = stored_identities(handle, resolved)
        with configured_file_state(handle) as snapshot:
            previous_state = snapshot.load(scope_ref)
        current_state, files_hashed = current_file_state(tree)

        run = run_map(
            tree=tree,
            scope=resolved,
            packs=selected,
            writer=handle.identities(),
            records=handle.revision_records(),
            stored=before,
            actor_id=actor,
        )
        diff = diff_for(
            run=run,
            before=before,
            previous_state=previous_state,
            current_state=current_state,
        )
        # **After the diff, before the write.** The probe half needs the deaths
        # the map just found -- a probe still exercising a referent the tree no
        # longer has is a stale declaration, not a drifted system -- and both
        # halves have to be in hand before the single transaction opens.
        probes = (
            ProbeDelta(ran=False, reason="--no-probes")
            if no_probes
            else probe_delta(
                handle,
                resolved,
                dead_uris=frozenset(
                    entry.uri for entry in diff.changes if entry.impact_class == CLASS_DEAD
                ),
                allow_network=allow_network,
            )
        )
        outcome = record_refresh(
            handle,
            scope=resolved,
            diff=diff,
            batch_key=batch_key,
            probes=probes,
            actor_id=actor,
        )
        outcome.files_hashed = files_hashed
        # After the canonical write commits, never before: a snapshot saved
        # first would let a crash between the two leave the store claiming this
        # tree was already accounted for, losing a real change permanently.
        with configured_file_state(handle) as snapshot:
            outcome.snapshot_paths = snapshot.replace(
                scope_ref, current_state, observed_at=_now(handle)
            )
        payload = build_payload(run, outcome)
    finally:
        handle.close()

    emit(payload, as_json=json_output, title="adopt refresh")

    if run.failed:
        # Exit 1: an extractor raised, so the map is incomplete in an unknown
        # way. Checked first because a failed extractor makes every absence
        # below it unreliable -- the same ordering `adopt map` uses.
        raise typer.Exit(1)
    if outcome.found_actionable:
        # Exit 4: the command worked and found something a human must see.
        # Actionable only -- a run whose sole finding is RENDER-ONLY recorded it,
        # rendered it and exits 0, because an exit code that fires on every
        # comment edit is one every script learns to ignore.
        raise typer.Exit(4)  # const-sync: ok -- exit code, not a version.


def build_payload(run: Any, outcome: Any) -> dict[str, Any]:
    """The refresh report: what changed, what was written, and what was not looked at.

    **Exemptions and re-baselines are first-class output**, not a footnote. Both
    are the run saying "I did not judge this", and a report that showed only
    findings would make a run that examined nothing look like a clean one.
    """
    from adopt_map.diff import CLASS_RENDER_ONLY

    diff = outcome.diff
    return {
        "scope": run.scope,
        "packs": list(run.packs),
        "files_walked": run.files_walked,
        "files_hashed": outcome.files_hashed,
        "batch_key": outcome.batch_key,
        "counts_by_class": dict(diff.by_class()),
        "changes": [
            {
                "uri": entry.uri,
                "class": entry.impact_class,
                "decided_by": entry.decided_by,
                "evidence": entry.evidence,
                "successor_uri": entry.successor_uri,
            }
            for entry in diff.changes
            if entry.impact_class != CLASS_RENDER_ONLY
        ],
        "render_only": {
            "available": diff.render_only_available,
            # Both, because the class is per **referent** (its binding survived)
            # while the thing a reader recognises is the **file** they just
            # edited. Reporting only one leaves the other to be inferred.
            "referents": [
                {"uri": entry.uri, "path": entry.source_path}
                for entry in diff.changes
                if entry.impact_class == CLASS_RENDER_ONLY
            ],
            "paths": sorted(
                {
                    entry.source_path
                    for entry in diff.changes
                    if entry.impact_class == CLASS_RENDER_ONLY and entry.source_path is not None
                }
            ),
        },
        "rebaselined": [
            {
                "uri": entry.uri,
                "from_extractor_version": entry.from_version,
                "to_extractor_version": entry.to_version,
                # The sentence v6.1 requires an upgrade to produce, verbatim.
                "note": "instrument changed, system not re-judged",
            }
            for entry in diff.rebaselines
        ],
        "exempt": [{"uri": entry.uri, "reason": entry.reason} for entry in diff.exemptions],
        "ambiguous": [list(group) for group in diff.ambiguous],
        "written": {
            "change_event": outcome.change_event_id,
            "classifications": len(outcome.classification_ids),
            "retired": len(outcome.retired),
            "digests_recorded": len(outcome.redigested),
            "bindings_staled": len(outcome.staled_bindings),
        },
        "review": {
            "batch": outcome.review_batch_id,
            "items": len(outcome.review_item_ids),
            "queued": [
                {
                    "item_id": item.item_id,
                    "blast_radius": item.blast_radius,
                    "causes": [
                        {
                            "uri": cause.identity_uri,
                            "class": cause.impact_class,
                            "evidence": cause.evidence,
                        }
                        for cause in item.causes
                    ],
                }
                for item in outcome.queued_items
            ],
            "unbound_changes": [entry.uri for entry in outcome.unbound],
        },
        "probes": _probe_payload(outcome.probes, outcome.probe_event_id),
        "snapshot_paths": outcome.snapshot_paths,
    }


def _probe_payload(probes: Any, change_event_id: str | None) -> dict[str, Any]:
    """The probe half of the report -- findings **and** everything not judged.

    A probe that could not run, or had no baseline, is as much a part of the
    answer as one that drifted: "the sandbox was down" printed as "nothing
    changed" is the measurement-nobody-took failure the artifact half already
    refuses to make about cosmetic edits.
    """
    if probes is None:  # pragma: no cover -- always set by the command
        return {"ran": False, "reason": "not run"}
    return {
        "ran": probes.ran,
        "reason": probes.reason,
        "probes_run": probes.probes_run,
        "drifted": list(probes.drifted),
        "failed": list(probes.failed),
        "skipped": [dict(entry) for entry in probes.skipped],
        "referents": [entry.uri for entry in probes.entries],
        # Declared by a probe and absent from the map: the probe file names a
        # referent that no longer exists, which is a fact about our probe rather
        # than about the client's system.
        "unresolved_exercises": list(probes.unresolved),
        "conflicts": [dict(entry) for entry in probes.conflicts],
        # The `provider`-source event, separate from the artifact one: "what did
        # the probes see this run" has to be answerable from the store.
        "change_event": change_event_id,
    }


def _now(handle: Any):  # type: ignore[no-untyped-def]
    """The store's clock when it has one, so a seeded test gets seeded stamps."""
    clock = getattr(handle, "clock", None)
    if clock is not None:
        return clock.now()
    from adopt_obs import SystemClock

    return SystemClock().now()
