"""`web.fastapi.routes` -- decorator routes, routers and their prefixes.

FastAPI declares a route as a decorator whose *name* is the method --
`@app.get("/orders")` -- so the method this framework hides in a view class is
here in plain sight, and no `ANY` fallback is needed for the ordinary case.

**`APIRouter(prefix=...)` is applied**, for the reason `include()` expansion
exists in the Django extractor: a router mounted under `/api/v1` serves
`/api/v1/orders`, and emitting `/orders` would be a confident wrong answer that
also forks against the OpenAPI document describing the same service.
"""

from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from tree_sitter import Node

from adopt_extractors_web._grammar import matches, node_text, parse, string_value
from adopt_extractors_web._routes import HTTP_METHODS, endpoint_fact

__all__ = ["MANIFEST", "FastapiRoutesExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="web.fastapi.routes",
    version="1.0.0",
    pack="web",
    archetypes=["web"],
    kinds=["endpoint"],
    method="grammar",
)

_FRAMEWORK: Final[str] = "fastapi"

#: `@app.get("/orders")` on a function definition. The decorator's attribute is
#: the method and its first string argument is the path.
_ROUTE_PATTERN: Final[str] = """
(decorated_definition
  (decorator
    (call
      function: (attribute object: (identifier) @object attribute: (identifier) @verb)
      arguments: (argument_list . (string) @route)))
  definition: (function_definition name: (identifier) @handler)) @decorated
"""

#: `router = APIRouter(prefix="/api/v1")`, whose prefix every route on `router`
#: inherits.
_ROUTER_PATTERN: Final[str] = """
(assignment
  left: (identifier) @name
  right: (call
    function: (identifier) @ctor
    arguments: (argument_list
      (keyword_argument name: (identifier) @kw value: (string) @prefix)))) @assign
"""


class FastapiRoutesExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        """Whether any Python file mentions FastAPI or an `APIRouter`.

        A name check over file *contents* would be the second walk `03` §5.8
        forbids, so this checks that Python files exist at all and lets `extract`
        decline per file. The registry records `not_applicable` either way, so a
        tree with no FastAPI produces no facts and one recorded reason.
        """
        return any(Path(root).rglob("*.py"))

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `endpoint` per decorated route, with the router prefix applied."""
        for entry in ctx.files(language="python"):
            ctx.budget.check()
            text = ctx.text(entry)
            if "fastapi" not in text.lower() and "apirouter" not in text.lower():
                continue
            root, data = parse("python", text)
            prefixes = _router_prefixes(root, data)
            for order, capture in enumerate(matches("python", _ROUTE_PATTERN, root)):
                fact = _fact(capture, data, entry.path, entry.blob_sha, prefixes, order)
                if fact is not None:
                    yield fact


def _router_prefixes(root: Node, data: bytes) -> dict[str, str]:
    """`{router variable: prefix}` for the routers declared in one file."""
    prefixes: dict[str, str] = {}
    for capture in matches("python", _ROUTER_PATTERN, root):
        names = capture.get("name") or []
        ctors = capture.get("ctor") or []
        keywords = capture.get("kw") or []
        values = capture.get("prefix") or []
        if not (names and ctors and keywords and values):
            continue
        if node_text(ctors[0], data) != "APIRouter":
            continue
        if node_text(keywords[0], data) != "prefix":
            continue
        prefixes[node_text(names[0], data)] = string_value(values[0], data)
    return prefixes


def _fact(
    capture: dict[str, list[Node]],
    data: bytes,
    path: str,
    blob_sha: str,
    prefixes: dict[str, str],
    order: int,
) -> SurfaceFact | None:
    verbs = capture.get("verb") or []
    routes = capture.get("route") or []
    objects = capture.get("object") or []
    handlers = capture.get("handler") or []
    if not (verbs and routes):
        return None
    verb = node_text(verbs[0], data).lower()
    if verb not in HTTP_METHODS:
        # `@app.middleware("http")` and `@app.on_event("startup")` match the same
        # shape and are not routes. Filtering on the closed method list rather
        # than on a decorator deny-list means a new FastAPI decorator is ignored
        # by default instead of minting a nonsense endpoint.
        return None
    owner = node_text(objects[0], data) if objects else ""
    module = ".".join([*PurePosixPath(path).parts[:-1], PurePosixPath(path).stem])
    handler = f"{module}.{node_text(handlers[0], data)}" if handlers else None
    return endpoint_fact(
        method=verb,
        path=f"{prefixes.get(owner, '')}{string_value(routes[0], data)}",
        framework=_FRAMEWORK,
        handler=handler,
        handler_namespace="python",
        source=SourceRef(path=path, start_line=routes[0].start_point[0] + 1, blob_sha=blob_sha),
        declaration_order=order,
    )
