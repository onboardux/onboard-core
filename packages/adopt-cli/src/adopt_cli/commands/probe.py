"""`adopt probe add` and `adopt probe run` -- Build 5's verbs.

**Every `adopt_probe` import happens inside a command body, not at module
import.** v6.1 §2.1 requires new verbs to register lazily so `CLI_COLD_START_MS`
holds, and that budget is already tight -- 962 ms p95 against a 400 ms budget on
a developer machine. `adopt version` must not pay for a YAML parser and a TLS
context it never uses.

**The `probe` command group already exists and belongs to `commands/policy.py`**,
which has owned `adopt probe manifest validate` since Build 0. This module
registers onto that same group rather than declaring a second one: two typers
named `probe` is either a duplicate group or a silent shadow, depending on
registration order. The verbs live here and the group lives there, which keeps
Build 0's validator and Build 5's runner in separate files while an operator sees
one command.

**`run FILE` executes without persisting anything, and that is deliberate**
(sprint-plan D-10). It is the authoring loop -- write a probe, run it, fix it,
run it again -- and it is also the negative control: `adopt probe run rogue.yaml`
is refused by the parse before any socket opens, so a probe nobody added leaves
no definition, no revision and no run behind. Persisted history belongs to probes
somebody added on purpose.
"""

from pathlib import Path
from typing import Annotated

import typer

from adopt_cli.commands.policy import probe_app
from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store, writer_identity

__all__ = ["add", "baseline", "diff", "run"]

FileArgument = Annotated[Path, typer.Argument(help="The probe YAML file.")]
TargetArgument = Annotated[
    str | None,
    typer.Argument(
        help="A probe name, or a path to a probe file to run without storing it. "
        "Omit it and pass --all to run every probe in scope."
    ),
]
AllOption = Annotated[bool, typer.Option("--all", help="Run every probe defined in the scope.")]
ScopeOption = Annotated[
    str | None,
    typer.Option("--scope", help="firm/engagement/system/environment. Defaults to the store's."),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]
NetworkOption = Annotated[
    bool,
    typer.Option(
        "--allow-network",
        help="Permit the model adapter to be reached for `prompt` steps. An `http` "
        "step always reaches its declared hosts; this governs the agent seam only.",
    ),
]


def _read(path: Path) -> str:
    """The probe file's text.

    Raises:
        AdoptError: ``KNOWLEDGE_SOURCE_UNREADABLE`` when the file cannot be read.
            Reused rather than given a probe-specific code: v6.1 §6 B5 authorizes
            exactly three new probe codes and "your file is not there" is not one
            of them.
    """
    from adopt_obs import AdoptError, ErrorCode

    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AdoptError(
            ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE,
            message=f"cannot read the probe file {path}",
            hint="Check the path and its permissions.",
        ) from exc


@probe_app.command("add")
def add(
    file: FileArgument,
    scope: ScopeOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Validate a probe file and store it as a definition plus a revision."""
    # Lazy by design -- see the module docstring.
    from adopt_probe import parse_probe

    from adopt_cli.commands._map_support import resolve_scope
    from adopt_cli.commands._probe_support import add_probe

    spec = parse_probe(_read(file))
    handle = open_configured_store(store, read_only=False, verb="probe add")
    try:
        resolved = resolve_scope(handle, scope)
        outcome, probe_id, revision_id = add_probe(
            handle, resolved, spec, actor_id=writer_identity()
        )
    finally:
        handle.close()

    emit(
        {
            "probe": spec.name,
            "outcome": outcome,
            "probe_id": probe_id,
            "revision": revision_id,
            "safe_path": spec.safe_path,
            "declared_hosts": sorted(spec.declared_hosts),
            "steps": len(spec.steps),
            "exercises": list(spec.exercises),
        },
        as_json=json_output,
        title="adopt probe add",
    )


@probe_app.command("run")
def run(
    target: TargetArgument = None,
    run_all: AllOption = False,
    scope: ScopeOption = None,
    store: StoreOption = None,
    allow_network: NetworkOption = False,
    json_output: JsonOption = False,
) -> None:
    """Execute probes, recording what each one observed.

    Exits `1` when any probe failed or was blocked, so a CI step running probes
    fails the build rather than reporting green over a refusal.
    """
    import sys

    from adopt_cli.commands._probe_support import run_targets
    from adopt_obs import AdoptError, ErrorCode

    if not run_all and target is None:
        raise AdoptError(
            ErrorCode.MANIFEST_INVALID,
            message="name a probe, pass a probe file, or use --all",
            hint="`adopt probe run --all` runs every probe in the scope; "
            "`adopt probe run FILE` runs one without storing it.",
        )

    payload = run_targets(
        target=target,
        run_all=run_all,
        scope_text=scope,
        store_override=store,
        allow_network=allow_network,
        read_file=_read,
    )
    emit(payload, as_json=json_output, title="adopt probe run")
    if payload["failed"]:
        sys.exit(1)


SetOption = Annotated[
    bool,
    typer.Option(
        "--set",
        help="Version the latest recorded run of each probe as its baseline.",
    ),
]


@probe_app.command("baseline")
def baseline(
    set_baseline: SetOption = False,
    scope: ScopeOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Version how each probe's system behaves today.

    `--set` takes each active probe's latest run that observed something and
    writes it as a `baseline_version`. A run that failed or was refused by the
    manifest is never eligible: versioning a fault as *how the system behaves*
    would make the next clean run read as drift away from a bug.

    A run whose outcome was `diff` **is** eligible, and the report says so. That
    is what re-baselining after drift is -- a human accepting a change -- and it
    is deliberately not silent.
    """
    from adopt_cli.commands._map_support import resolve_scope
    from adopt_cli.commands._probe_support import set_baselines
    from adopt_obs import AdoptError, ErrorCode

    if not set_baseline:
        raise AdoptError(
            ErrorCode.MANIFEST_INVALID,
            message="`adopt probe baseline` needs --set",
            hint="v1 has one baseline operation: `adopt probe baseline --set` versions "
            "the latest recorded runs. Listing and pruning baselines are not in this "
            "build, so a bare `baseline` would do nothing and say it worked.",
        )

    handle = open_configured_store(store, read_only=False, verb="probe baseline")
    try:
        payload = set_baselines(handle, resolve_scope(handle, scope))
    finally:
        handle.close()

    emit(payload, as_json=json_output, title="adopt probe baseline")


@probe_app.command("diff")
def diff(
    scope: ScopeOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Name what changed since the baseline -- and who changed it.

    Exits `4` when any probe drifted: degraded-with-findings, the same contract
    `adopt doctor` and `adopt map --check-expected` already use. The command
    **worked**; it found something a human must see.

    A probe whose latest run is of a different revision than its baseline
    reports `probe_changed` and never drift. The probe file was edited, so the
    question changed -- and calling that a change in the client's system is the
    one mistake that would train an FDE to ignore this command.
    """
    import sys

    from adopt_cli.commands._map_support import resolve_scope
    from adopt_cli.commands._probe_support import diff_probes

    handle = open_configured_store(store, read_only=True)
    try:
        payload = diff_probes(handle, resolve_scope(handle, scope))
    finally:
        handle.close()

    emit(payload, as_json=json_output, title="adopt probe diff")
    if payload["drifted"]:
        from adopt_obs import ExitCode

        sys.exit(ExitCode.DEGRADED_WITH_FINDINGS)
