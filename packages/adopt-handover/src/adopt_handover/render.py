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

from adopt_handover.assemble import AssembledPack, AssembledSection
from adopt_handover.ports import BoundaryView, GapView
from adopt_obs import format_timestamp

__all__ = ["render"]

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

    for assembled in pack.sections:
        lines += ["", _heading(2, assembled.section.heading), ""]
        if assembled.key == "overview":
            lines += _overview(pack)
        elif assembled.key == "gaps":
            lines += _gaps(pack.gaps)
        elif assembled.key == "boundary":
            lines += _boundary(pack.boundary)
        else:
            lines += _knowledge_section(assembled)

    return "\n".join(lines).rstrip("\n") + "\n"
