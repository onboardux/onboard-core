"""`web.django.routes` -- urlpatterns, view references and `include()` trees.

**No Django import, and that is the whole design constraint** (`05` S1.4, `02` §7
obligation 1). Django's own resolver would give perfect answers and requires
importing the client's settings module, which executes client code at import --
the one thing `01` F7.2 forbids and the `poisoned-import` fixture proves we never
do. So this reads `urls.py` as text through the `python` grammar.

**`include()` trees are expanded, and roots are detected rather than assumed.**
A `path("billing/", include("billing.urls"))` contributes its prefix to every
route in `billing/urls.py`, so emitting each urlconf's routes independently would
produce `orders/` where the served route is `billing/orders/` -- wrong, and wrong
in a way that looks plausible in a report. It would also **double-count**: the
included file's routes would appear once unprefixed and once under each includer.
So the extractor expands from the files nothing else includes, which is the set of
roots without needing `ROOT_URLCONF` (and therefore without reading `settings.py`,
which may not be the only settings module).

**An unresolvable `include()` degrades rather than guessing.** A dotted path with
no file in the index -- a third-party app's urlconf, most often -- yields the
prefix as an endpoint with no handler and no expansion, which is a true statement
about what the tree declares. Inventing the included routes is the failure `01`
§1.6 names.
"""

from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.fileindex import FileEntry
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from tree_sitter import Node

from adopt_extractors_web._grammar import matches, node_text, parse, string_value
from adopt_extractors_web._routes import ANY_METHOD, HTTP_METHODS, endpoint_fact

__all__ = ["MANIFEST", "DjangoRoutesExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="web.django.routes",
    version="1.0.0",
    pack="web",
    archetypes=["web"],
    kinds=["endpoint"],
    method="grammar",
)

_FRAMEWORK: Final[str] = "django"

#: `path("x/", view)` and its siblings. The first string argument is the route;
#: the second argument, whatever its shape, is the view reference.
_ROUTE_PATTERN: Final[str] = """
(call
  function: [(identifier) @fn (attribute attribute: (identifier) @fn)]
  arguments: (argument_list . (string) @route . (_)? @view)) @call
"""

#: A class body, for resolving a class-based view's handler methods.
_CLASS_PATTERN: Final[str] = """
(class_definition name: (identifier) @name body: (block) @body) @class
"""

#: A decorated function, for resolving a function-based view's declared methods.
_FUNCTION_PATTERN: Final[str] = """
(decorated_definition
  (decorator) @decorator
  definition: (function_definition name: (identifier) @name)) @decorated
"""

_ROUTE_CALLS: Final[frozenset[str]] = frozenset({"path", "re_path", "url"})
_INCLUDE_CALLS: Final[frozenset[str]] = frozenset({"include"})


class DjangoRoutesExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        """Whether the tree declares Django urlpatterns anywhere.

        Reads file *names* only. `applies_to` runs during planning, before the
        index exists, and opening every Python file here would be the second walk
        `03` §5.8 forbids.
        """
        return any(Path(root).rglob("urls.py"))

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `endpoint` per served route, with `include()` prefixes applied."""
        urlconfs, methods = _read_tree(ctx)
        if not urlconfs:
            return
        included = {
            target
            for module in sorted(urlconfs)
            for target in urlconfs[module].includes
            if target in urlconfs
        }
        for module in sorted(set(urlconfs) - included):
            yield from _expand(ctx, urlconfs, methods, module, prefix="", seen=frozenset())


class _Urlconf:
    """One `urls.py`'s routes, already read once.

    Held as a small object rather than re-parsed per includer: a urlconf included
    from three places is parsed once, which is the same one-read discipline
    `03` §5.8 applies to the tree walk.
    """

    __slots__ = ("entry", "includes", "routes")

    def __init__(self, entry: FileEntry, routes: list["_Route"]) -> None:
        self.entry = entry
        self.routes = routes
        self.includes = tuple(route.include for route in routes if route.include is not None)


class _Route:
    """One `path(...)` call, before prefixes are applied."""

    __slots__ = ("handler", "include", "line", "order", "route")

    def __init__(
        self,
        route: str,
        handler: str | None,
        include: str | None,
        line: int,
        order: int,
    ) -> None:
        self.route = route
        self.handler = handler
        self.include = include
        self.line = line
        self.order = order


def _module_path(path: str) -> str:
    """`orders/urls.py` -> `orders.urls`, which is what `include()` names."""
    relative = PurePosixPath(path)
    return ".".join([*relative.parts[:-1], relative.stem])


def _read_tree(ctx: ExtractorContext) -> tuple[dict[str, _Urlconf], dict[str, tuple[str, ...]]]:
    """One pass over the Python files, returning the urlconfs and the view methods.

    **One pass, because the two answers come from different files.** A urlconf
    assigns `urlpatterns`; the view it names lives in `views.py`. Reading them in
    separate passes would parse most of the tree twice, and `03` §5.8's one-walk
    rule is about exactly that kind of duplication.

    A file counts as a urlconf when it assigns `urlpatterns` -- Django's own
    contract for one -- rather than when it is *named* `urls.py`, because a
    project splitting routes across `api_urls.py` is ordinary and a name check
    would silently miss all of it.
    """
    urlconfs: dict[str, _Urlconf] = {}
    methods: dict[str, tuple[str, ...]] = {}
    for entry in ctx.files(language="python"):
        ctx.budget.check()
        text = ctx.text(entry)
        has_routes = "urlpatterns" in text
        # A cheap text prefilter before the parser: most files in a Django tree
        # declare neither a urlconf nor a view, and parsing them to discover that
        # is the largest avoidable cost in this extractor.
        has_views = "class " in text or "require_" in text or "api_view" in text
        if not (has_routes or has_views):
            continue
        root, data = parse("python", text)
        if has_routes:
            routes = _routes_in(root, data)
            if routes:
                urlconfs[_module_path(entry.path)] = _Urlconf(entry, routes)
        if has_views:
            methods.update(_methods_in(root, data))
    return urlconfs, methods


def _routes_in(root: Node, data: bytes) -> list["_Route"]:
    routes: list[_Route] = []
    for order, capture in enumerate(matches("python", _ROUTE_PATTERN, root)):
        names = capture.get("fn") or []
        if not names:
            continue
        call = node_text(names[0], data)
        if call not in _ROUTE_CALLS:
            continue
        route_nodes = capture.get("route") or []
        if not route_nodes:
            continue
        route = string_value(route_nodes[0], data)
        view_nodes = capture.get("view") or []
        view = node_text(view_nodes[0], data) if view_nodes else ""
        include = _include_target(view)
        routes.append(
            _Route(
                route=route,
                handler=None if include is not None else _handler_symbol(view),
                include=include,
                line=route_nodes[0].start_point[0] + 1,
                order=order,
            )
        )
    return routes


def _include_target(view: str) -> str | None:
    """The dotted module an `include(...)` names, or `None`.

    Matched on the call's text rather than on a second query, because the view
    capture is already the whole expression and an `include` is the only shape
    whose *first* argument is the thing we need.
    """
    stripped = view.strip()
    for name in _INCLUDE_CALLS:
        opener = f"{name}("
        if stripped.startswith(opener) or stripped.startswith(f"urls.{opener}"):
            inner = stripped[stripped.index("(") + 1 :].strip()
            for quote in ('"', "'"):
                if inner.startswith(quote) and quote in inner[1:]:
                    return inner[1 : inner.index(quote, 1)]
            return ""
    return None


def _handler_symbol(view: str) -> str | None:
    """`views.OrderDetail.as_view()` -> `views.OrderDetail`; `health` -> `health`.

    Returns `None` for anything that is not a plain reference -- a lambda, a
    `functools.partial`, a subscript. A handler we cannot name is recorded as
    absent rather than as a string that looks like a symbol and resolves to
    nothing.
    """
    stripped = view.strip()
    if stripped.endswith(".as_view()"):
        stripped = stripped[: -len(".as_view()")]
    if not stripped or "(" in stripped or "[" in stripped:
        return None
    if not all(part.isidentifier() for part in stripped.split(".") if part):
        return None
    return stripped


def _methods_in(root: Node, data: bytes) -> dict[str, tuple[str, ...]]:
    """`{view name: declared methods}` for the views declared in one file.

    Two shapes, because Django has two:

    * **Class-based.** The handler methods a view defines *are* the methods it
      serves -- `def get` and `def post` on the class body, and nothing else.
    * **Function-based.** The methods come from a decorator: `@require_GET`,
      `@require_POST`, `@require_http_methods([...])`, or DRF's `@api_view([...])`.

    A view matching neither shape is absent from this map and its route is keyed
    `ANY`, which is what Django actually does with it.
    """
    declared: dict[str, tuple[str, ...]] = {}
    for capture in matches("python", _CLASS_PATTERN, root):
        names = capture.get("name") or []
        bodies = capture.get("body") or []
        if not names or not bodies:
            continue
        body = node_text(bodies[0], data)
        methods = tuple(
            method
            for method in HTTP_METHODS
            if f"def {method}(" in body or f"def {method} (" in body
        )
        if methods:
            declared[node_text(names[0], data)] = methods
    for capture in matches("python", _FUNCTION_PATTERN, root):
        names = capture.get("name") or []
        decorators = capture.get("decorator") or []
        if not names:
            continue
        methods = _decorated_methods(" ".join(node_text(node, data) for node in decorators))
        if methods:
            declared[node_text(names[0], data)] = methods
    return declared


def _decorated_methods(decorators: str) -> tuple[str, ...]:
    lowered = decorators.lower()
    found = {method for method in HTTP_METHODS if f"require_{method}" in lowered}
    for method in HTTP_METHODS:
        if f'"{method}"' in lowered or f"'{method}'" in lowered:
            found.add(method)
    return tuple(method for method in HTTP_METHODS if method in found)


def _expand(
    ctx: ExtractorContext,
    urlconfs: dict[str, _Urlconf],
    methods: dict[str, tuple[str, ...]],
    module: str,
    *,
    prefix: str,
    seen: frozenset[str],
) -> Iterator[SurfaceFact]:
    """Emit one urlconf's routes, recursing through its `include()`s.

    `seen` breaks an import cycle. A urlconf that includes itself, directly or
    through a chain, is a client defect rather than ours, and the honest response
    is to stop expanding rather than to recurse until the interpreter does.
    """
    if module in seen:
        return
    conf = urlconfs[module]
    seen = seen | {module}
    for route in conf.routes:
        ctx.budget.check()
        full = f"{prefix}{route.route}"
        if route.include is not None:
            if route.include in urlconfs:
                yield from _expand(ctx, urlconfs, methods, route.include, prefix=full, seen=seen)
                continue
            # Unresolvable include: the mount point is real and everything under
            # it is not ours to invent.
            yield endpoint_fact(
                method=ANY_METHOD,
                path=full,
                framework=_FRAMEWORK,
                handler=None,
                handler_namespace="python",
                source=SourceRef(
                    path=conf.entry.path, start_line=route.line, blob_sha=conf.entry.blob_sha
                ),
                declaration_order=route.order,
            )
            continue
        for method in _methods_for(methods, route):
            yield endpoint_fact(
                method=method,
                path=full,
                framework=_FRAMEWORK,
                handler=route.handler,
                handler_namespace="python",
                source=SourceRef(
                    path=conf.entry.path, start_line=route.line, blob_sha=conf.entry.blob_sha
                ),
                declaration_order=route.order,
            )


def _methods_for(methods: dict[str, tuple[str, ...]], route: "_Route") -> tuple[str, ...]:
    """The methods one route serves, or `(ANY,)`.

    Resolved by the view's **last name segment**, because a urlconf writes
    `views.OrderDetail` and the class is declared as `OrderDetail`. That is a
    deliberately shallow resolution: this build has no import graph, and pretending
    to one would produce confident wrong answers where two apps declare a class of
    the same name. The cost of the collision is a route keyed with the wrong
    sibling's methods; the cost of *not* resolving is every Django route keyed
    `ANY`, which loses the method distinction that `02` §3.1 puts in the key.
    """
    if route.handler is None:
        return (ANY_METHOD,)
    return methods.get(route.handler.rsplit(".", 1)[-1], (ANY_METHOD,))
