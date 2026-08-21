"""The web pack -- HTTP endpoints, middleware/auth boundaries, schema fields.

Parsing is `ast`, not regular expressions, wherever the source is Python. That is
not a preference: a decorator's arguments, a class's bases and an assignment's
target are *structure*, and matching them textually produces both false
positives (a route in a docstring) and false negatives (a decorator split across
lines by a formatter). `ast` also gives exact line spans for free, which is what
`identity_revision.source_ref` records.

An unparseable file is skipped, never fatal. A client repository legitimately
contains Python 2, a template with placeholders, or a deliberately broken
fixture, and none of those should end a run.
"""

import ast
from collections.abc import Iterator
from typing import Final

from adopt_map.keys import MIDDLEWARE_NAMESPACE, endpoint_key, module_key
from adopt_map.observation import Observation, Span
from adopt_map.tree import SourceTree, TreeFile

__all__ = ["EndpointExtractor", "MiddlewareExtractor", "SchemaFieldExtractor"]

#: Decorator attribute names that name an HTTP method directly --
#: `@app.get(...)`, `@router.post(...)`. FastAPI, Flask 2.x and Starlette agree
#: on these, which is why one rule covers three frameworks.
_METHOD_DECORATORS: Final[frozenset[str]] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)

#: Django URL-conf callables. `path` and `re_path` take the route first.
_DJANGO_ROUTERS: Final[frozenset[str]] = frozenset({"path", "re_path", "url"})


def _parse(tree: SourceTree, entry: TreeFile) -> ast.Module | None:
    text = tree.text(entry)
    if text is None:
        return None
    try:
        return ast.parse(text, filename=entry.path)
    except (SyntaxError, ValueError):
        return None


def _span(entry: TreeFile, node: ast.AST) -> Span:
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", None) or start
    return Span(path=entry.path, start_line=start, end_line=end)


def _string_arg(node: ast.Call, index: int = 0) -> str | None:
    if len(node.args) <= index:
        return None
    argument = node.args[index]
    return (
        argument.value
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
        else None
    )


def _keyword_strings(node: ast.Call, name: str) -> list[str]:
    for keyword in node.keywords:
        if keyword.arg != name:
            continue
        if isinstance(keyword.value, ast.List | ast.Tuple):
            return [
                element.value
                for element in keyword.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return [keyword.value.value]
    return []


def _parameter_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """The handler's parameter names -- part of an endpoint's digest.

    v6.1 §6 names "method + path + parameter names" as an endpoint's attribute
    set. Names only: a type annotation changing from `int` to `str` is a real
    change but it is not one this build claims to detect, and including the
    annotation's *source text* would make a reformatting of it look semantic.
    `self` and `cls` are dropped -- they say how the handler is dispatched, not
    what it accepts.
    """
    arguments = function.args
    return [
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
        if argument.arg not in {"self", "cls"}
    ]


class EndpointExtractor:
    """HTTP endpoints: method + path, from decorators and Django URL confs."""

    name = "web.endpoints"
    version = "1"

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.iter_suffix(".py"):
            module = _parse(tree, entry)
            if module is None:
                continue
            yield from self._decorated(entry, module)
            yield from self._django_urls(entry, module)

    def _decorated(self, entry: TreeFile, module: ast.Module) -> Iterator[Observation]:
        prefixes = _router_prefixes(module)
        for node in ast.walk(module):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                attribute = decorator.func
                if not isinstance(attribute, ast.Attribute):
                    continue
                route = _string_arg(decorator)
                if route is None:
                    continue
                # `APIRouter(prefix="/items")` makes `@router.get("/")` serve
                # `GET /items/`, and recording the decorator's literal would name
                # an endpoint that does not exist -- permanently, since a URI is
                # never rewritten. See `_router_prefixes` for what this can and
                # cannot resolve.
                if isinstance(attribute.value, ast.Name):
                    route = prefixes.get(attribute.value.id, "") + route
                if attribute.attr in _METHOD_DECORATORS:
                    methods = [attribute.attr.upper()]
                elif attribute.attr == "route":
                    # Flask's default when `methods=` is absent is GET, and
                    # spelling that out here keeps the digest honest: an
                    # explicit `methods=["GET"]` and an omitted one are the
                    # same endpoint and must produce the same identity.
                    methods = [
                        method.upper() for method in _keyword_strings(decorator, "methods")
                    ] or ["GET"]
                else:
                    continue
                for method in sorted(set(methods)):
                    yield Observation(
                        kind="endpoint",
                        key=endpoint_key(method, route),
                        namespace=None,
                        attributes={
                            "method": method,
                            "path": route,
                            "parameters": _parameter_names(node),
                        },
                        span=_span(entry, node),
                        note=f"{attribute.attr} decorator on {node.name}",
                    )

    def _django_urls(self, entry: TreeFile, module: ast.Module) -> Iterator[Observation]:
        if not entry.path.endswith("urls.py"):
            return
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            called = (
                function.id
                if isinstance(function, ast.Name)
                else function.attr
                if isinstance(function, ast.Attribute)
                else None
            )
            if called not in _DJANGO_ROUTERS:
                continue
            route = _string_arg(node)
            if route is None:
                continue
            # A URL conf declares a route without declaring its methods -- those
            # live on the view. `ANY` records that honestly rather than guessing
            # GET, because a guess here would mint an identity that never
            # matches the endpoint the view actually serves.
            yield Observation(
                kind="endpoint",
                key=endpoint_key("ANY", route),
                namespace="django",
                attributes={"method": "ANY", "path": route, "parameters": []},
                span=_span(entry, node),
                note=f"{called}() in a Django URL conf",
            )


class MiddlewareExtractor:
    """Middleware and auth boundaries -- where a request is intercepted.

    Two shapes: Django's `MIDDLEWARE` list, and `add_middleware(...)` as used by
    Starlette and FastAPI.
    """

    name = "web.middleware"
    version = "1"

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.iter_suffix(".py"):
            module = _parse(tree, entry)
            if module is None:
                continue
            yield from self._django_setting(entry, module)
            yield from self._add_middleware(entry, module)

    def _django_setting(self, entry: TreeFile, module: ast.Module) -> Iterator[Observation]:
        for node in module.body:
            if not isinstance(node, ast.Assign):
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "MIDDLEWARE" not in targets and "MIDDLEWARE_CLASSES" not in targets:
                continue
            if not isinstance(node.value, ast.List | ast.Tuple):
                continue
            for position, element in enumerate(node.value.elts):
                if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                    continue
                dotted = element.value
                yield Observation(
                    kind="symbol",
                    key=tuple(dotted.split(".")),
                    namespace=MIDDLEWARE_NAMESPACE,
                    # Order is an attribute because middleware order is
                    # behaviour: authentication after CSRF is a different system
                    # from authentication before it.
                    attributes={"target": dotted, "order": position},
                    span=_span(entry, element),
                )

    def _add_middleware(self, entry: TreeFile, module: ast.Module) -> Iterator[Observation]:
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_middleware":
                continue
            if not node.args:
                continue
            first = node.args[0]
            named = (
                first.id
                if isinstance(first, ast.Name)
                else first.attr
                if isinstance(first, ast.Attribute)
                else None
            )
            if named is None:
                continue
            yield Observation(
                kind="symbol",
                key=module_key(entry.path, named),
                namespace=MIDDLEWARE_NAMESPACE,
                attributes={"target": named, "registration": "add_middleware"},
                span=_span(entry, node),
            )


class SchemaFieldExtractor:
    """Persisted fields -- Django models and SQLAlchemy columns.

    The namespace is the model or table, so `id` on `Order` and `id` on
    `Customer` are two referents rather than one that both of them overwrite.
    """

    name = "web.schema_fields"
    version = "1"

    _FIELD_SUFFIX: Final[str] = "Field"

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.iter_suffix(".py"):
            module = _parse(tree, entry)
            if module is None:
                continue
            for node in ast.walk(module):
                if not isinstance(node, ast.ClassDef):
                    continue
                yield from self._fields_of(entry, node)

    def _fields_of(self, entry: TreeFile, klass: ast.ClassDef) -> Iterator[Observation]:
        declares_table = _declares_table(klass)
        for statement in klass.body:
            target, call = _field_assignment(statement)
            if target is None:
                continue
            constructor = _called_name(call) if call is not None else None
            if call is not None and constructor is not None:
                is_django = constructor.endswith(self._FIELD_SUFFIX)
                is_sqlalchemy = constructor in {"Column", "mapped_column"}
                if not (is_django or is_sqlalchemy):
                    continue
                options = sorted(
                    keyword.arg for keyword in call.keywords if keyword.arg is not None
                )
            elif declares_table and isinstance(statement, ast.AnnAssign):
                # A bare annotation inside a `table=True` class is a real column
                # -- `hashed_password: str` in a SQLModel table is the whole of
                # that column's declaration. Restricted to classes that declare
                # themselves tables, because taking bare annotations from every
                # class would mint a `db_field` for every dataclass, Protocol and
                # TypedDict attribute in the repository.
                constructor = ast.unparse(statement.annotation)
                options = []
            else:
                continue
            yield Observation(
                kind="db_field",
                key=(target,),
                namespace=klass.name,
                attributes={"name": target, "type": constructor, "options": options},
                span=_span(entry, statement),
            )


def _field_assignment(statement: ast.stmt) -> tuple[str | None, ast.Call | None]:
    """`name = Something(...)` or `name: T = Something(...)`, else `(None, None)`."""
    if isinstance(statement, ast.Assign):
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            return None, None
        value = statement.value
        return (statement.targets[0].id, value) if isinstance(value, ast.Call) else (None, None)
    if isinstance(statement, ast.AnnAssign):
        if not isinstance(statement.target, ast.Name):
            return None, None
        annotated = statement.value
        # A bare annotation returns its target with no call: whether that counts
        # as a column depends on the class, which `_fields_of` knows and this
        # does not.
        return statement.target.id, annotated if isinstance(annotated, ast.Call) else None
    return None, None


def _router_prefixes(module: ast.Module) -> dict[str, str]:
    """Router variable -> its declared path prefix, for prefixes visible here.

    Resolves the common and locally-decidable case: `router = APIRouter(
    prefix="/items")` at module level, which makes every decorator on `router`
    serve a path the decorator's own literal does not contain.

    **What this deliberately does not resolve**, because it is not decidable from
    one module: a prefix applied at the *mount* site
    (`include_router(other.router, prefix="/x")`), and a prefix given as a
    variable rather than a literal (`prefix=settings.API_V1_STR`) -- the second
    of which needs the value of a setting, which would mean executing the
    client's configuration. Endpoint paths from such a repository are therefore
    relative to their mount point, and the curated `--check-expected` list is
    where that shows up as a named miss rather than as a silent wrong answer.
    That is exactly the job v6.1 §6 H1 gives the recall floor.
    """
    prefixes: dict[str, str] = {}
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        call = node.value
        if not isinstance(target, ast.Name) or not isinstance(call, ast.Call):
            continue
        if _called_name(call) != "APIRouter":
            continue
        for keyword in call.keywords:
            if keyword.arg != "prefix":
                continue
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                prefixes[target.id] = keyword.value.value.rstrip("/")
    return prefixes


def _declares_table(klass: ast.ClassDef) -> bool:
    """Whether the class declares itself a database table.

    SQLModel's marker is the class keyword `table=True`; Django's is inheriting
    `models.Model`. Both say "rows of this exist in a database", which is what
    makes a bare annotation inside them a column rather than an attribute.
    """
    for keyword in klass.keywords:
        if (
            keyword.arg == "table"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
        ):
            return True
    return any(isinstance(base, ast.Attribute) and base.attr == "Model" for base in klass.bases)


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None
