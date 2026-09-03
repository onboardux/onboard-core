"""The `adopt` CLI entry point.

Three postures are set here and inherited by every command added later:

* **`--json` on every command.** The JSON envelope is a contract from 0.3.0.
* **Offline by default.** Network egress requires `--allow-network`. In offline
  mode the process opens no socket other than to a configured local adapter
  endpoint.
* **Typed errors map to stable exit codes.** `0` success, `1` operational
  failure, `2` usage error, `3` policy refusal, `4` degraded success with
  findings. The mapping lives in `adopt_obs.errors` -- a second copy here would
  be a second place to get it wrong.
"""

import sys
from typing import Annotated, Any, Final, cast

import click
import typer

from adopt_cli.commands import agent as agent_commands
from adopt_cli.commands import answer as answer_commands
from adopt_cli.commands import ask as ask_commands
from adopt_cli.commands import boundary as boundary_command
from adopt_cli.commands import ci_sense as ci_sense_commands
from adopt_cli.commands import coverage as coverage_commands
from adopt_cli.commands import detect as detect_command
from adopt_cli.commands import doctor as doctor_command
from adopt_cli.commands import draft as draft_command
from adopt_cli.commands import freshness as freshness_commands
from adopt_cli.commands import handover as handover_commands
from adopt_cli.commands import identity as identity_commands
from adopt_cli.commands import init as init_command
from adopt_cli.commands import interchange as interchange_commands
from adopt_cli.commands import knowledge as knowledge_commands
from adopt_cli.commands import map_command as map_commands
from adopt_cli.commands import pack as pack_commands
from adopt_cli.commands import policy as policy_commands

# Build 5. Imported for its side effect: `commands/probe.py` registers `add`,
# `run`, `baseline` and `diff` onto `policy_commands.probe_app`, the group
# Build 0 already owns for
# `adopt probe manifest validate`. It must be imported **before** that group is
# attached below, or the verbs are declared on a typer nobody mounted. The module
# itself imports `adopt_probe` only inside its command bodies, so this costs
# typer options and nothing else.
from adopt_cli.commands import probe as probe_commands  # noqa: F401
from adopt_cli.commands import pull as pull_commands
from adopt_cli.commands import refresh as refresh_commands
from adopt_cli.commands import serve as serve_commands
from adopt_cli.commands import store as store_commands
from adopt_cli.commands import version as version_command
from adopt_cli.json_out import emit, emit_error
from adopt_obs import AdoptError, ExitCode, get_logger

__all__ = ["app", "main"]

app = typer.Typer(
    name="adopt",
    help="Adoption-Phase Platform CLI. Offline by default; no telemetry, ever.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(identity_commands.app)
app.add_typer(coverage_commands.app)
app.add_typer(freshness_commands.app)
app.add_typer(store_commands.app)
app.add_typer(policy_commands.probe_app)
app.add_typer(policy_commands.envelope_app)
app.add_typer(agent_commands.app)

# `init`, `detect` and `boundary` are bare commands, not groups: contracts §14
# names them `adopt init [path]`, `adopt detect [path]` and `adopt boundary`.
app.command("init")(init_command.init)
app.command("detect")(detect_command.detect)
app.command("boundary")(boundary_command.boundary)

# Build 1. `map_command` imports `adopt_map` inside the function body, so
# registering it here costs an import of typer options and nothing else --
# `CLI_COLD_START_MS` is measured against `adopt version`, which must not pay
# for six extractors it never runs.
app.command("map")(map_commands.map_command)

# Build 2, registered the same way and for the same reason: `knowledge` imports
# `adopt_knowledge` inside each command body, so `adopt version` pays for five
# typer signatures and nothing else.
app.command("ingest")(knowledge_commands.ingest)
app.command("harvest")(knowledge_commands.harvest)
app.command("bind")(knowledge_commands.bind)
app.command("gaps")(knowledge_commands.gaps)
app.command("review")(knowledge_commands.review)

# Build 3, registered the same way and for the same reason: `ask` imports
# `adopt_ask` and the FTS index opener inside the command body, so `adopt
# version` pays for one typer signature and nothing else.
app.command("ask")(ask_commands.ask)
app.command("answer")(answer_commands.answer)
app.command("serve")(serve_commands.serve)

# Build 4. `pack` imports `adopt_handover` and the coverage recompute inside the
# command body, so `adopt version` still pays for neither. `draft` is the
# single-target door onto the same drafting pass `pack --draft-missing` runs in
# bulk, and imports `adopt_knowledge.drafting` and the agent seam the same way --
# so registering the model-calling verb costs `adopt version` one typer
# signature and no adapter, which is what "offline by default" means at import
# time as well as at run time.
app.command("pack")(pack_commands.pack)
app.command("draft")(draft_command.draft)

# Registered as bare commands rather than a group: contracts §14 names them
# `adopt export DIR` and `adopt import DIR`. `import` is a Python keyword, so the
# function is `import_` and the command name is given explicitly -- the CLI
# surface is the contract, not the identifier that happens to implement it.
app.command("export")(interchange_commands.export)
app.command("import")(interchange_commands.import_)

# Build 6. `refresh` imports `adopt_map` and the annex inside its body, so the
# verb that re-walks a repository costs `adopt version` one typer signature.
# The review queue it fills is `adopt review`'s, already registered above --
# one surface, one habit (v6.1 F5), so no verb is added for the change items.
app.command("refresh")(refresh_commands.refresh)

# Build 7. `pull` refreshes this store from the plane it replicates -- v6.1 §6
# demo line 4. Registered bare rather than under a group for the reason `export`
# and `import` are: it is one verb an FDE types, and the direction it moves canon
# is the whole of what it does.
app.command("pull")(pull_commands.pull)

# Build 8. `ci-sense` is the customer's CI reporting what it sees to the plane
# that owns the canon (v6.1 §6 Build 8 F4/D10). Apache-2.0 and in this CLI
# because it runs on the customer's machine and the free layer must be able to
# read it; useless without a paid tenant endpoint, so the OSS line holds. Its
# `adopt_map` imports are inside the body for `refresh`'s reason.
app.command("ci-sense")(ci_sense_commands.ci_sense)

# Build 9. `adopt handover` is a group rather than a bare verb, because the
# event is six recorded steps plus `status` and each is separately re-runnable
# -- a single verb would hide which step an operator is asking for. Every
# `adopt_handover` import happens inside a command body, so registering seven
# typer signatures is all `adopt version` pays for (v6.1 §2.1).
app.add_typer(handover_commands.app)

JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]
NetworkOption = Annotated[
    bool,
    typer.Option(
        "--allow-network",
        help="Permit network egress for this invocation. Offline is the default posture.",
    ),
]


@app.callback()
def _root(ctx: typer.Context, allow_network: NetworkOption = False) -> None:
    """Set the process posture before any command runs."""
    ctx.ensure_object(dict)
    ctx.obj["allow_network"] = allow_network


@app.command()
def version(json_output: JsonOption = False) -> None:
    """Report the binary, schema and export versions and the build provenance."""
    emit(version_command.build_payload(), as_json=json_output, title="adopt version")


@app.command()
def doctor(json_output: JsonOption = False) -> None:
    """Report every configuration key with its resolved value and source.

    Exits `4` when there are findings: degraded success, not failure. `doctor`
    never repairs what it reports.
    """
    payload, findings = doctor_command.build_payload()
    emit(payload, as_json=json_output, title="adopt doctor")
    if findings:
        raise typer.Exit(ExitCode.DEGRADED_WITH_FINDINGS)


def _wants_json(argv: list[str] | None) -> bool:
    return "--json" in (argv if argv is not None else sys.argv[1:])


def _typer_exception_base(name: str, fallback: type[BaseException]) -> type[BaseException]:
    """The class typer's **vendored** click raises, found through typer's public surface.

    The installed typer ships its own copy of click under `typer._click`, so
    `typer.BadParameter` is not a `click.ClickException` and `typer.Exit` is not
    a `click.exceptions.Exit`. Catching only the standalone click's classes let
    every parser-level usage error -- an unknown option, a missing argument, a
    bad parameter -- escape `main()` unhandled: exit `1`, a rich traceback on
    stderr, and nothing at all on the stdout a `--json` caller was promised.

    The bases are read off `typer.BadParameter.__mro__` rather than imported
    from a `typer._click...` path, so no private module is named here and the
    same walk keeps working if typer ever un-vendors click again. The fallback
    preserves today's behaviour if a future typer restructures the hierarchy
    entirely, and `tests/unit/test_cli_usage_errors.py` pins the resolution so
    that fallback can never quietly become the normal case.
    """
    for base in typer.BadParameter.__mro__:
        if base.__name__ == name:
            return base
    return fallback


#: "The caller asked for something the parser could not accept" -- contracts
#: §13 `usage`, exit `2` -- in both copies of click.
_USAGE_ERROR_CLASSES: Final[tuple[type[BaseException], ...]] = (
    click.UsageError,
    _typer_exception_base("UsageError", click.UsageError),
)
#: Click's reportable-exception root in both copies. One that is not a
#: `UsageError` stays an operational failure, exactly as before.
_CLICK_EXCEPTION_CLASSES: Final[tuple[type[BaseException], ...]] = (
    click.ClickException,
    _typer_exception_base("ClickException", click.ClickException),
)
#: `Exit` carries its own code; `Abort` is an interrupted run.
_EXIT_CLASSES: Final[tuple[type[BaseException], ...]] = (click.exceptions.Exit, typer.Exit)
_ABORT_CLASSES: Final[tuple[type[BaseException], ...]] = (click.exceptions.Abort, typer.Abort)
_HANDLED_CLICK_CLASSES: Final[tuple[type[BaseException], ...]] = (
    _CLICK_EXCEPTION_CLASSES + _EXIT_CLASSES
)


def _exit_code_of(error: BaseException) -> int:
    """Contracts §13's mapping for click's own control-flow exceptions.

    A parser-level usage error is deliberately **not** given a §13 JSON
    envelope: §14's envelope is a command's output, and here no command ran --
    the parser refused the invocation before one could. Click's usage message on
    stderr and exit `2` is the contract, and it is what the operator reading a
    shell needs.
    """
    reported = cast(Any, error)
    if isinstance(error, _EXIT_CLASSES):
        return int(reported.exit_code)
    reported.show()
    if isinstance(error, _USAGE_ERROR_CLASSES):
        return ExitCode.USAGE_ERROR
    return ExitCode.OPERATIONAL_FAILURE


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point carrying the contracts §13 exit-code mapping.

    A typed `AdoptError` escaping a command is rendered as the one documented
    envelope and mapped to its category's exit code here, so no command has to
    remember to do it. Click's own control-flow exceptions are translated in the
    same place for the same reason.

    **`app(...)` is invoked with `standalone_mode=False`, and in that mode Click
    *returns* the exit code of a `typer.Exit` rather than raising it.** Discarding
    the return value therefore silently turned every deliberate non-zero exit
    into `0` -- including the `4` that contracts §14 gives `adopt doctor` and
    `adopt coverage recompute` when there are findings. The `except` clause below
    still catches an `Exit` raised from outside a command, so both paths are
    covered; neither on its own is.
    """
    log = get_logger("adopt_cli")
    try:
        result = app(args=argv, standalone_mode=False)
    except AdoptError as error:
        emit_error(error.to_envelope(), as_json=_wants_json(argv))
        log.error("cli.failed", code=str(error.code), category=str(error.category))
        return error.exit_code
    except _ABORT_CLASSES:
        log.warn("cli.aborted")
        return ExitCode.OPERATIONAL_FAILURE
    except _HANDLED_CLICK_CLASSES as error:
        return _exit_code_of(error)
    # A command returning an `int` returned it through `typer.Exit`; commands
    # return `None` otherwise, so there is no value here to confuse with a code.
    return result if isinstance(result, int) else ExitCode.SUCCESS


if __name__ == "__main__":  # pragma: no cover -- exercised through the release entry point
    sys.exit(main())
