"""`adopt map` -- contracts §8, PRD F1/F2/F3/F13.

**This module is composition and nothing else.** It resolves flags, opens the
store, hands `adopt_map` its ports and renders the result. Every rule lives in
`adopt_map`: scope resolution and its four aborts in `scope_resolve`, URI minting
in `minting`, the write set in `writer`. That split is why this file gets **zero
dedicated tests** under `03` §7's budget -- it is T4 glue, swept by the contract
tests over the exit codes and by the six journeys in S1.8.

**The exit codes are `02` §8's, not the category default** (B1-CR-35, OD-3).
`adopt_obs.map_exit_code_for` holds the table; this module calls it and holds no
copy. Codes `0`, `3` and `6` are *successful runs with less output* and callers
treat them as usable.

**`--json` stdout is exactly one object.** Logs and the error envelope go to
stderr, so a caller piping into `jq` strips nothing (`02` §8).

Sprint S1.1 wires the write path against `common.stub`. The file index, the
scheduler, the budgets, the degrade ladder, coverage and the four emitters are
S1.3's and S1.7's. **The `02` §8 flags those sprints implement are not declared
here yet** -- `--profile`, `--budget`, `--stage1-budget`, `--format`,
`--export-bundle`, `--db-url` and `--agent` arrive with the machinery behind
them. Accepting a flag that does nothing is indistinguishable from a broken one,
and `05` S1.1's deliverables record the deferral instead.
"""

from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from adopt_map.schemas import Extractor
from adopt_map.scope_resolve import ResolvedScope, resolve_scope
from adopt_map.writer import SurfaceWriter, SurfaceWriteResult

from adopt_cli.config import (
    load_config_file,
    project_config_path,
    resolve_all,
    user_config_path,
)
from adopt_cli.json_out import emit, emit_error
from adopt_cli.store_option import open_configured_store
from adopt_model._enums import Archetype
from adopt_obs import (
    AdoptError,
    ErrorCode,
    MapExitCode,
    get_logger,
    map_exit_code_for,
    new_run_id,
)

__all__ = ["map_command"]

_log = get_logger(__name__)

#: `02` §8's flag surface. Declared in full even where S1.1 does not yet honour a
#: flag, because a CLI contract that grows flags sprint by sprint is one no
#: integrator can write a script against.
FirmOption = Annotated[str | None, typer.Option("--firm", help="Firm id.")]
EngagementOption = Annotated[str | None, typer.Option("--engagement", help="Engagement id.")]
SystemOption = Annotated[str | None, typer.Option("--system", help="System id.")]
EnvironmentOption = Annotated[
    str | None,
    typer.Option(
        "--environment",
        help="Environment id. Omit only when the system has exactly one environment: "
        "there is no default environment.",
    ),
]
ArchetypeOption = Annotated[
    str, typer.Option("--archetype", help="auto|web|platform|lowcode|data|ai.")
]
OutOption = Annotated[Path | None, typer.Option("--out", help="Artifact directory.")]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path.")]
DryRunOption = Annotated[
    bool, typer.Option("--dry-run", help="Resolve and extract, but write nothing.")
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def _resolved_payload(resolved: ResolvedScope) -> dict[str, Any]:
    return {
        "firm_id": resolved.firm_id,
        "engagement_id": resolved.engagement_id,
        "system_id": resolved.system_id,
        "environment_id": resolved.environment_id,
        "environment_name": resolved.environment_slug,
    }


def _result_payload(result: SurfaceWriteResult, resolved: ResolvedScope) -> dict[str, Any]:
    """The `surface.json` counters this build produces so far. `02` §9.2's shape,
    minus the fields S1.3 fills (`coverage`, `degradations`, `facts`)."""
    return {
        "report_version": result.report_version,
        "run_id": result.run_id,
        "scope": _resolved_payload(resolved),
        "system": {"archetype": resolved.archetype, "tier": resolved.tier},
        "identities_seen": result.identities_seen,
        "identities_new": result.identities_new,
        "revisions_written": result.revisions_written,
        "provenance_written": result.provenance_written,
        "moves": [{"from": move.from_uri, "to": move.to_uri} for move in result.moves],
        "conflicts": [
            {
                "identity_uri": conflict.identity_uri,
                "reason": conflict.reason,
                "candidates": conflict.candidates,
            }
            for conflict in result.conflicts
        ],
        # PRD F4.5: when revisions were written because the *tool* changed rather
        # than the system, the report says so by name.
        "extractor_version_changes": {
            extractor: {"from": previous, "to": current}
            for extractor, (previous, current) in sorted(result.extractor_version_changes.items())
        },
        "gaps": [
            {"identity_uri": gap.identity_uri, "reason": gap.reason, "detail": gap.detail}
            for gap in result.gaps
        ],
    }


def map_command(
    path: Annotated[Path, typer.Argument(help="The client tree to map.")] = Path(),
    firm: FirmOption = None,
    engagement: EngagementOption = None,
    system: SystemOption = None,
    environment: EnvironmentOption = None,
    archetype: ArchetypeOption = "auto",
    out: OutOption = None,
    store: StoreOption = None,
    dry_run: DryRunOption = False,
    as_json: JsonOption = False,
) -> None:
    """Map a system's surface into canonical identity URIs and append-only revisions."""
    del out  # S1.3 owns emission; accepted so the flag surface is stable.

    # `02` §2 rule 1, in its stated order: the config file, then `ADOPT_*`, then
    # the flag. `adopt_cli.config` already holds that order for every other key,
    # so the scope ids join the registry rather than growing a second resolver.
    firm = _configured(firm, "ADOPT_FIRM_ID")
    engagement = _configured(engagement, "ADOPT_ENGAGEMENT_ID")
    system = _configured(system, "ADOPT_SYSTEM_ID")
    environment = _configured(environment, "ADOPT_ENVIRONMENT_ID")

    if firm is None or engagement is None or system is None:
        missing = [
            name
            for name, value in (
                ("--firm", firm),
                ("--engagement", engagement),
                ("--system", system),
            )
            if value is None
        ]
        _fail(
            AdoptError(
                ErrorCode.MAP_USAGE,
                message=f"{', '.join(missing)} not supplied by any configuration source",
                hint="`adopt map` resolves exactly one scope and never guesses one. Pass "
                "the ids as flags, set ADOPT_FIRM_ID / ADOPT_ENGAGEMENT_ID / "
                "ADOPT_SYSTEM_ID in the environment, or put them in "
                "`.adopt/config.toml`. `adopt doctor --json` shows where each one "
                "resolved from.",
            ),
            as_json=as_json,
        )

    resolved_archetype: Archetype = "web" if archetype == "auto" else archetype  # type: ignore[assignment]

    # **Resolution runs against a read-only handle, and that is load-bearing.**
    # `02` §8's exit-4 row promises "zero writes", and `05` S1.1's validation line
    # is stronger still: *"the store is byte-identical afterwards"*. Opening a
    # SQLite store read-write changes the file even when no row changes -- the
    # header and the journal move -- so an abort reached through a writable handle
    # fails that line while being entirely correct about rows. Resolving under a
    # read-only handle makes the promise true at the byte level, and makes every
    # abort path one that *cannot* write rather than one that declines to.
    resolved, facts = _resolve_and_extract(
        store=store,
        path=path,
        firm=firm,
        engagement=engagement,
        system=system,
        environment=environment,
        archetype=resolved_archetype,
        as_json=as_json,
    )

    # One correlation id for the whole invocation: the log lines, the
    # `audit_event.subject_ref` and the report all carry it, which is what makes
    # a run traceable from a log line to the row it wrote. `run_id` is a
    # **reserved** log field -- the logger stamps it -- so it is bound rather
    # than passed, and the writer is handed the same value.
    run_id = new_run_id()
    log = _log.bind_run(run_id)

    if dry_run:
        log.info("map_dry_run", run_scope=resolved.scope.path(), facts=len(facts))
        emit(
            {"dry_run": True, "scope": _resolved_payload(resolved), "facts": len(facts)},
            as_json=as_json,
            title="adopt map (dry run)",
        )
        raise typer.Exit(MapExitCode.COMPLETE)

    from adopt_store.revisions import (
        BindingRevisionDraft,
        IdentityRevisionDraft,
        KnowledgeRevisionDraft,
    )

    handle = open_configured_store(store, read_only=False)
    try:
        writer = SurfaceWriter(
            identities=handle.identities(),
            items=handle.items(),
            bindings=handle.bindings(),
            aux=handle.import_records(),
            lookup=handle.export_records(),
            revisions=handle.revisions(),
            knowledge_draft=KnowledgeRevisionDraft,
            binding_draft=BindingRevisionDraft,
            identity_draft=IdentityRevisionDraft,
            schema_version=handle.schema_version,
            supported_schema_version=handle.schema_version,
        )
        result = writer.write_run(
            resolved=resolved,
            manifest=_extractor_for(path).manifest(),
            facts=facts,
            vcs_revision=_vcs_revision(path),
            run_id=run_id,
        )
        log.info(
            "map_completed",
            identities_seen=result.identities_seen,
            identities_new=result.identities_new,
            gaps=len(result.gaps),
        )
        emit(_result_payload(result, resolved), as_json=as_json, title="adopt map")
        raise typer.Exit(MapExitCode.COMPLETE)
    except AdoptError as error:
        _fail(error, as_json=as_json)
    finally:
        handle.close()


def _resolve_and_extract(
    *,
    store: Path | None,
    path: Path,
    firm: str,
    engagement: str,
    system: str,
    environment: str | None,
    archetype: Archetype,
    as_json: bool,
) -> tuple[ResolvedScope, list[Any]]:
    """Rules 1-5 and the extraction pass, under a **read-only** handle.

    Extraction happens here too, before the store is opened for writing, because
    `02` §7 obligation 1 makes extractors static-only: they read a client tree and
    have no business holding a writable store while they do it.
    """
    handle = open_configured_store(store, read_only=True)
    try:
        boundary = handle.boundary().current(
            system_id=system, environment_id=environment
        ) or handle.boundary().current(system_id=system, environment_id=None)

        resolved = resolve_scope(
            handle.export_records(),
            firm_id=firm,
            engagement_id=engagement,
            system_id=system,
            environment_id=environment,
            archetype=archetype,
            tier=None if boundary is None else boundary.tier,
        )
    except AdoptError as error:
        _fail(error, as_json=as_json)
    finally:
        handle.close()

    return resolved, list(_extractor_for(path).extract(str(path)))


def _configured(override: str | None, key: str) -> str | None:
    """A scope id from the flag, else the registry's resolution order.

    Returns `None` when no layer supplies one, which the caller turns into
    `MAP_USAGE`. **It never invents a value**: `02` §2 rule 5 is that nothing is
    created and a missing row produces the exact command to run, and a default
    scope id would resolve to a row belonging to somebody else.

    **The two file layers are loaded here, and that is worth explaining.**
    `resolve_all()` takes `project` and `user` as injectable mappings and
    defaults both to empty, so a bare `resolve_all()` resolves the flag and
    environment layers **only** -- the config files are never read. That is a
    Build 0 observation rather than a Build 1 repair: `configured_store_path`
    and `configured_annex` call it the same way, so `ADOPT_STORE_PATH` in
    `.adopt/config.toml` is silently ignored today too, and widening
    `resolve_all` itself would change how every key in the registry resolves --
    a Build 0 amendment with its own review, recorded as OD-10. `02` §2 rule 1
    binds `adopt map` specifically, so `adopt map` reads the files for its own
    keys and nothing else changes.
    """
    if override is not None:
        return override
    resolutions = resolve_all(
        project=load_config_file(project_config_path()),
        user=load_config_file(user_config_path()),
    )
    for resolution in resolutions:
        if resolution.key == key and resolution.value:
            return resolution.value
    return None


def _extractor_for(path: Path) -> Extractor:
    """The one extractor S1.2 runs, chosen by the protocol's own `applies_to`.

    **A stopgap with a stated end date**: `03` §5.8's registry, scheduler and
    manifest audit are S1.3's, and until they exist a run has exactly one
    extractor. Choosing between the two stubs through `applies_to` rather than a
    flag means the seam S1.3 replaces is the one the protocol already declares --
    `common.stub_tree` reads the tree, so a tree with no source files falls back
    to `common.stub`, which reads nothing and always applies.
    """
    from adopt_extractors_common import StubExtractor, StubTreeExtractor

    tree = StubTreeExtractor()
    return tree if tree.applies_to(str(path)) else StubExtractor()


def _fail(error: AdoptError, *, as_json: bool) -> NoReturn:
    """Render the envelope to stderr and exit with `02` §8's code.

    `map_exit_code_for` raises `KeyError` for a Build 0 code, deliberately: a
    non-`MAP_*` code reaching here means a caller took the wrong exit table, and
    a plausible default would hide it.
    """
    emit_error(error.to_envelope(), as_json=as_json)
    raise typer.Exit(map_exit_code_for(error.code))


def _vcs_revision(path: Path) -> str | None:
    """The tree's commit sha, or `None`.

    `None` is a real answer, not a failure: a tree that is not a checkout has no
    commit, and `provenance` is then recorded as a gap rather than written under
    a `source_type` that does not fit (B1-CR-36). S1.3's file index owns the real
    resolution; S1.1 answers honestly with what it can see without running a
    subprocess against a client tree.
    """
    head = path / ".git" / "HEAD"
    if not head.is_file():
        return None
    content = head.read_text(encoding="utf-8").strip()
    if not content.startswith("ref: "):
        return content or None
    ref = (path / ".git" / content.removeprefix("ref: ")).resolve()
    return ref.read_text(encoding="utf-8").strip() if ref.is_file() else None
