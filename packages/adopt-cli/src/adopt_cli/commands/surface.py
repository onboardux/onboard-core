"""`adopt surface show|coverage|changes` -- `02` §8, PRD F11.4.

**Three read-only views. None of them writes a row**, and that is the contract
rather than a habit: every one opens the store `read_only=True`, so a write
attempt raises `STORE_READ_ONLY` from the driver rather than succeeding
somewhere unexpected.

**`changes --since` is the delta engine, and there isn't one** (B1-CR-01, `01`
F11.4). Append-only revisions *are* the delta: a re-run writes exactly one
revision per changed referent, so "what changed since Tuesday" is a query over
the revision chain and not a second computation that could disagree with it. The
withdrawn `--at` / `--diff` engine would have been a second, drift-prone truth.

**PRD Q6 is due this sprint and this command is the answer** -- *"Does
`adopt surface changes` belong here or in Build 5's gap tooling?"* Default:
here, read-only over revisions. Proceeding on the stated default, flagged, and
recorded as **B1-CR-57**: it is reversible (a read-only view moves without a
schema change), it touches no persisted shape, and Build 5 owns *gap ranking*,
which is a different question from *what changed*.

**Every coverage figure comes from `recompute_coverage()`** (`01` F10.2). The
`coverage` subcommand routes through `adopt_map.coverage.report_coverage` with
`rebuild=False`, so looking never repairs -- the same argument `adopt coverage
recompute` makes for its own default.
"""

from pathlib import Path
from typing import Annotated, Any

import typer
from adopt_map.coverage import report_coverage

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store
from adopt_identity import parse_uri
from adopt_model import Identity, IdentityRevision
from adopt_obs import AdoptError, ErrorCode, MapExitCode, map_exit_code_for

__all__ = ["app"]

app = typer.Typer(
    name="surface",
    help="Read the surface map: one identity, coverage, or what changed.",
    no_args_is_help=True,
)

StoreOption = Annotated[
    Path | None,
    typer.Option("--store", help="Store path. Defaults to the resolved ADOPT_STORE_PATH."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]
SystemOption = Annotated[str, typer.Option("--system", help="The system id.", show_default=False)]
EnvironmentOption = Annotated[str | None, typer.Option("--environment", help="One environment id.")]


@app.command("show")
def show(
    uri: Annotated[str, typer.Argument(help="The canonical identity URI.")],
    store: StoreOption = None,
    as_json: JsonOption = False,
) -> None:
    """One identity and its revision chain, newest first.

    The URI is **validated by parsing it**, not by pattern-matching it: `parse_uri`
    is the same code that built it, so a URI this command accepts is one the store
    could actually hold.
    """
    try:
        parse_uri(uri)
    except AdoptError as error:
        emit({"error": error.to_envelope()}, as_json=as_json, title="adopt surface show")
        raise typer.Exit(map_exit_code_for(ErrorCode.MAP_USAGE)) from error

    handle = open_configured_store(store, read_only=True)
    try:
        records = handle.export_records()
        identity = next(
            (row for row in records.table_rows("identity", Identity) if row.uri == uri), None
        )
        if identity is None:
            emit(
                {"identity_uri": uri, "found": False},
                as_json=as_json,
                title="adopt surface show",
            )
            raise typer.Exit(MapExitCode.COMPLETE)
        revisions = [
            row
            for row in records.table_rows("identity_revision", IdentityRevision)
            if row.identity_id == identity.id
        ]
        emit(
            {
                "identity_uri": identity.uri,
                "identity_kind": identity.identity_kind,
                "namespace": identity.namespace,
                "local_key": identity.local_key,
                "first_seen": _stamp(identity.first_seen),
                "last_seen": _stamp(identity.last_seen),
                "revisions": [_revision_payload(row) for row in _newest_first(revisions)],
            },
            as_json=as_json,
            title="adopt surface show",
        )
        raise typer.Exit(MapExitCode.COMPLETE)
    finally:
        handle.close()


@app.command("coverage")
def coverage(
    system: SystemOption,
    environment: EnvironmentOption = None,
    by_kind: Annotated[
        bool, typer.Option("--by-kind", help="Break the ratio down by kind.")
    ] = False,
    store: StoreOption = None,
    as_json: JsonOption = False,
) -> None:
    """Coverage for one scope. **Always via `recompute_coverage()`.**

    `rebuild=False`: looking must not repair. A command whose default rewrote the
    cache would destroy the evidence of whatever wrote it wrong, every time an
    operator ran it to find out what was wrong.
    """
    handle = open_configured_store(store, read_only=True)
    try:
        report = report_coverage(
            handle.coverage_records(),
            None,
            system_id=system,
            environment_id=environment,
            rebuild=False,
        )
        payload: dict[str, Any] = {
            **report.as_report_block(),
            "cold_cache_entries": report.cold,
            "drift": [
                {
                    "identity_id": entry.identity_id,
                    "cached": entry.cached,
                    "recomputed": entry.recomputed,
                }
                for entry in report.drift
            ],
        }
        if by_kind:
            payload["by_kind"] = {
                kind: {"covered": covered, "discovered": discovered}
                for kind, (covered, discovered) in report.by_kind.items()
            }
        emit(payload, as_json=as_json, title="adopt surface coverage")
        # Drift on a **read-only** path takes the code `02` §1.4 registers for it:
        # exit 5's guarantee is *"nothing written; prior state intact"*, and here
        # that is true by construction. B1-CR-60 records why the same code does
        # **not** fire at the end of a committed `adopt map` run.
        raise typer.Exit(
            map_exit_code_for(ErrorCode.MAP_COVERAGE_CACHE_DRIFT)
            if report.drift
            else MapExitCode.COMPLETE
        )
    finally:
        handle.close()


@app.command("changes")
def changes(
    since: Annotated[str, typer.Option("--since", help="RFC 3339 timestamp.", show_default=False)],
    system: SystemOption = "",
    environment: EnvironmentOption = None,
    store: StoreOption = None,
    as_json: JsonOption = False,
) -> None:
    """Added, superseded and moved revisions since a timestamp. Read-only.

    **The revision history is the delta** (B1-CR-01). Each entry names the
    identity URI, the revision's status and whether it superseded another, which
    is exactly the three classes `01` F11.4 asks for -- added identities,
    superseded revisions, moved revisions -- read off the chain rather than
    recomputed.
    """
    handle = open_configured_store(store, read_only=True)
    try:
        records = handle.export_records()
        identities = {
            row.id: row
            for row in records.table_rows("identity", Identity)
            if (not system or row.system_id == system)
            and (environment is None or row.environment_id == environment)
        }
        entries = [
            {
                "identity_uri": identities[row.identity_id].uri,
                "revision_id": row.id,
                "status": row.status,
                "created_at": _stamp(row.created_at),
                "supersedes_revision_id": row.supersedes_revision_id,
                "alias_of_identity_id": row.alias_of_identity_id,
                "change": _classify(row),
                "extractor": row.extractor,
            }
            for row in records.table_rows("identity_revision", IdentityRevision)
            if row.identity_id in identities and _stamp(row.created_at) >= since
        ]
        entries.sort(key=lambda entry: (str(entry["created_at"]), str(entry["revision_id"])))
        emit(
            {"since": since, "count": len(entries), "changes": entries},
            as_json=as_json,
            title="adopt surface changes",
        )
        raise typer.Exit(MapExitCode.COMPLETE)
    finally:
        handle.close()


def _classify(revision: IdentityRevision) -> str:
    """`added` · `moved` · `superseded` -- `01` F11.4's three classes.

    Derived from the row rather than stored, because a stored classification is a
    fourth thing that can disagree with the chain it describes.
    """
    if revision.status == "moved":
        return "moved"
    return "superseded" if revision.supersedes_revision_id else "added"


def _newest_first(revisions: list[IdentityRevision]) -> list[IdentityRevision]:
    return sorted(revisions, key=lambda row: (_stamp(row.created_at), row.id), reverse=True)


def _revision_payload(revision: IdentityRevision) -> dict[str, Any]:
    return {
        "revision_id": revision.id,
        "status": revision.status,
        "created_at": _stamp(revision.created_at),
        "extractor": revision.extractor,
        "extractor_version": revision.extractor_version,
        "source_version": revision.source_version,
        "confidence": revision.confidence,
        "supersedes_revision_id": revision.supersedes_revision_id,
        "alias_of_identity_id": revision.alias_of_identity_id,
        "change": _classify(revision),
    }


def _stamp(value: object) -> str:
    """An RFC 3339 string for a timestamp column, whatever the driver returned.

    Rendered through Build 0's formatter where the value is a datetime, so the
    two comparison sides in `changes --since` are the same shape -- a lexical
    comparison over RFC 3339 UTC is a chronological one, and only if both sides
    were formatted the same way.
    """
    import datetime as _dt

    from adopt_obs import format_timestamp

    return format_timestamp(value) if isinstance(value, _dt.datetime) else str(value)
