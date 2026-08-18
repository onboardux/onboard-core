"""`adopt map` -- contracts §8, PRD F1/F2/F3/F7/F9/F10/F11/F13.

**This module is composition and nothing else.** It resolves flags, opens the
store, hands `adopt_map.orchestrator` its ports and renders the result. Every rule
lives in `adopt_map`: scope resolution and its four aborts in `scope_resolve`, URI
minting in `minting`, the write set in `writer`, and from S1.3 the whole run
lifecycle -- one walk, the plan, staging, the budget, coverage and the four
emitters -- in `orchestrator`. That split is why this file gets **zero dedicated
tests** under `03` §7's budget: it is T4 glue, swept by the contract tests over
the exit codes and by the six journeys in S1.8.

**The exit codes are `02` §8's, not the category default** (B1-CR-35, OD-3).
`adopt_obs.map_exit_code_for` holds the table; this module calls it and holds no
copy. Codes `0`, `3` and `6` are *successful runs with less output* and callers
treat them as usable.

**`--json` stdout is exactly one object.** Logs and the error envelope go to
stderr, so a caller piping into `jq` strips nothing (`02` §8).

**S1.3 lands six of the seven flags S1.1 deferred**, because the machinery behind
each now exists: `--profile`, `--budget`, `--stage1-budget`, `--format`,
`--export-bundle` and `--db-url`. `--agent` stays out until S1.7 builds the pass
-- `05` S1.1's argument holds, that accepting a flag which does nothing is
indistinguishable from a broken one.
"""

from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from adopt_map.netguard import EgressGuard
from adopt_map.orchestrator import DEFAULT_FORMAT_FLAG, DEFAULT_OUT_DIR
from adopt_map.orchestrator import run as run_map
from adopt_map.plugins import DEFAULT_ENABLED_PACKS, ExtractorRegistry
from adopt_map.report import RunResult
from adopt_map.scope_resolve import ResolvedScope, resolve_scope
from adopt_map.writer import SurfaceWriter

from adopt_cli.commands.version import adopt_version
from adopt_cli.config import (
    load_config_file,
    project_config_path,
    resolve_all,
    user_config_path,
)
from adopt_cli.json_out import emit, emit_error
from adopt_cli.map_config import load_map_config
from adopt_cli.store_option import open_configured_store
from adopt_const import MAP_STAGE1_BUDGET_S, MAP_TOTAL_BUDGET_S
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

#: The `02` §8 formats, and the packs whose flag is on by default. A `--format`
#: value outside this set is `MAP_USAGE` rather than a silently skipped artifact.
_FORMATS: frozenset[str] = frozenset({"md", "json", "mermaid", "d2"})

#: The archetypes whose run `02` §8 requires an `--export-bundle` for. A packaged
#: platform has no source tree to walk: the metadata *is* the export, so a run
#: without one has nothing to read and says so at exit 4 rather than reporting an
#: empty map (`01` F8.3, `05` S1.6).
_BUNDLE_ARCHETYPES: frozenset[str] = frozenset({"platform", "lowcode"})

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
ProfileOption = Annotated[
    str, typer.Option("--profile", help="fast|full. `fast` runs extractors in one process.")
]
BudgetOption = Annotated[
    float | None, typer.Option("--budget", help="Total extraction budget, seconds.")
]
Stage1BudgetOption = Annotated[
    float | None, typer.Option("--stage1-budget", help="Stage-1 deadline, seconds.")
]
FormatOption = Annotated[str, typer.Option("--format", help="Comma-separated: md,json,mermaid,d2.")]
ExportBundleOption = Annotated[
    Path | None, typer.Option("--export-bundle", help="Packaged-platform metadata export.")
]
DbUrlOption = Annotated[
    str | None, typer.Option("--db-url", help="Live schema reflection (tier-gated).")
]
DryRunOption = Annotated[
    bool, typer.Option("--dry-run", help="Resolve and extract, but write nothing.")
]
AgentOption = Annotated[
    bool,
    typer.Option(
        "--agent/--no-agent",
        help="Run the agentic glue pass after the deterministic one. Off by default.",
    ),
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


def _result_payload(result: RunResult) -> dict[str, Any]:
    """`--json`'s single stdout object.

    The `02` §9.2 `surface.json` payload minus `facts[]`, which belongs in the
    artifact rather than on a terminal: a caller wanting the facts reads the file
    the run just wrote, and a caller scripting exit codes wants the counters.
    """
    from adopt_map.emit.json_report import surface_payload

    payload = surface_payload(result)
    payload.pop("facts", None)
    payload["exit_code"] = result.exit_code
    return payload


def map_command(
    path: Annotated[Path, typer.Argument(help="The client tree to map.")] = Path(),
    firm: FirmOption = None,
    engagement: EngagementOption = None,
    system: SystemOption = None,
    environment: EnvironmentOption = None,
    archetype: ArchetypeOption = "auto",
    out: OutOption = None,
    store: StoreOption = None,
    profile: ProfileOption = "full",
    budget: BudgetOption = None,
    stage1_budget: Stage1BudgetOption = None,
    output_format: FormatOption = DEFAULT_FORMAT_FLAG,
    export_bundle: ExportBundleOption = None,
    db_url: DbUrlOption = None,
    dry_run: DryRunOption = False,
    agent: AgentOption = False,
    as_json: JsonOption = False,
) -> None:
    """Map a system's surface into canonical identity URIs and append-only revisions."""
    # `02` §2 rule 1, in its stated order: the config file, then `ADOPT_*`, then
    # the flag. `adopt_cli.config` already holds that order for every other key,
    # so the scope ids join the registry rather than growing a second resolver.
    firm = _configured(firm, "ADOPT_FIRM_ID")
    engagement = _configured(engagement, "ADOPT_ENGAGEMENT_ID")
    system = _configured(system, "ADOPT_SYSTEM_ID")
    environment = _configured(environment, "ADOPT_ENVIRONMENT_ID")

    formats = _formats(output_format, as_json=as_json)
    if profile not in {"fast", "full"}:
        _fail(
            AdoptError(
                ErrorCode.MAP_USAGE,
                message=f"--profile {profile!r} is not one of fast|full",
                hint="`fast` runs extractors sequentially in this process, which is "
                "deterministic but cannot enforce the per-extractor watchdog. `full` "
                "uses the process pool.",
            ),
            as_json=as_json,
        )

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
    if resolved_archetype in _BUNDLE_ARCHETYPES and export_bundle is None:
        _fail(
            AdoptError(
                ErrorCode.MAP_EXPORT_BUNDLE_MISSING,
                message=f"a {resolved_archetype} run needs --export-bundle",
                hint="A packaged platform has no source tree to read: the metadata *is* "
                "the export. Ask the platform owner to run their export and pass the "
                "bundle:\n"
                "  sfdx force:source:retrieve -x manifest/package.xml   # Salesforce\n"
                "  adopt map . --archetype platform --export-bundle <path>",
            ),
            as_json=as_json,
        )
    if export_bundle is not None and not export_bundle.exists():
        # A flag pointing at nothing is the same fact as no flag -- there is no
        # bundle to read -- so it takes the same code and the same exit. Checked
        # here rather than at `build_index`, where a nonexistent root produces an
        # empty index and a run that cheerfully reports a platform with no
        # components in it: `01` §1.6 forbids exactly that kind of quiet zero.
        _fail(
            AdoptError(
                ErrorCode.MAP_EXPORT_BUNDLE_MISSING,
                message=f"--export-bundle {export_bundle} does not exist",
                hint="Point --export-bundle at the directory the platform export "
                "produced. `adopt map` reads it as the tree; it never connects to "
                "the platform.",
            ),
            as_json=as_json,
        )

    # **Resolution runs against a read-only handle, and that is load-bearing.**
    # `02` §8's exit-4 row promises "zero writes", and `05` S1.1's validation line
    # is stronger still: *"the store is byte-identical afterwards"*. Opening a
    # SQLite store read-write changes the file even when no row changes -- the
    # header and the journal move -- so an abort reached through a writable handle
    # fails that line while being entirely correct about rows.
    resolved = _resolve(
        store=store,
        firm=firm,
        engagement=engagement,
        system=system,
        environment=environment,
        archetype=resolved_archetype,
        as_json=as_json,
    )

    if db_url is not None and resolved.tier in {"T0", "T1", "T2"}:
        _fail(
            AdoptError(
                ErrorCode.MAP_TIER_DECLINED,
                message=f"--db-url needs a tier above T2; the negotiated tier is {resolved.tier}",
                hint="Live schema reflection reads a client database. Renegotiate the "
                "boundary with `adopt boundary` before passing --db-url, or drop the "
                "flag and let the migration extractors read the declared schema.",
            ),
            as_json=as_json,
        )

    run_id = new_run_id()
    log = _log.bind_run(run_id)

    registry = _registry()

    handle = None if dry_run else open_configured_store(store, read_only=False)
    try:
        writer = None if handle is None else _writer_for(handle)
        result = run_map(
            resolved=resolved,
            root=path,
            registry=registry,
            adopt_version=adopt_version(),
            writer=writer,
            coverage_records=None if handle is None else handle.coverage_records(),
            cache=None if handle is None else handle.backend,
            out_dir=out if out is not None else Path(DEFAULT_OUT_DIR),
            formats=formats,
            stage1_budget_s=MAP_STAGE1_BUDGET_S if stage1_budget is None else stage1_budget,
            total_budget_s=MAP_TOTAL_BUDGET_S if budget is None else budget,
            run_id=run_id,
            sequential=profile == "fast",
            guard=EgressGuard(),
            export_bundle=export_bundle,
        )
        log.info("map_command_completed", facts=result.total_facts(), exit_code=result.exit_code)
        payload = _result_payload(result)
        # **The glue pass runs after the transaction, and that ordering is `04`
        # §2's G-2 plus `02` §8's exit-6 guarantee together.** By the time a model
        # is called the deterministic map is written and committed, so budget
        # exhaustion can abort the pass without costing the run anything -- and a
        # pass that never runs is exit 0 with the map already on disk.
        agent_payload = _glue_pass(result, enabled=agent, root=path, log=log)
        payload["agent"] = agent_payload
        emit(payload, as_json=as_json, title="adopt map")
        # **The pass may raise the run's exit code, never lower it.** `02` §8 gives
        # exit 6 to an exhausted glue budget and guarantees "deterministic
        # artifacts and transaction intact"; a run already at exit 3 keeps the
        # larger claim about how little it managed to do.
        raise typer.Exit(max(result.exit_code, int(agent_payload.get("exit_code", 0))))
    except AdoptError as error:
        _fail(error, as_json=as_json)
    finally:
        if handle is not None:
            handle.close()


def _glue_pass(
    result: RunResult,
    *,
    enabled: bool,
    root: Path,
    log: Any,
) -> dict[str, Any]:
    """Run `04` §2's gate and, if it opens, `04` §6's pipeline. Never raises past exit 6.

    **A run without `--agent` makes zero model calls, and this is where that is
    true rather than intended**: the gate is evaluated with `agent_flag=False`,
    which refuses at G-1 before a runner is constructed, before a prompt is loaded
    and before `agent.adapter` is read. `05` S1.7's last validation line asks for
    deterministic output byte-identical with and without `--agent`; nothing on this
    path touches `result`.
    """
    from adopt_map.agent_gate import AgentBudget, GateInputs
    from adopt_map.agent_gate import evaluate as evaluate_gate
    from adopt_map.quarantine import QuarantinePaths, run_glue_pass

    configuration = load_map_config(project_config_path())
    facts_by_kind: dict[str, int] = {}
    for batch in result.batches:
        for fact in batch.facts:
            facts_by_kind[fact.identity_kind] = facts_by_kind.get(fact.identity_kind, 0) + 1

    inputs = GateInputs(
        agent_flag=enabled,
        config_enabled=configuration.agent.enabled,
        deterministic_complete=True,
        facts_by_kind=facts_by_kind,
        budget=AgentBudget(),
        tier=result.resolved.tier,
        archetype=result.resolved.archetype,
    )
    decision = evaluate_gate(inputs)
    if not decision.allowed:
        decision.record()
        return {"ran": False, "skipped_condition": decision.failed, "detail": decision.detail}

    runner = _glue_runner(configuration.agent.adapter)
    try:
        outcome = run_glue_pass(
            runner,
            inputs_gate=inputs,
            index=result.index,
            paths=QuarantinePaths(adopt_dir=root / ".adopt"),
            root=root,
            adapter=runner.adapter,
            extractor_ids=[batch.manifest.id for batch in result.batches],
        )
    except AdoptError as error:
        if error.code is ErrorCode.MAP_AGENT_BUDGET_EXHAUSTED:
            log.warn("agent_budget_exhausted", agent_error=error.code.value)
            return {
                "ran": True,
                "status": "budget_exhausted",
                "exit_code": int(MapExitCode.AGENT_BUDGET_EXHAUSTED),
            }
        # **B1-CR-88 -- a missing agent pass is never an error, and this is where
        # that stopped being true.** `04` §7's degrade ladder ends *"skip the pass
        # and record a gap"*, and the glue pass is the first path in `adopt map`
        # that can raise a **Build 0** code: `MANIFEST_INVALID` from a prompts
        # directory that is not where the run was started, `AGENT_ADAPTER_UNKNOWN`
        # when none is configured, `AGENT_OFFLINE_ADAPTER_DENIED` under the
        # offline default. None of them is in `02` §8's `MAP_*` exit table, so
        # `map_exit_code_for` raised `KeyError` **by design** -- correctly saying
        # a caller took the wrong table -- and an operator saw a traceback instead
        # of a complete map. Found by running it; no test could have, because
        # every earlier path in this command raises only `MAP_*` codes.
        log.warn("agent_pass_unavailable", agent_error=error.code.value)
        return {
            "ran": False,
            "status": "unavailable",
            "reason": error.code.value,
            "detail": error.message,
        }
    return {
        "ran": True,
        "status": outcome.status,
        "extractor_id": outcome.extractor_id or None,
        "written": outcome.written,
        "fact_count": outcome.fact_count,
        "audit_rules": list(outcome.audit_rules),
        "store_rows_written": 0,
    }


def _glue_runner(configured_adapter: str | None) -> Any:
    """Build 0's seam, wrapped in Build 1's port. `04` §3.

    Constructed **only after the gate opened**, so a run without `--agent` never
    reaches an adapter, an endpoint or a credential.
    """
    from adopt_agent import Runner
    from adopt_cli.commands.agent import adapter_settings, prompts_root
    from adopt_cli.glue_runner import SeamGlueRunner

    offline, adapter, model, endpoint = adapter_settings()
    chosen = configured_adapter or adapter
    runner = Runner(
        annex=_null_annex(),
        scope_ref="adopt-map-glue",
        skills_root=prompts_root(),
        offline=offline,
        adapter_id=chosen,
        model=model,
        endpoint=endpoint,
    )
    return SeamGlueRunner(runner, adapter=chosen)


def _null_annex() -> Any:
    """The runtime annex the seam records into.

    `adopt map` keeps no annex: `03` §4 gives this build eleven tables and none of
    them is one, and Build 0's `AnnexRecords` is a separate store outside the
    manifest (`BACKLOG.md` B-05). So the seam is handed a recorder that finds no
    prior run and keeps none -- idempotent replay is a Build 0 optimisation this
    pass does not get, and paying twice for the same question is the honest cost
    of not inventing a store.
    """

    class _NoAnnex:
        def find_run(self, *, scope_ref: str, idempotency_key: str) -> None:
            return None

        def record_run(self, *args: Any, **kwargs: Any) -> None:
            return None

    return _NoAnnex()


def _registry() -> ExtractorRegistry:
    """Every registered extractor, with the packs configuration enables.

    **Registration and enablement are two decisions and this keeps them two.**
    Every pack's extractors are registered; `ExtractorRegistry.plan` then filters
    by `enabled_packs`, and `skipped()` records `pack_disabled` for the rest. A
    disabled pack that was never registered would be indistinguishable from one
    that does not exist, and `01` F13.5 wants the reason on the first screen.

    The enabled set is `DEFAULT_ENABLED_PACKS` -- `common` and, from S1.4, `web`
    (`01` §9) -- overridden by `[extractors]` in `.adopt/config.toml`. **S1.4 had
    to build that override path**: `05` S1.1's checkbox for strict section parsing
    was marked complete with nothing behind it, so until now no configuration
    could reach pack enablement at all (`adopt_cli.map_config`).
    """
    from adopt_extractors_ai import pack as ai_pack
    from adopt_extractors_common import pack as common_pack
    from adopt_extractors_data import pack as data_pack
    from adopt_extractors_lowcode import pack as lowcode_pack
    from adopt_extractors_platform import pack as platform_pack
    from adopt_extractors_web import pack as web_pack

    configuration = load_map_config(project_config_path())
    enabled = configuration.extractors.enabled_packs(defaults=DEFAULT_ENABLED_PACKS)
    registry = ExtractorRegistry(enabled_packs=enabled)
    registry.register_all(common_pack())
    registry.register_all(web_pack())
    registry.register_all(ai_pack())
    registry.register_all(platform_pack())
    registry.register_all(lowcode_pack())
    registry.register_all(data_pack())
    return registry


def _writer_for(handle: Any) -> SurfaceWriter:
    """Compose the writer from the handle's facades.

    Here rather than in `adopt_map`, because `no-raw-sqlite` names `adopt_map` a
    source module and follows indirect chains: the package declares ports and is
    handed a realization (CR-34/CR-37's pattern, `adopt_map.ports`).
    """
    from adopt_store.revisions import (
        BindingRevisionDraft,
        IdentityRevisionDraft,
        KnowledgeRevisionDraft,
    )

    return SurfaceWriter(
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


def _formats(value: str, *, as_json: bool) -> tuple[str, ...]:
    """`02` §8's `--format`, validated. An unknown value is `MAP_USAGE`.

    Refused rather than skipped, because a caller who typed `--format markdown`
    and received `md,json,mermaid` would get artifacts they did not ask for and
    conclude the flag works.
    """
    selected = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = sorted(set(selected) - _FORMATS)
    if unknown or not selected:
        _fail(
            AdoptError(
                ErrorCode.MAP_USAGE,
                message=f"--format {value!r} names {unknown or 'nothing'}",
                hint=f"Valid formats are {', '.join(sorted(_FORMATS))}, comma-separated.",
            ),
            as_json=as_json,
        )
    return selected


def _resolve(
    *,
    store: Path | None,
    firm: str,
    engagement: str,
    system: str,
    environment: str | None,
    archetype: Archetype,
    as_json: bool,
) -> ResolvedScope:
    """`02` §2 rules 1-5, under a **read-only** handle."""
    handle = open_configured_store(store, read_only=True)
    try:
        boundary = handle.boundary().current(
            system_id=system, environment_id=environment
        ) or handle.boundary().current(system_id=system, environment_id=None)

        return resolve_scope(
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
    Build 0 observation rather than a Build 1 repair, recorded as OD-10 and
    scheduled as `BACKLOG.md` B-07 item 3; widening `resolve_all` itself would
    change how every key in the registry resolves. `02` §2 rule 1 binds
    `adopt map` specifically, so `adopt map` reads the files for its own keys and
    nothing else changes.
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


def _fail(error: AdoptError, *, as_json: bool) -> NoReturn:
    """Render the envelope to stderr and exit with `02` §8's code.

    `map_exit_code_for` raises `KeyError` for a Build 0 code, deliberately: a
    non-`MAP_*` code reaching here means a caller took the wrong exit table, and
    a plausible default would hide it.
    """
    emit_error(error.to_envelope(), as_json=as_json)
    raise typer.Exit(map_exit_code_for(error.code))
