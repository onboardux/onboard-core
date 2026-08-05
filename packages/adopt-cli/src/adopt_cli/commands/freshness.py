"""`adopt freshness resolve --item ID` -- contracts §14.

Emits `{state, level, deciding_rule}`. **The deciding rule is not decoration.**
CUJ-5 requires an operator to be told *why* knowledge stopped being fresh --
`observation_stale` because a webhook is failing is a different problem, with a
different owner, from `stale` because the referent moved -- and a state with no
reason cannot be acted on and cannot be argued with.

Opens the store **read-only**. `resolve_freshness` writes nothing by contract
(contracts §6), and a read-only handle is that guarantee expressed as a property
of the file rather than as a rule the command has to remember.
"""

from pathlib import Path
from typing import Annotated

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store
from adopt_freshness import resolve_freshness

__all__ = ["app"]

app = typer.Typer(
    name="freshness",
    help="Resolve freshness across source, binding, knowledge-revision and system levels.",
    no_args_is_help=True,
)

ItemOption = Annotated[
    str,
    typer.Option("--item", help="The knowledge item id.", show_default=False),
]
StoreOption = Annotated[
    Path | None,
    typer.Option("--store", help="Store path. Defaults to the resolved ADOPT_STORE_PATH."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


@app.command()
def resolve(
    item: ItemOption,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Resolve one item's freshness and report the rule that decided it."""
    with open_configured_store(store, read_only=True) as handle:
        resolution = resolve_freshness(handle.freshness_records(), item)

    emit(
        {
            "state": resolution.state,
            "level": resolution.level,
            "deciding_rule": resolution.deciding_rule,
        },
        as_json=json_output,
        title="adopt freshness resolve",
    )
