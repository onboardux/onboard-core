"""Build 4's verb: `adopt pack` -- audience-scoped handover packs.

**Every `adopt_handover` import happens inside the command body**, exactly as
Builds 1-3 do it: v6.1 §2.1 requires new verbs to register lazily so
`CLI_COLD_START_MS` holds, and `adopt version` must not pay for an assembler it
never runs.

**Assembly writes no store rows and never will.** `--draft-missing` does write --
it is the drafting pass -- and it runs strictly *before* assembly, in its own
step, so the pack that is rendered is a pure reading of the store as it stands
after drafting finished. That ordering is what keeps the byte-stability claim
true: the renderer is handed a store, never a store plus something in flight.

**No adapter means no drafting and a complete pack anyway** (v6.1 R3). The flag
reports that it drafted nothing and names the configuration that would let it;
the Markdown, the sidecar and every stamp are identical to a run that never
passed the flag. A capability that degraded into an error would make the model
a dependency, which is the thing R3 forbids.
"""

from pathlib import Path
from typing import Annotated, Any

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store

__all__ = ["pack"]

AudienceOption = Annotated[
    str,
    typer.Option(
        "--audience",
        help="Who the pack is for: technical, client_ops, end_user, admin, or a firm's own tag.",
    ),
]
OutOption = Annotated[
    Path,
    typer.Option("--out", help="Directory to write the pack into. Created if absent."),
]
FormatOption = Annotated[
    str,
    typer.Option(
        "--format",
        help="md (canonical), docx (via pandoc) or pdf (via typst). Derived formats are "
        "content-equivalent conversions of the Markdown, never canon.",
    ),
]
DraftMissingOption = Annotated[
    bool,
    typer.Option(
        "--draft-missing",
        help="Draft uncovered sections through the configured model adapter. Drafts land "
        "UNVERIFIED and must be confirmed in `adopt review` before they count.",
    ),
]
ScopeOption = Annotated[
    str | None,
    typer.Option("--scope", help="firm/engagement/system/environment. Defaults to the store's."),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Path to the store.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


def pack(
    audience: AudienceOption = "technical",
    out: OutOption = Path("./handover"),
    format_name: FormatOption = "md",
    draft_missing: DraftMissingOption = False,
    scope: ScopeOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Assemble an audience-scoped handover pack from the store.

    Sections select **confirmed** knowledge by audience and kind; the map
    contributes the inventory; the coverage join contributes the gap appendix,
    and any open conflict a probe recorded is listed inside it; Build 0's
    observability boundary is embedded so the pack states its own limits. Every
    section carries its verification status and date.

    The Markdown is byte-stable given the same revisions -- no clock reaches it,
    so running this twice over an unchanged store produces identical files.
    With `--draft-missing`, uncovered identities are drafted first and render
    under UNVERIFIED banners until a human confirms them.
    """
    from adopt_handover import assemble, convert, converter_for, render, render_sidecar
    from adopt_knowledge import rank_gaps

    from adopt_cli.commands import _pack_support as support
    from adopt_cli.commands._map_support import resolve_scope
    from adopt_coverage import recompute_coverage
    from adopt_obs import AdoptError, ErrorCode

    # Resolved before the store is opened: an unknown `--format` must refuse
    # before anything is written, not after a pack is on disk.
    converter = converter_for(format_name)
    if converter is not None and not out:  # pragma: no cover -- `--out` has a default
        # Unreachable while `--out` carries a default, and left as a guard rather
        # than deleted. **If it is ever made reachable it must not raise
        # `typer.BadParameter`** -- see the note at the scope refusal below for
        # why that exits 1 and bypasses the JSON envelope. It needs a registered
        # code, and none of the current set means "you named a format with
        # nowhere to put it", so choosing one is a decision for whoever makes
        # this branch live.
        raise typer.BadParameter("--format needs --out: a derived file has to go somewhere.")

    # Drafting writes; assembling does not. The store is opened writable only
    # when the flag asked for it, so an ordinary `adopt pack` cannot modify a
    # store even if something below it tried to.
    handle = open_configured_store(store, read_only=not draft_missing)
    try:
        resolved = resolve_scope(handle, scope)
        if resolved.system is None:
            # **`AdoptError`, not `typer.BadParameter`.** The installed typer
            # vendors its own click under `typer._click`, so a `BadParameter`
            # raised in a command body is not a `click.ClickException`: it never
            # reaches `main._exit_code_of`, escapes as an unhandled exception,
            # exits 1 instead of a contracts §13 code, and renders a rich panel
            # where a `--json` caller was promised the one error envelope.
            # Found while building Build 6's review actions, which needed
            # refusals of exactly this shape.
            #
            # `SCOPE_VIOLATION` is the code the store's own facades already
            # raise for an unresolved scope, so an operator gets one sentence
            # for one situation whichever layer noticed it first.
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message="a pack is assembled for one system, and this store has no system in scope",
                hint="Pass --scope firm/engagement/system/environment, or run `adopt init` first.",
            )

        system_id = str(resolved.system.id)
        environment_id = str(resolved.environment.id) if resolved.environment is not None else None

        drafting = None
        if draft_missing:
            from adopt_cli.commands._drafting import draft_gaps

            drafting = draft_gaps(
                handle,
                scope=resolved,
                audience=audience,
                system_id=system_id,
                environment_id=environment_id,
            )

        # Recomputed **after** drafting, deliberately. Drafts are unverified, so
        # they change no coverage number and the gap appendix is identical
        # either way -- and computing it afterwards is what makes that a fact
        # the command demonstrates rather than a claim the docstring makes.
        coverage = recompute_coverage(handle.coverage_records(), system_id, environment_id)
        covered = frozenset(row.identity_id for row in coverage.identities if row.covered)
        ranked = rank_gaps(coverage.identities)
        # Every identity the recompute evaluated, by URI. The conflict join
        # needs it, and it is the same population the inventory renders --
        # so a conflict can never name an identity this pack does not list.
        uris = {row.identity_id: row.uri for row in coverage.identities}

        assembled = assemble(
            audience=audience,
            knowledge=support.build_knowledge(
                handle, system_id=system_id, environment_id=environment_id
            ),
            identities=support.build_identities(
                handle, system_id=system_id, environment_id=environment_id, covered=covered
            ),
            freshness=support.FreshnessCache(handle),
            boundary=support.build_boundary(
                handle, system_id=system_id, environment_id=environment_id
            ),
            gaps=support.build_gaps(ranked, handle.governance().gap_dispositions()),
            conflicts=support.build_conflicts(handle, uris=uris),
            drafts=support.build_drafts(handle, system_id=system_id, environment_id=environment_id),
        )
        document = render(assembled)
        lineage = render_sidecar(assembled)
    finally:
        handle.close()

    out.mkdir(parents=True, exist_ok=True)
    markdown_path = out / f"{audience}.md"
    sidecar_path = out / f"{audience}.lineage.json"
    # `newline="\n"` on both: a pack diffed across a Windows checkout and a Linux
    # runner must not differ in every line, and CRLF is a recorded failure class
    # in this repository's own release pipeline.
    markdown_path.write_text(document, encoding="utf-8", newline="\n")
    sidecar_path.write_text(lineage, encoding="utf-8", newline="\n")

    payload = _payload(assembled, markdown_path, sidecar_path)
    if converter is not None:
        derived_path = out / f"{audience}.{converter.format}"
        payload["derived"] = str(derived_path)
        payload["derived_with"] = convert(markdown_path, derived_path, converter)

    if drafting is not None:
        payload["drafting"] = drafting

    emit(payload, as_json=json_output, title="adopt pack")


def _payload(assembled: Any, markdown_path: Path, sidecar_path: Path) -> dict[str, Any]:
    return {
        "audience": assembled.audience,
        "markdown": str(markdown_path),
        "sidecar": str(sidecar_path),
        "identities": len(assembled.identities),
        "gaps": len(assembled.gaps),
        "conflicts": len(assembled.conflicts),
        "boundary": "declared" if assembled.boundary is not None else "none",
        "sections": [
            {
                "section": section.key,
                "heading": section.section.heading,
                "revisions": len(section.revisions),
                # The stamps actually rendered, so `--json` shows an operator
                # that a section went out unverified without reading the file.
                "stamps": sorted({stamped.stamp for stamped in section.revisions}),
                "unverified": section.drafted,
            }
            for section in assembled.sections
        ],
    }
