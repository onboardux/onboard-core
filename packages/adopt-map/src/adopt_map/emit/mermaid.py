"""The Mermaid diagram, and the collapse that keeps it readable -- `01` F11.5.

Above `MAP_DIAGRAM_MAX_NODES` the diagram collapses into **kind-level clusters
with a stated notice**. Not truncation: a diagram that silently drew the first
300 of 3,000 nodes would be a picture of an arbitrary quarter of a system,
presented as a picture of the system.

Nodes are named by a stable index over the byte-sorted URI order rather than by
the URI itself, because a Mermaid identifier cannot carry the punctuation a URI
is made of. The mapping is emitted as the node label, so the diagram is still
readable against `surface.json`.
"""

from typing import Final

from adopt_const import MAP_DIAGRAM_MAX_NODES
from adopt_map.report import RunResult

__all__ = ["MERMAID_NAME", "collapsed", "render_mermaid"]

MERMAID_NAME: Final[str] = "surface.mmd"


def collapsed(result: RunResult) -> bool:
    """Whether this run's diagram is drawn at kind level rather than per node."""
    return result.total_facts() > MAP_DIAGRAM_MAX_NODES


def _escape(label: str) -> str:
    """Mermaid label text. Quotes and brackets close a node early."""
    return label.replace('"', "'").replace("[", "(").replace("]", ")")


def render_mermaid(result: RunResult) -> str:
    """A `flowchart LR`, per node or per kind depending on size."""
    if collapsed(result):
        return _render_collapsed(result)
    return _render_full(result)


def _render_collapsed(result: RunResult) -> str:
    counts = result.counts_by_kind()
    lines = [
        "%% Collapsed: this system has "
        f"{result.total_facts()} referents, above the {MAP_DIAGRAM_MAX_NODES} node "
        "diagram threshold. Nodes below are identity **kinds**, not referents; "
        "`surface.json` holds every referent.",
        "flowchart LR",
    ]
    for index, (kind, count) in enumerate(counts.items()):
        lines.append(f'  k{index}["{_escape(kind)} ({count})"]')
    # Kind-level edges, de-duplicated: one arrow per (source kind, predicate,
    # target kind) rather than per relation, which is the whole point of
    # collapsing.
    seen: set[tuple[str, str, str]] = set()
    order = {kind: index for index, kind in enumerate(counts)}
    for entry in result.minted():
        for relation in entry.fact.relations:
            key = (entry.fact.identity_kind, relation.predicate, relation.target_kind)
            if key in seen or key[0] not in order or key[2] not in order:
                continue
            seen.add(key)
            lines.append(f"  k{order[key[0]]} -->|{_escape(relation.predicate)}| k{order[key[2]]}")
    return "\n".join(lines) + "\n"


def _render_full(result: RunResult) -> str:
    minted = result.minted()
    index_of = {entry.uri: position for position, entry in enumerate(minted)}
    lines = ["flowchart LR"]
    for position, entry in enumerate(minted):
        lines.append(f'  n{position}["{_escape(entry.fact.title)}"]')
    for position, entry in enumerate(minted):
        for relation in entry.fact.relations:
            target = result.relation_target_uri(relation)
            if target in index_of:
                lines.append(
                    f"  n{position} -->|{_escape(relation.predicate)}| n{index_of[target]}"
                )
    return "\n".join(lines) + "\n"
