"""The D2 diagram. Same content as Mermaid, different renderer -- `01` F11.5.

Two diagram formats rather than one because `02` §8's `--format` offers both and
teams standardise on different renderers; the *content* decision -- what a node
is, what an edge is, and when to collapse -- lives in `adopt_map.emit.mermaid`
and is shared. A second content model would be a second thing to keep true.

Collapse above `MAP_DIAGRAM_MAX_NODES`, with the same stated notice, for the same
reason: a picture of an arbitrary quarter of a system presented as a picture of
the system is worse than no picture.
"""

from typing import Final

from adopt_const import MAP_DIAGRAM_MAX_NODES
from adopt_map.emit.mermaid import collapsed
from adopt_map.report import RunResult

__all__ = ["D2_NAME", "render_d2"]

D2_NAME: Final[str] = "surface.d2"


def _label(text: str) -> str:
    """A D2 label. Quotes are escaped; newlines would end the declaration."""
    return text.replace('"', "'").replace("\n", " ")


def render_d2(result: RunResult) -> str:
    """The D2 source, collapsed to kind level above the node threshold."""
    if collapsed(result):
        counts = result.counts_by_kind()
        lines = [
            f"# Collapsed: {result.total_facts()} referents exceed the "
            f"{MAP_DIAGRAM_MAX_NODES} node diagram threshold. Nodes are identity "
            "kinds, not referents; surface.json holds every referent.",
        ]
        for index, (kind, count) in enumerate(counts.items()):
            lines.append(f'k{index}: "{_label(kind)} ({count})"')
        seen: set[tuple[str, str, str]] = set()
        order = {kind: index for index, kind in enumerate(counts)}
        for entry in result.minted():
            for relation in entry.fact.relations:
                key = (entry.fact.identity_kind, relation.predicate, relation.target_kind)
                if key in seen or key[0] not in order or key[2] not in order:
                    continue
                seen.add(key)
                lines.append(f"k{order[key[0]]} -> k{order[key[2]]}: {_label(key[1])}")
        return "\n".join(lines) + "\n"

    minted = result.minted()
    index_of = {entry.uri: position for position, entry in enumerate(minted)}
    lines = [f'n{position}: "{_label(entry.fact.title)}"' for position, entry in enumerate(minted)]
    for position, entry in enumerate(minted):
        for relation in entry.fact.relations:
            target = result.relation_target_uri(relation)
            if target in index_of:
                lines.append(f"n{position} -> n{index_of[target]}: {_label(relation.predicate)}")
    return "\n".join(lines) + "\n"
