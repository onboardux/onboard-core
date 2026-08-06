"""`adopt store info | doctor | migrate` -- contracts §14.

**A coverage gap, closed here.** `02` §14 defines this command group and `01`
F16.1 lists it among the verbs, and **no sprint task in `05` ever created it** --
S2 built `adopt_store.doctor` as a library and nothing gave it a command line.
It surfaces at S8 because Final Output Validation item 7 runs `adopt store info`
to prove the OSS path needs no Postgres. Recorded as a gap rather than folded in
silently, the way S0's missing `error_registry_sync.py` and S6's missing
`adopt init` were.

All three emit the one envelope §14 declares -- `{schema_version, counts{},
findings[]}` -- because a group whose subcommands each invented a shape would
make the contract three contracts.

**`info` and `doctor` open read-only.** Looking must not repair: §8's incident
card says rebuilding a disagreeing cache first destroys the evidence of whatever
wrote it. Only `migrate` opens for writing, and it is the only one that can.
"""

from pathlib import Path
from typing import Annotated

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store, open_for_migration
from adopt_obs import ExitCode

__all__ = ["app"]

app = typer.Typer(
    name="store",
    help="Inspect, diagnose and migrate a store. Looking never repairs.",
    no_args_is_help=True,
)

StoreOption = Annotated[
    Path | None,
    typer.Option("--store", help="Store path. Defaults to the resolved ADOPT_STORE_PATH."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


@app.command()
def info(store: StoreOption = None, json_output: JsonOption = False) -> None:
    """Schema version and row counts. Reads; never writes."""
    with open_configured_store(store, read_only=True) as handle:
        payload = {
            "schema_version": handle.schema_version,
            "counts": handle.counts(),
            "findings": [],
        }
    emit(payload, as_json=json_output, title="adopt store info")


@app.command()
def doctor(store: StoreOption = None, json_output: JsonOption = False) -> None:
    """Report every finding in the store, and change nothing.

    Exit `4` with findings -- degraded success, not failure. The store opened,
    the checks ran, and their answers are trustworthy; what needs a human is
    what they found.
    """
    with open_configured_store(store, read_only=True) as handle:
        findings = handle.doctor()
        payload = {
            "schema_version": handle.schema_version,
            "counts": handle.counts(),
            "findings": [
                {
                    "code": finding.code.value,
                    "table": finding.table,
                    "subject_id": finding.subject_id,
                    "detail": finding.detail,
                }
                for finding in findings
            ],
        }

    emit(payload, as_json=json_output, title="adopt store doctor")
    if findings:
        raise typer.Exit(ExitCode.DEGRADED_WITH_FINDINGS)


@app.command()
def migrate(store: StoreOption = None, json_output: JsonOption = False) -> None:
    """Apply pending forward migrations, then report the resulting version.

    Forward-only, always: implementation spec §7.4 states the schema has no
    rollback, and recovery is older code against a newer store.
    """
    with open_for_migration(store) as handle:
        payload = {
            "schema_version": handle.schema_version,
            "counts": handle.counts(),
            "findings": [],
        }
    emit(payload, as_json=json_output, title="adopt store migrate")
