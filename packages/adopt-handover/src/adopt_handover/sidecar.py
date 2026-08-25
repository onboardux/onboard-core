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
from typing import Any

from adopt_handover.assemble import AssembledPack

__all__ = ["render_sidecar"]


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
