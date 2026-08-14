"""`web.express.routes` -- `app.get('/orders', handler)` in JavaScript and TypeScript.

Express declares the method as the call name, like FastAPI, and the path as the
first string argument. The one difference that matters is that a *middleware*
registration (`app.use(...)`) uses the same shape and is not a route, so the
method list is the filter -- the same closed-list discipline the FastAPI
extractor applies to `@app.middleware`.

**Both languages, one pattern.** The tree-sitter grammars for JavaScript and
TypeScript agree on `call_expression`, so the pattern is written once and run
against whichever language the file is.
"""

from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from tree_sitter import Node

from adopt_extractors_web._grammar import matches, node_text, parse, string_value
from adopt_extractors_web._routes import HTTP_METHODS, endpoint_fact

__all__ = ["MANIFEST", "ExpressRoutesExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="web.express.routes",
    version="1.0.0",
    pack="web",
    archetypes=["web"],
    kinds=["endpoint"],
    method="grammar",
)

_FRAMEWORK: Final[str] = "express"

_LANGUAGES: Final[tuple[str, ...]] = ("javascript", "typescript")

#: `app.get('/orders', handler)` -- an attribute call whose first argument is a
#: string. The handler capture is optional because `app.get('/x')` is legal.
_ROUTE_PATTERN: Final[str] = """
(call_expression
  function: (member_expression
    object: (identifier) @object
    property: (property_identifier) @verb)
  arguments: (arguments . (string) @route . (_)? @handler)) @call
"""


class ExpressRoutesExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        """Whether the tree holds JavaScript or TypeScript at all."""
        return any(any(Path(root).rglob(f"*{suffix}")) for suffix in (".js", ".jsx", ".ts", ".tsx"))

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `endpoint` per registered route, in file then declaration order."""
        for language in _LANGUAGES:
            for entry in ctx.files(language=language):
                ctx.budget.check()
                text = ctx.text(entry)
                if "express" not in text.lower() and "router" not in text.lower():
                    continue
                root, data = parse(language, text)
                for order, capture in enumerate(matches(language, _ROUTE_PATTERN, root)):
                    fact = _fact(capture, data, entry.path, entry.blob_sha, language, order)
                    if fact is not None:
                        yield fact


def _fact(
    capture: dict[str, list[Node]],
    data: bytes,
    path: str,
    blob_sha: str,
    language: str,
    order: int,
) -> SurfaceFact | None:
    verbs = capture.get("verb") or []
    routes = capture.get("route") or []
    if not (verbs and routes):
        return None
    verb = node_text(verbs[0], data).lower()
    if verb not in HTTP_METHODS:
        return None
    handlers = capture.get("handler") or []
    handler = None
    if handlers:
        candidate = node_text(handlers[0], data).strip()
        # Only a plain reference names a symbol. An inline arrow function is a
        # handler with no name, and `02` §4.2 would put an invented one in the
        # semantic projection -- where it would change every time the file is
        # reformatted.
        if candidate.isidentifier():
            module = ".".join([*PurePosixPath(path).parts[:-1], PurePosixPath(path).stem])
            handler = f"{module}.{candidate}"
    return endpoint_fact(
        method=verb,
        path=string_value(routes[0], data),
        framework=_FRAMEWORK,
        handler=handler,
        handler_namespace=language,
        source=SourceRef(path=path, start_line=routes[0].start_point[0] + 1, blob_sha=blob_sha),
        declaration_order=order,
    )
