"""`adopt coverage recompute` -- contracts §14.

Emits `{covered, uncovered, disagreements[]}` and exits `4` when the cache
disagrees with the recompute: **degraded success with findings**, not failure.
The distinction matters operationally -- the recompute itself worked, and its
answer is the one to trust; what is broken is the cache, and something wrote it.

`--rebuild` is opt-in and off by default, so the default invocation is the one
`store doctor` also performs: look, report, change nothing. A tool whose default
repaired the drift would destroy the evidence of the writer that caused it every
time an operator ran it to find out what was wrong (implementation spec §8).
"""

from pathlib import Path
from typing import Annotated

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store
from adopt_coverage import rebuild_cache, recompute_coverage
from adopt_obs import ExitCode

__all__ = ["app"]

app = typer.Typer(
    name="coverage",
    help="Recompute coverage. The function is the authority; the cache must alarm.",
    no_args_is_help=True,
)

SystemOption = Annotated[
    str,
    typer.Option("--system", help="The system id to evaluate.", show_default=False),
]
EnvironmentOption = Annotated[
    str | None,
    typer.Option("--environment", help="One environment id. Omit for every environment."),
]
StoreOption = Annotated[
    Path | None,
    typer.Option("--store", help="Store path. Defaults to the resolved ADOPT_STORE_PATH."),
]
RebuildOption = Annotated[
    bool,
    typer.Option(
        "--rebuild",
        help="Write the result to covered_cache. Off by default: looking must not repair.",
    ),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


@app.command()
def recompute(
    system: SystemOption,
    environment: EnvironmentOption = None,
    store: StoreOption = None,
    rebuild: RebuildOption = False,
    json_output: JsonOption = False,
) -> None:
    """Evaluate the six inputs of contracts §6 for every identity in scope."""
    with open_configured_store(store, read_only=not rebuild) as handle:
        result = recompute_coverage(handle.coverage_records(), system, environment)
        rebuilt = rebuild_cache(handle.backend, result) if rebuild else 0

    payload = {
        "covered": result.covered,
        "uncovered": result.uncovered,
        "disagreements": [
            {
                "identity_id": entry.identity_id,
                "uri": entry.uri,
                "cached": entry.cached,
                "recomputed": entry.recomputed,
            }
            for entry in result.disagreements
        ],
    }
    if rebuild:
        payload["cache_rows_written"] = rebuilt

    emit(payload, as_json=json_output, title="adopt coverage recompute")
    if result.disagreements:
        raise typer.Exit(ExitCode.DEGRADED_WITH_FINDINGS)
