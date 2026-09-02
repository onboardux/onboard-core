"""Markdown rendering. The canonical output, and it is byte-stable.

v6.1 §6 Build 4: *"Canonical output is Markdown, byte-stable given the same
revisions."* Three things make that hold, and all three are decisions rather
than accidents:

* **No clock.** Every date rendered comes from a revision's own `created_at`
  through `format_timestamp`. There is no "generated at" line and there must
  never be one: it is the single field that would make two runs over an
  unchanged store differ, and it tells a reader nothing the bundle's own
  metadata does not.
* **No dict iteration and no set iteration.** Every sequence rendered was
  ordered by `assemble`, which sorts explicitly. The bytes are a pure function
  of `(AssembledPack)`.
* **LF, always.** `write_text` is called with `newline="\\n"` by the caller, and
  nothing here emits `\\r`. CRLF on Windows is a recorded failure class in this
  repository (the release pipeline hit it), and a pack diffed across two
  machines would otherwise differ in every line.

The renderer is deliberately dumb: it takes stamps rather than computing them,
counts rather than counting, and order rather than sorting. Everything it could
get wrong was decided in `sections.py` and `assemble.py`, where it is testable
without parsing Markdown.
"""

from collections.abc import Sequence

from adopt_handover.assemble import AssembledPack, AssembledSection
from adopt_handover.ports import BoundaryView, ConflictView, GapView
from adopt_obs import format_timestamp

__all__ = ["render", "render_sections", "section_blocks"]

_RULE = ""


def _heading(level: int, text: str) -> str:
    return f"{'#' * level} {text}"


def _body(text: str) -> str:
    """One stored body, with its line endings normalised to LF.

    **The bytes of the file are ours; the words are the author's.** A document
    ingested from a Windows checkout carries CRLF in `body_md` -- `git` with
    `core.autocrlf` guarantees it -- and quoting it verbatim would emit a pack
    with mixed endings, which every diff tool then reports as a whole-file
    change. Normalising the endings alters no character a reader sees and no
    Markdown semantics; it is the same decision the export writer makes when it
    pins `newline="\\n"`.

    This is *not* what makes the pack byte-stable -- the stored body is already
    deterministic, so two runs agree either way. It is what stops a pack
    assembled on Windows from being a different file to one assembled on Linux
    from the same store.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _overview(pack: AssembledPack) -> list[str]:
    """The map's contribution: what is in the system, by kind, with coverage."""
    lines = [f"Identities mapped: **{len(pack.identities)}**"]
    if pack.kind_counts:
        lines += ["", "| Kind | Count |", "|---|---|"]
        lines += [f"| {kind} | {count} |" for kind, count in pack.kind_counts]

    covered = sum(1 for row in pack.identities if row.covered)
    lines += [
        "",
        f"Covered by confirmed knowledge: **{covered}** of **{len(pack.identities)}**.",
    ]
    if not pack.identities:
        lines += [
            "",
            "No identities are mapped yet. Run `adopt map` before assembling a pack: "
            "an overview with nothing in it is a pack that cannot state its own limits.",
        ]
    return lines


def _gaps(gaps: tuple[GapView, ...]) -> list[str]:
    """The elicitation queue, with each gap's human disposition beside it.

    Rendered even when empty, and rendered in full when not: the gap appendix is
    a deliverable (v6.1 §1.1 row 7), not an embarrassment to be trimmed. A pack
    that hides what it does not know is the artefact this product exists to
    replace.
    """
    if not gaps:
        return [
            "No uncovered identities. Every mapped identity has confirmed knowledge bound to it."
        ]

    lines = [
        f"**{len(gaps)}** identities have no confirmed knowledge bound to them.",
        "",
        "| Identity | Kind | Status | Owner | Note | Waived until |",
        "|---|---|---|---|---|---|",
    ]
    for gap in gaps:
        # An undisposed gap is `open`: the gap exists because the recompute
        # derived it, and nobody having decided anything about it yet is
        # exactly what `open` means.
        status = gap.status or "open"
        owner = gap.owner_actor_id or "—"
        note = (gap.note or "—").replace("|", "\\|").replace("\n", " ")
        until = format_timestamp(gap.waived_until) if gap.waived_until is not None else "—"
        lines.append(f"| `{gap.uri}` | {gap.kind} | {status} | {owner} | {note} | {until} |")
    return lines


def _conflicts(conflicts: tuple[ConflictView, ...]) -> list[str]:
    """Bet 4, inside the appendix: what this pack says that a probe disagrees with.

    Rendered **only when there are some**. That is the opposite of the gap
    table's rule, and the difference is what each says when empty: "no uncovered
    identities" is a real fact about coverage, while "no conflicts" over a store
    that has never run a probe would be a claim of agreement nobody measured.
    Silence is the honest rendering of a question never asked.
    """
    if not conflicts:
        return []

    lines = [
        "",
        # const-sync: ok -- a Markdown heading level, not a schema version.
        _heading(3, "Contradicted by observation"),
        "",
        f"**{len(conflicts)}** confirmed statement(s) in this pack are contradicted by "
        "what a behavioural probe observed. Each was true when it was confirmed; a "
        "probe has since seen the system do otherwise, and nobody has yet decided "
        "which of the two is wrong.",
        "",
        "| Identity | Kind | Confirmed revision | Observed |",
        "|---|---|---|---|",
    ]
    for conflict in conflicts:
        revision = conflict.intent_revision_id or "—"
        lines.append(
            f"| `{conflict.uri}` | {conflict.kind} | `{revision}` | "
            f"{format_timestamp(conflict.detected_at)} |"
        )
    return lines


def _boundary(boundary: BoundaryView | None) -> list[str]:
    """Build 0's signed boundary, embedded so the pack carries its own limits."""
    if boundary is None:
        return [
            "**No observability boundary is declared for this system.**",
            "",
            "Every statement in this pack is therefore unbounded by an agreed scope. "
            "Run `adopt init` or `adopt boundary declare` to record what may be "
            "observed and what may leave.",
        ]

    categories = ", ".join(f"`{value}`" for value in boundary.permitted_outbound_categories)
    lines = [
        f"- **Tier:** {boundary.tier}",
        f"- **Declared:** {format_timestamp(boundary.declared_at)}",
        f"- **Contractual:** {'yes' if boundary.contractual else 'no'}",
        f"- **Permitted outbound:** {categories or '—'}",
    ]
    if boundary.covered:
        lines += ["", f"**Covered.** {boundary.covered}"]
    if boundary.not_covered:
        lines += ["", f"**Not covered.** {boundary.not_covered}"]
    lines += [
        "",
        "This pack states only what the boundary above permits. Anything outside it "
        "was not observed and is not claimed.",
    ]
    return lines


def _knowledge_section(assembled: AssembledSection) -> list[str]:
    """A knowledge-backed section: every body preceded by its stamp.

    There is no branch that renders a body without a stamp. That is the point --
    the stamp is emitted from the same loop iteration as the body, so no future
    edit can render one without the other by forgetting a flag.
    """
    if assembled.is_empty:
        return [assembled.section.empty_note]

    lines: list[str] = []
    for stamped in assembled.revisions:
        revision = stamped.revision
        # const-sync: ok -- a Markdown heading level, not a schema version.
        lines.append(_heading(3, revision.title))
        lines.append("")
        lines.append(
            f"*Status:* **{stamped.stamp}** · *Dated:* {format_timestamp(revision.created_at)} "
            f"· *Revision:* `{revision.revision_id}`"
        )
        banner = stamped.banner
        if banner is not None:
            lines += ["", banner]
        lines += ["", _body(revision.body_md)]
        if revision.identity_uris:
            lines += [
                "",
                "*Applies to:* " + ", ".join(f"`{uri}`" for uri in revision.identity_uris),
            ]
        lines.append("")
    return lines[:-1] if lines and lines[-1] == "" else lines


def _section_lines(pack: AssembledPack, assembled: AssembledSection) -> list[str]:
    """One section's lines: its heading, a blank, and whatever fills it.

    Extracted so `render` and `render_sections` cannot produce different bytes
    for the same section. **That is Build 8's scoped-regeneration correctness
    contract, made structural rather than asserted**: a scoped re-render that
    built its blocks by a second route would agree with a full render on the day
    it was written and drift the first time either changed -- and the drift
    would be invisible, because both documents would still be well-formed
    Markdown that nothing but a byte comparison could separate.
    """
    lines = [_heading(2, assembled.section.heading), ""]
    if assembled.key == "overview":
        return lines + _overview(pack)
    if assembled.key == "gaps":
        return lines + _gaps(pack.gaps) + _conflicts(pack.conflicts)
    if assembled.key == "boundary":
        return lines + _boundary(pack.boundary)
    return lines + _knowledge_section(assembled)


def section_blocks(pack: AssembledPack) -> tuple[tuple[str, str], ...]:
    """`(section key, the exact text that section contributes)`, in render order.

    The blocks are what a full document is made of: `render` puts the pack's
    title and preamble above them and joins them with one blank line between. A
    caller regenerating a subset joins the same values the same way, which is
    why the two agree byte-for-byte by construction rather than by test.
    """
    return tuple(
        (assembled.key, "\n".join(_section_lines(pack, assembled))) for assembled in pack.sections
    )


def render_sections(pack: AssembledPack, keys: Sequence[str]) -> str:
    """Only the named sections, byte-identical to their place in a full render.

    v6.1 section 6 Build 8: *pack sections regenerate scoped, not whole-pack.*
    The fragment carries no title and no preamble -- it is the part of a
    document that changed, not a smaller document -- and the sections appear in
    `SECTIONS` order whatever order they were asked for, because the pack's
    order is the reader's and a caller's argument order is an accident of how
    the names were collected.

    **A name that is not in the pack is ignored rather than refused.** The names
    come from a sidecar an earlier `adopt pack` wrote, and one section since
    removed from `SECTIONS` would otherwise make that sidecar unusable for every
    section that does still exist.

    Returns:
        The selected blocks joined exactly as `render` joins them, ending in one
        newline. An empty selection returns one newline -- the honest rendering
        of "nothing this change touched is in this pack", and not an error: a
        change that affected no section is a normal outcome of scoping.
    """
    wanted = frozenset(keys)
    chosen = [block for key, block in section_blocks(pack) if key in wanted]
    return "\n\n".join(chosen).rstrip("\n") + "\n"


def render(pack: AssembledPack) -> str:
    """The canonical Markdown. A pure function of `pack`; no clock, no I/O.

    Returns:
        The document, ending in exactly one newline. Callers write it with
        `newline="\\n"` so a Windows checkout and a Linux runner produce the
        same bytes.
    """
    lines: list[str] = [_heading(1, f"Handover pack — {pack.audience}"), ""]
    lines += [
        "Assembled from the knowledge store. Every section below carries the "
        "verification status and date of the knowledge it was built from; nothing "
        "here is asserted beyond what the store holds.",
    ]

    body = "\n\n".join(block for _key, block in section_blocks(pack))
    return ("\n".join(lines) + "\n\n" + body).rstrip("\n") + "\n"
