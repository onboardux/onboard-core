"""`adopt map` -- Build 1's verb, and the first command that creates an identity.

**Every `adopt_map` import happens inside the command body, not at module
import.** v6.1 §2.1 requires new verbs to register lazily so `CLI_COLD_START_MS`
holds, and that budget is already tight: 962 ms p95 against a 400 ms budget on a
developer machine. `adopt version` must not pay for `ast`, `yaml` and eleven
extractors it never uses.

**`--report` and `--check-expected` map first, then read the store.** Neither is
a separate mode: mapping is idempotent, so running it again costs a walk and
writes nothing, and the alternative -- a report that renders whatever the store
happened to hold from an earlier run -- would answer a question nobody asked. The
counts and the listing still come from the *rows*, which is v6.1's requirement
and the difference between reporting the system and reporting ourselves.
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
ReportOption = Annotated[
    bool,
    typer.Option(
        "--report", help="Render the stored map: counts by kind, listing with provenance."
    ),
]
CheckExpectedOption = Annotated[
    Path | None,
    typer.Option(
        "--check-expected",
        help="A curated file of expected identity URIs. Exits 4 naming every miss.",
    ),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def map_command(
    path: PathArgument = Path(),
    scope: ScopeOption = None,
    packs: PacksOption = None,
    report_map: ReportOption = False,
    check_expected: CheckExpectedOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Extract identities from a repository, deterministically and offline."""
    # Lazy by design -- see the module docstring.
    from adopt_map import (
        SourceTree,
        build_report,
        load_expected,
        missing_identities,
        registry,
        run_map,
        select_packs,
    )

    from adopt_cli.commands._map_support import (
        resolve_scope,
        scope_identities,
        scope_revisions,
        stored_identities,
        system_archetype,
    )

    expected = load_expected(_read_expected(check_expected)) if check_expected else ()
    misses: tuple[str, ...] = ()

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
        # Read **before** the run. Afterwards every new identity is already
        # stored, so nothing has appeared and no move is detectable.
        before = stored_identities(handle, resolved)
        run = run_map(
            tree=tree,
            scope=resolved,
            packs=selected,
            writer=handle.identities(),
            records=handle.revision_records(),
            stored=before,
        )
        payload = build_payload(run)
        if report_map or expected:
            identities = scope_identities(handle, resolved)
            if report_map:
                rendered = build_report(
                    identities=identities,
                    revisions=scope_revisions(handle, identities),
                    files_walked=run.files_walked,
                    files_unmapped=run.files_unmapped,
                    files_oversized=run.files_oversized,
                )
                # Merged at the top level rather than nested: the human renderer
                # draws a table for a list of dicts one level down and prints
                # raw JSON for one nested deeper, and a report nobody can read is
                # not a report. `files_walked` and the rest are already here.
                for key in ("identities", "counts_by_kind", "listing"):
                    payload[key] = rendered[key]
            if expected:
                misses = missing_identities(expected, (row.uri for row in identities))
    finally:
        handle.close()

    if expected:
        payload["expected"] = _expected_payload(expected, misses)
    emit(payload, as_json=json_output, title="adopt map")

    if run.failed:
        # Exit 1, not 4: an extractor that raised means the map is **incomplete
        # in an unknown way**, which is an operational failure rather than a
        # finding a human can act on. B-08's whole cost was that this case
        # exited 0 and looked like a smaller system. It is checked before the
        # recall floor because a failed extractor makes every miss below
        # unreliable evidence about the extractors that did run.
        raise typer.Exit(1)
    if expected and misses:
        # Exit 4: the command **worked**, and found something a human must see.
        # `MAP_EXPECTED_IDENTITY_MISSING` is carried in the payload rather than
        # raised, because raising it would map to exit 1 and turn "your map is
        # incomplete" into "the command failed" -- a different sentence sending
        # the reader somewhere else entirely (contracts §13, `_NEVER_RAISED`).
        raise typer.Exit(4)


def _read_expected(path: Path) -> str:
    """The expected-identities file, or a typed refusal naming the path.

    A missing list is a usage error and not an empty list: `--check-expected`
    against a file that is not there would otherwise pass, reporting a perfect
    recall floor over nothing -- the `0/0 covered (100%)` failure CR-67 found in
    a gate that had gone blind.
    """
    from adopt_obs import AdoptError, ErrorCode

    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise AdoptError(
            ErrorCode.MAP_EXPECTED_LIST_UNREADABLE,
            message=f"the expected-identities file {str(path)!r} could not be read",
            hint="Pass a readable file of one identity URI per line; `#` comments and "
            "blank lines are ignored. An unreadable list is refused rather than "
            "treated as empty, because an empty list passes every check.",
        ) from error


def _expected_payload(expected: tuple[str, ...], misses: tuple[str, ...]) -> dict[str, Any]:
    """The recall-floor result: how many were checked, and **which** are absent."""
    return {
        "checked": len(expected),
        "present": len(expected) - len(misses),
        "missing": list(misses),
        # One finding per miss, each naming the URI. A count alone would say the
        # map is incomplete without saying what it is missing, which is the
        # difference between a number and something a person can act on.
        "findings": [{"code": "MAP_EXPECTED_IDENTITY_MISSING", "uri": uri} for uri in misses],
    }


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
        # Moves written, plus the two conclusions that write nothing. Absence
        # and ambiguity are reported rather than acted on (plan decision D6):
        # absence is not death, and a guessed pairing is a permanent alias
        # between two referents nobody checked.
        "moves": [{"from": move.from_uri, "to": move.to.uri} for move in report.moves.moves],
        "absent": list(report.moves.absent),
        "ambiguous_moves": [list(group) for group in report.moves.ambiguous],
    }
