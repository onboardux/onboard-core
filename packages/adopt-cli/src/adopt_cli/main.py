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
from typing import Annotated

import click
import typer

from adopt_cli.commands import agent as agent_commands
from adopt_cli.commands import boundary as boundary_command
from adopt_cli.commands import coverage as coverage_commands
from adopt_cli.commands import detect as detect_command
from adopt_cli.commands import doctor as doctor_command
from adopt_cli.commands import freshness as freshness_commands
from adopt_cli.commands import identity as identity_commands
from adopt_cli.commands import init as init_command
from adopt_cli.commands import interchange as interchange_commands
from adopt_cli.commands import knowledge as knowledge_commands
from adopt_cli.commands import map_command as map_commands
from adopt_cli.commands import policy as policy_commands
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
# `adopt_knowledge` inside each command body, so `adopt version` pays for four
# typer signatures and nothing else.
app.command("ingest")(knowledge_commands.ingest)
app.command("bind")(knowledge_commands.bind)
app.command("gaps")(knowledge_commands.gaps)
app.command("review")(knowledge_commands.review)

# Registered as bare commands rather than a group: contracts §14 names them
# `adopt export DIR` and `adopt import DIR`. `import` is a Python keyword, so the
# function is `import_` and the command name is given explicitly -- the CLI
# surface is the contract, not the identifier that happens to implement it.
app.command("export")(interchange_commands.export)
app.command("import")(interchange_commands.import_)

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


def _exit_code_of(error: click.ClickException | click.exceptions.Exit) -> int:
    if isinstance(error, click.exceptions.Exit):
        return int(error.exit_code)
    if isinstance(error, click.UsageError):
        error.show()
        return ExitCode.USAGE_ERROR
    error.show()
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
    except click.exceptions.Abort:
        log.warn("cli.aborted")
        return ExitCode.OPERATIONAL_FAILURE
    except (click.ClickException, click.exceptions.Exit) as error:
        return _exit_code_of(error)
    # A command returning an `int` returned it through `typer.Exit`; commands
    # return `None` otherwise, so there is no value here to confuse with a code.
    return result if isinstance(result, int) else ExitCode.SUCCESS


if __name__ == "__main__":  # pragma: no cover -- exercised through the release entry point
    sys.exit(main())
