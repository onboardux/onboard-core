"""`adopt map` -- Build 1's verb, and the first command that creates an identity.

**Every `adopt_map` import happens inside the command body, not at module
import.** v6.1 §2.1 requires new verbs to register lazily so `CLI_COLD_START_MS`
holds, and that budget is already tight: 962 ms p95 against a 400 ms budget on a
developer machine. `adopt version` must not pay for `ast`, `yaml` and six
extractors it never uses.
"""

from pathlib import Path
from typing import Annotated, Any

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store

__all__ = ["map_command"]

PathArgument = Annotated[
    Path, typer.Argument(help="Repository root to map. Defaults to the working directory.")
]
ScopeOption = Annotated[
    str | None,
    typer.Option("--scope", help="firm/engagement/system/environment. Defaults to the store's."),
]
PacksOption = Annotated[
    str | None,
    typer.Option(
        "--packs",
        help="Comma-separated pack names, overriding archetype selection. For a mixed "
        "system whose archetype names only its dominant half.",
    ),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def map_command(
    path: PathArgument = Path(),
    scope: ScopeOption = None,
    packs: PacksOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Extract identities from a repository, deterministically and offline."""
    # Lazy by design -- see the module docstring.
    from adopt_map import SourceTree, registry, run_map, select_packs

    from adopt_cli.commands._map_support import resolve_scope, system_archetype

    tree = SourceTree.scan(path)
    # Writable, and deliberately **not** migrating: `init` creates the store, and
    # a `map` that silently migrated would upgrade a client's database as a side
    # effect of being asked to read their repository.
    handle = open_configured_store(store, read_only=False)
    try:
        resolved = resolve_scope(handle, scope)
        selected = select_packs(
            system_archetype(handle, resolved),
            override=[name.strip() for name in packs.split(",")] if packs else None,
            available=registry(),
        )
        report = run_map(
            tree=tree,
            scope=resolved,
            packs=selected,
            writer=handle.identities(),
            records=handle.revision_records(),
        )
    finally:
        handle.close()

    emit(build_payload(report), as_json=json_output, title="adopt map")
    if report.failed:
        # Exit 1, not 4: an extractor that raised means the map is **incomplete
        # in an unknown way**, which is an operational failure rather than a
        # finding a human can act on. B-08's whole cost was that this case
        # exited 0 and looked like a smaller system.
        raise typer.Exit(1)


def build_payload(report: Any) -> dict[str, Any]:
    """The §14 envelope for a map run."""
    return {
        "scope": report.scope,
        "packs": list(report.packs),
        "identities_seen": report.identities_seen,
        "files_walked": report.files_walked,
        "files_unmapped": report.files_unmapped,
        "files_oversized": list(report.files_oversized),
        "extractors": [
            {
                "extractor": outcome.extractor,
                "version": outcome.version,
                "pack": outcome.pack,
                "observations": outcome.observations,
                "status": outcome.status,
                "detail": outcome.detail,
            }
            for outcome in report.outcomes
        ],
        "failed": [outcome.extractor for outcome in report.failed],
    }
