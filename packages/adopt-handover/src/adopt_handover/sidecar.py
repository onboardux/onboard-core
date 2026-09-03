"""The lineage sidecar: section -> the revisions and identities it came from.

v6.1 §6 Build 4: *"One lean JSON sidecar per pack maps section → source revision
ids + identity URIs — the minimum lineage B8 needs for section-scoped
regeneration. A build artifact, not a published contract (R4)."*

Both halves of that sentence are load-bearing:

* **Minimum.** Section key, revision ids, identity URIs. Not bodies, not
  digests, not a schema version. B8 needs to know which sections a changed
  identity touches; anything more would be a shape somebody starts depending on.
* **Not a contract.** There is no `$id`, no version field and no JSON-schema
  target, because R4 says a wire shape waits for a second party shipping against
  it. B8 is in this repository and will read it as a build artifact.

Rendered with the export writer's rules -- sorted keys, no spaces, LF -- so the
sidecar is byte-stable for the same reason the Markdown is.
"""

import json
from collections.abc import Iterable, Mapping
from typing import Any

from adopt_handover.assemble import AssembledPack

__all__ = ["parse_sidecar", "render_sidecar", "sections_affected"]


def render_sidecar(pack: AssembledPack) -> str:
    """The lineage JSON for `pack`. A pure function of it; no clock, no I/O.

    Every list is already ordered by `assemble`, so this re-sorts nothing and
    cannot disagree with what the Markdown rendered.
    """
    sections: list[dict[str, Any]] = []
    for assembled in pack.sections:
        if not assembled.section.kinds:
            # The overview, gaps and boundary sections have no source revisions:
            # they are derived from the identity registry, the coverage join and
            # the boundary row. Recorded with empty lists rather than omitted,
            # so a reader can tell "this section cites nothing" from "this
            # section is not in the pack".
            sections.append({"section": assembled.key, "revision_ids": [], "identity_uris": []})
            continue

        uris: list[str] = []
        for stamped in assembled.revisions:
            for uri in stamped.revision.identity_uris:
                if uri not in uris:
                    uris.append(uri)
        sections.append(
            {
                "section": assembled.key,
                "revision_ids": [stamped.revision.revision_id for stamped in assembled.revisions],
                "identity_uris": uris,
            }
        )

    payload = {"audience": pack.audience, "sections": sections}
    # Sorted keys and no spaces: the export writer's one rendering rule
    # (`adopt_export`), applied here so two runs over the same revisions produce
    # identical bytes on any machine.
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def parse_sidecar(text: str) -> Mapping[str, Any]:
    """One rendered sidecar back into the mapping `sections_affected` reads.

    Here rather than at each caller so the one place that knows the file's shape
    is the one place that writes it. A body that is not an object, or whose
    `sections` is not a list, comes back as an empty sidecar: this is a build
    artifact rather than a contract (R4), and a caller handed a truncated or
    hand-edited file should regenerate the pack rather than receive a partial
    selection it would take for a complete one.
    """
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"audience": "", "sections": []}
    if not isinstance(decoded, dict) or not isinstance(decoded.get("sections"), list):
        return {"audience": "", "sections": []}
    return decoded


def sections_affected(
    sidecar: Mapping[str, Any],
    changed_revision_ids: Iterable[str] = (),
    changed_uris: Iterable[str] = (),
) -> tuple[str, ...]:
    """The section keys a change touches, in the sidecar's own order.

    v6.1 section 6 Build 8: *pack sections regenerate scoped via Build 4's
    sidecar lineage -- not whole-pack.* This is the selection half; `render_
    sections` is the rendering half, and the two are deliberately separate so a
    caller can see which sections it is about to regenerate before it does.

    Args:
        sidecar: A parsed sidecar -- `parse_sidecar`'s output, or the mapping a
            caller already holds.
        changed_revision_ids: `knowledge_revision.id`s a review resolution
            superseded or appended. A section citing one of them no longer
            renders what it rendered.
        changed_uris: Canonical identity URIs the cascade classified. A section
            whose knowledge is *about* one of them may render differently even
            when no revision id moved -- a stamp changes when a binding stales,
            and the stamp is part of the section's bytes.

    Returns:
        Section keys, deduplicated, in the sidecar's recorded order -- which is
        `SECTIONS`' order, so the caller's selection renders in reading order
        without sorting anything.

        **A section whose lineage is empty is never selected**, and that is a
        statement about what the sidecar records rather than a filter. The
        overview, gap and boundary sections are derived from the whole store
        (the identity registry, the coverage join, the boundary row), so they
        have no lineage to match against and "which change touched them" is not
        a question this data can answer. A caller who wants them regenerated
        names them; nothing here will guess.
    """
    revisions = frozenset(changed_revision_ids)
    uris = frozenset(changed_uris)
    if not revisions and not uris:
        return ()

    affected: list[str] = []
    for entry in sidecar.get("sections", []):
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("section") or "")
        if not key or key in affected:
            continue
        cited = {str(value) for value in entry.get("revision_ids") or ()}
        about = {str(value) for value in entry.get("identity_uris") or ()}
        if cited & revisions or about & uris:
            affected.append(key)
    return tuple(affected)
