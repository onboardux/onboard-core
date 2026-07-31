"""`adopt-schema {generate,lint,migrate}` -- the schema author's command surface.

Separate from the `adopt` CLI on purpose: these are the commands that change the
schema, and they are run by whoever is editing `schema/canonical.yaml`, not by an
FDE in a client environment.

Exit codes are the contracts §13 mapping, carried by `AdoptError` itself, so this
module does not hold a second copy of it.
"""

import sys
from typing import Annotated

import typer

from adopt_obs import AdoptError, ErrorCode, ExitCode, get_logger
from adopt_schema.generate import TARGETS, repo_root
from adopt_schema.generate import generate as run_generate
from adopt_schema.lint import lint as run_lint
from adopt_schema.lint import manifest_at_ref
from adopt_schema.manifest import load_manifest
from adopt_schema.migrate import new_migration

__all__ = ["app", "main"]

app = typer.Typer(
    name="adopt-schema",
    help="Author the canonical schema: generate every target, lint a diff, write a migration.",
    no_args_is_help=True,
    add_completion=False,
)

migrate_app = typer.Typer(help="Author and inspect migration files.", no_args_is_help=True)
app.add_typer(migrate_app, name="migrate")


@app.command()
def generate(
    check: Annotated[
        bool,
        typer.Option("--check", help="Fail with SCHEMA_GENERATED_DRIFT instead of writing."),
    ] = False,
) -> None:
    """Emit all four targets from the manifest, or prove none has drifted."""
    written = run_generate(check=check)
    if check:
        typer.echo(f"schema generate --check: OK ({', '.join(TARGETS)})")
        return
    for path in written:
        typer.echo(f"wrote {path}")


@app.command("lint")
def lint_command(
    base: Annotated[
        str,
        typer.Option("--base", help="The git ref to compare the working-tree manifest against."),
    ],
) -> None:
    """Reject every non-additive change between a base ref and the working tree."""
    violations = run_lint(manifest_at_ref(base), load_manifest())
    if not violations:
        typer.echo(f"schema lint --base {base}: OK (additive)")
        return
    for violation in violations:
        typer.echo(violation.render(), err=True)
    raise AdoptError(
        ErrorCode.SCHEMA_NON_ADDITIVE,
        message=f"{len(violations)} non-additive change(s) against {base}",
        hint="Additive-only binds from the 0.3.0 tag. Add a new column, or retire the old "
        "one with `retired_in_version` and leave the physical object in place.",
    )


@migrate_app.command("new")
def migrate_new(
    slug: Annotated[str, typer.Argument(help="Lowercase words joined by underscores.")],
    dialect: Annotated[str, typer.Option("--dialect", help="sqlite or postgres.")] = "sqlite",
) -> None:
    """Scaffold the next migration file, with its back-out note unfilled."""
    path = new_migration(repo_root(), dialect, slug)
    typer.echo(f"wrote {path.relative_to(repo_root())}")
    typer.echo("Fill in the `-- back-out:` note. A migration without one cannot be applied.")


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point carrying the contracts §13 exit-code mapping."""
    log = get_logger("adopt_schema")
    try:
        app(args=argv, standalone_mode=False)
    except AdoptError as error:
        typer.echo(error.message, err=True)
        if error.hint:
            typer.echo(error.hint, err=True)
        log.error("schema.failed", code=str(error.code), category=str(error.category))
        return error.exit_code
    except typer.Exit as error:
        return int(error.exit_code)
    return ExitCode.SUCCESS


if __name__ == "__main__":  # pragma: no cover -- exercised through the console script
    sys.exit(main())
