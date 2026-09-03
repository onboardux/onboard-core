"""`adopt ci-sense` -- Build 8's verb: the customer's CI reports what it sees.

**Sensing executes where access exists** (v6.1 §6 Build 8 F4, D10). The plane
holds an operated system's canon but has no route into a client network and no
business holding repository credentials; the CI already has the checkout. So the
deterministic half runs here -- walk, extract, hash -- and the compact result is
posted to the plane, which classifies it against the canon it owns. Nothing
inbound is required, no credential leaves the pipeline, and no line of the
repository is transmitted: attribute digests and file digests only.

**This verb writes nothing locally.** It is not `adopt refresh` with a POST on
the end: it opens no transaction, appends no revision and stales nothing. R9
makes the plane the sole writer of an operated system's canon, and the mirror of
this rule is why `adopt refresh` now refuses a replica outright.

**Exit 0 on findings, deliberately, and `--strict` to opt out.** A sensing step
that fails a customer's deploy because their documentation is now stale teaches
that customer to delete the step, and a queue nobody feeds is worse than a queue
nobody reads. Findings are the *product working*. `--strict` exists for the
pipeline that genuinely wants to gate on knowledge health, and it is the
operator's choice rather than ours. Exit 1 stays a real failure -- the plane
refused, the network broke, an extractor crashed -- and exit 3 is a refusal.

**Every `adopt_map` import is inside the body**, per v6.1 §2.1: `adopt version`
must not pay for eleven extractors and a hashing pass it never uses.
"""

from pathlib import Path
from typing import Annotated, Any

import typer

from adopt_cli.json_out import emit

__all__ = ["ci_sense"]

PathArgument = Annotated[
    Path, typer.Argument(help="Repository root to observe. Defaults to the working directory.")
]
ScopeOption = Annotated[
    str | None,
    typer.Option("--scope", help="firm/engagement/system/environment. Defaults to the store's."),
]
PacksOption = Annotated[
    str | None,
    typer.Option("--packs", help="Comma-separated pack names, overriding archetype selection."),
]
CadenceOption = Annotated[
    int | None,
    typer.Option(
        "--cadence-hours",
        help="How often this pipeline is expected to report. Silence beyond it degrades "
        "freshness for the scope. Defaults to CI_SENSE_DEFAULT_CADENCE_HOURS.",
    ),
]
StrictOption = Annotated[
    bool,
    typer.Option(
        "--strict",
        help="Exit 4 when the plane records findings. Off by default: a sensing step that "
        "fails a deploy over stale knowledge is a step somebody deletes.",
    ),
]
RunIdOption = Annotated[
    str | None,
    typer.Option(
        "--run-id",
        help="Idempotency key. Reuse it across retries of one pipeline run so a retry "
        "records liveness without double-writing. Generated when absent.",
    ),
]
NoProbesOption = Annotated[
    bool,
    typer.Option(
        "--no-probes",
        help="Skip the probe half. The artifact observation is still posted; nothing "
        "opens a socket toward the client's system.",
    ),
]
NetworkOption = Annotated[
    bool,
    typer.Option(
        "--allow-network",
        help="Permit the model adapter for a probe's `prompt` steps. An `http` step "
        "needs no such flag -- the manifest's host allow-list is the invariant there.",
    ),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def ci_sense(
    path: PathArgument = Path(),
    scope: ScopeOption = None,
    packs: PacksOption = None,
    cadence_hours: CadenceOption = None,
    strict: StrictOption = False,
    run_id: RunIdOption = None,
    no_probes: NoProbesOption = False,
    allow_network: NetworkOption = False,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Observe this repository and post the observation to the control plane."""
    # Lazy by design -- see the module docstring.
    from adopt_map import MapReport, SourceTree, registry, select_packs
    from adopt_map.observe import observe_tree

    from adopt_cli.commands import version as version_command
    from adopt_cli.commands._ci_sense_probes import ProbeSection, run_payload_probes
    from adopt_cli.commands._ci_sense_support import (
        build_payload,
        post_payload,
        sense_run_id,
    )
    from adopt_cli.commands._map_support import resolve_scope, system_archetype
    from adopt_cli.commands._refresh_support import current_file_state
    from adopt_cli.commands._remote_support import configured_remote
    from adopt_cli.remote import PLANE_URL_KEY, Remote
    from adopt_cli.store_option import open_configured_store
    from adopt_const import CI_SENSE_DEFAULT_CADENCE_HOURS
    from adopt_obs import AdoptError, ErrorCode, SystemClock

    remote: Remote | None = configured_remote()
    if remote is None:
        # A refusal rather than a local fallback: this verb's entire purpose is
        # the plane, and "worked" with nothing posted is the silence Build 8
        # exists to prevent.
        raise AdoptError(
            ErrorCode.PLANE_REMOTE_NOT_CONFIGURED,
            message="`adopt ci-sense` posts observations to a control plane, and none is configured",
            hint=f"Set {PLANE_URL_KEY}, ADOPT_PLANE_SYSTEM and ADOPT_PLANE_TOKEN_ENV. "
            "To sense a store you own locally instead, run `adopt refresh`.",
        )

    tree = SourceTree.scan(path)
    # Read-only: this verb observes and never writes. The store is opened only
    # to resolve the scope, the archetype and the probe definitions -- all facts
    # about the system that the payload must carry and that the tree does not
    # know. **The probes run inside this block**, because they are executed from
    # definitions the handle holds; they still write nothing, here or anywhere.
    handle = open_configured_store(store, read_only=True)
    try:
        resolved = resolve_scope(handle, scope)
        selected = select_packs(
            system_archetype(handle, resolved),
            override=[name.strip() for name in packs.split(",")] if packs else None,
            available=registry(),
        )
        probe_section = (
            ProbeSection(ran=False, reason="--no-probes")
            if no_probes
            else run_payload_probes(handle, resolved, allow_network=allow_network)
        )
    finally:
        handle.close()

    sightings = observe_tree(tree, packs=selected)
    report = MapReport(scope=resolved.path(), packs=tuple(pack.name for pack in selected))
    report.files_walked = len(tree.files)
    report.files_oversized = tree.oversized
    for found in sightings:
        report.outcomes.extend(found.outcomes)
        for sighting in found.sightings:
            report.identities_seen += 1
            report.files_with_observations.add(sighting.observation.span.path)

    current_state, files_hashed = current_file_state(tree)
    cadence = (cadence_hours or CI_SENSE_DEFAULT_CADENCE_HOURS) * 3600
    payload = build_payload(
        run_id=run_id or sense_run_id(),
        scope=resolved,
        sightings=sightings,
        report=report,
        file_state=current_state,
        cadence_seconds=cadence,
        # The version this binary *reports* is the version it stamps on what it
        # writes -- Build 0's provenance lesson, which cost a republished
        # release when the two came from different expressions.
        connector_version=f"adopt-cli/{version_command.build_payload()['version']}",
        observed_at=SystemClock().now(),
        probes=probe_section.payload(),
    )
    outcome = post_payload(remote, payload)

    emit(
        _report(payload, report, outcome, files_hashed=files_hashed),
        as_json=json_output,
        title="ci-sense",
    )
    if report.failed:
        # Exit 1 first, for `adopt refresh`'s reason: a crashed extractor makes
        # every absence below it unreliable evidence, and the plane was told so
        # in the payload -- it exempts those identities from death rather than
        # retiring what nobody looked for.
        raise typer.Exit(1)
    if strict and outcome.findings:
        raise typer.Exit(4)  # const-sync: ok -- exit code, not a version.


def _report(
    payload: dict[str, Any], report: Any, outcome: Any, *, files_hashed: int
) -> dict[str, Any]:
    """What the step prints, which is also what a CI log has to be readable as."""
    return {
        "run_id": payload["run_id"],
        "scope": payload["scope"],
        "packs": payload["packs"],
        "observed": len(payload["observed"]),
        "files_walked": report.files_walked,
        "files_hashed": files_hashed,
        "extractors_failed": [outcome_.extractor for outcome_ in report.failed],
        "probes": {
            "ran": payload["probes"]["ran"],
            "reason": payload["probes"]["reason"],
            "executed": len(payload["probes"]["results"]),
            "skipped": len(payload["probes"]["skipped"]),
        },
        "posted": {
            "accepted": outcome.accepted,
            "replayed": outcome.replayed,
            "batch_key": outcome.batch_key,
            "counts": dict(outcome.counts),
            "findings": outcome.findings,
        },
    }
