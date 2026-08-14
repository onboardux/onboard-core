"""`ai.graph` -- the agent graph's nodes, and the edges as `calls` relations.

`01` F8.2 files graph nodes under `symbol`, which means this extractor mints
keys **`common.stub_tree` also mints**: both name a Python declaration
`<dotted-module-path>.<name>`, and that is deliberate rather than a collision.
`01` F2.3 requires two extractors describing one referent to mint byte-identical
URIs, and B1-CR-68's `reconcile_batches` then merges the two observations
additively -- `stub_tree` supplies the signature, this extractor supplies the
edges. Minting `graph:triage` instead would have avoided the overlap by
**forking the referent**, which is the more expensive mistake: the node and the
function would be two identities for one thing, and every count downstream would
double.

**Edges are `calls`, directed, and never inverted.** `02` §5.2 fixes the closed
predicate list and says the framework does not auto-create inverses: an extractor
emits the direction it observed, and `triage -> retrieve` is what the edge list
says. `calls` rather than `depends_on` because a graph edge *is* an invocation:
the node runs and then the next node runs with its output.
"""

from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.fileindex import FileEntry
from adopt_map.schemas import ExtractorManifest, FactRelation, SourceRef, SurfaceFact
from tree_sitter import Node

from adopt_extractors_ai._grammar import matches, node_text, parse, string_value

__all__ = ["MANIFEST", "GraphExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="ai.graph",
    version="1.0.0",
    pack="ai",
    archetypes=["ai"],
    kinds=["symbol"],
    method="grammar",
)

#: `graph.add_node("triage", triage)` / `graph.add_edge("triage", "retrieve")`.
#: One pattern for both, because they are the same shape and telling them apart
#: on the method name is one comparison rather than a second query.
_CALL_PATTERN: Final[str] = """
(call
  function: (attribute attribute: (identifier) @method)
  arguments: (argument_list . (string) @first . (_)? @second)) @call
"""

_ADD_NODE: Final[tuple[str, ...]] = ("add_node",)
_ADD_EDGE: Final[tuple[str, ...]] = ("add_edge", "add_conditional_edges")
_ENTRY_POINT: Final[tuple[str, ...]] = ("set_entry_point", "set_finish_point")

_LANGUAGE: Final[str] = "python"


class GraphExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        for entry in ctx.files(language=_LANGUAGE):
            ctx.budget.check()
            text = ctx.text(entry)
            root, data = parse(_LANGUAGE, text)
            nodes, edges = _graph_of(root, data)
            if not nodes:
                continue
            module = _module_of(entry.path)
            for name in sorted(nodes):
                yield _fact(
                    node_name=name,
                    symbol=nodes[name],
                    module=module,
                    targets=[nodes[target] for target in edges.get(name, ()) if target in nodes],
                    entry=entry,
                )


def _graph_of(root: Node, data: bytes) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """`({node name: handler symbol}, {node name: (targets,)})` for one module."""
    nodes: dict[str, str] = {}
    edges: dict[str, tuple[str, ...]] = {}
    for capture in matches(_LANGUAGE, _CALL_PATTERN, root):
        methods = capture.get("method") or []
        first = capture.get("first") or []
        second = capture.get("second") or []
        if not (methods and first):
            continue
        method = node_text(methods[0], data)
        name = string_value(first[0], data)
        if method in _ADD_NODE:
            handler = node_text(second[0], data) if second else name
            nodes[name] = handler
        elif method in _ADD_EDGE and second and second[0].type == "string":
            target = string_value(second[0], data)
            edges[name] = (*edges.get(name, ()), target)
        elif method in _ENTRY_POINT:
            nodes.setdefault(name, name)
    return nodes, edges


def _fact(
    *, node_name: str, symbol: str, module: str, targets: list[str], entry: FileEntry
) -> SurfaceFact:
    return SurfaceFact(
        identity_kind="symbol",
        namespace=_LANGUAGE,
        # The **handler**, not the node label: `stub_tree` keys the declaration
        # and this has to agree with it byte for byte or the referent forks.
        local_key=f"{module}.{symbol}",
        title=symbol,
        attributes={},
        relations=[
            FactRelation(
                predicate="calls",
                target_kind="symbol",
                target_namespace=_LANGUAGE,
                target_local_key=f"{module}.{target}",
            )
            for target in targets
        ],
        source_refs=[SourceRef(path=entry.path, blob_sha=entry.blob_sha)],
    )


def _module_of(path: str) -> str:
    relative = PurePosixPath(path)
    return ".".join([*relative.parts[:-1], relative.stem])
