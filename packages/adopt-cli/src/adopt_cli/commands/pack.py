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
from adopt_obs import AdoptError, ErrorCode

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
SectionsOption = Annotated[
    str | None,
    typer.Option(
        "--sections",
        help="Re-render only these sections, comma-separated -- the section-scoped "
        "regeneration Build 8's review queue feeds. Names come from the pack's "
        "lineage sidecar via `adopt_handover.sections_affected`.",
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
    sections: SectionsOption = None,
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
    from adopt_handover import converter_for

    from adopt_cli.commands import _pack_support as support
    from adopt_cli.commands._map_support import resolve_scope

    # Resolved before the store is opened: an unknown `--format` must refuse
    # before anything is written, not after a pack is on disk.
    converter = converter_for(format_name)
    if converter is not None and not out:  # pragma: no cover -- `--out` has a default
        raise AdoptError(
            ErrorCode.PACK_RENDERER_MISSING,
            message=f"--format {format_name} was asked for with no --out to write it to.",
            hint="Pass --out DIR, or drop --format to keep the canonical Markdown.",
        )

    # Drafting writes; assembling does not. The store is opened writable only
    # when the flag asked for it, so an ordinary `adopt pack` cannot modify a
    # store even if something below it tried to.
    handle = open_configured_store(store, read_only=not draft_missing, verb="pack --draft-missing")
    try:
        resolved = resolve_scope(handle, scope)
        if resolved.system is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message=(
                    "a pack is assembled for one system, and this store has no system in scope."
                ),
                hint=(
                    "Pass --scope firm/engagement/system/environment, or run `adopt init` first."
                ),
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

        # Assembled through the shared halves in `_pack_support`, which
        # `adopt handover pack` calls too -- one assembler, two callers, so the
        # document a client receives cannot differ from the one `adopt pack`
        # produced. The coverage recompute happens **after** drafting inside
        # it, deliberately: drafts are unverified, so they change no coverage
        # number and the gap appendix is identical either way -- and computing
        # it afterwards is what makes that a fact the command demonstrates
        # rather than a claim the docstring makes.
        assembled = support.assemble_pack(
            handle, audience=audience, system_id=system_id, environment_id=environment_id
        )
        selected = _section_names(sections)
    finally:
        handle.close()

    written = support.write_pack(
        assembled, out, audience=audience, selected=selected, converter=converter
    )
    markdown_path = written["markdown_path"]
    sidecar_path = written["sidecar_path"]

    payload = _payload(assembled, markdown_path, sidecar_path)
    if selected is not None:
        payload["rendered_sections"] = list(selected)
    if converter is not None:
        payload["derived"] = str(written["derived_path"])
        payload["derived_with"] = written["derived_with"]

    if drafting is not None:
        payload["drafting"] = drafting

    emit(payload, as_json=json_output, title="adopt pack")


def _section_names(raw: str | None) -> tuple[str, ...] | None:
    """`--sections` as names, or `None` for the whole pack.

    **An empty or all-blank value is a refusal rather than "everything".** A
    scoped run is asked for by a caller that computed a selection, and a
    selection that came back empty means the change touched nothing in this
    pack -- rendering the whole pack instead would be the opposite of what was
    asked, and it would look like it worked.
    """
    if raw is None:
        return None
    names = tuple(name.strip() for name in raw.split(",") if name.strip())
    if not names:
        raise AdoptError(
            ErrorCode.PACK_SECTIONS_EMPTY,
            message="--sections was given no section names.",
            hint=(
                "A change that affected no section needs no re-render; "
                "omit the flag to render the whole pack."
            ),
        )
    return names


def _payload(assembled: Any, markdown_path: Path, sidecar_path: Path | None) -> dict[str, Any]:
    return {
        "audience": assembled.audience,
        "markdown": str(markdown_path),
        "sidecar": None if sidecar_path is None else str(sidecar_path),
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
