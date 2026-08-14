"""`web.openapi` -- OpenAPI 3.x documents as `reflection`-method evidence.

**Enumerates; does not validate** (B1-CR-65, OD-12). `03` §2 named
`openapi-spec-validator`, which brings thirteen in-binary distributions to answer
a question this extractor never asks: it walks `paths` → operations and reports
what is declared. A document that does not conform produces fewer facts and a
gap, which is `01` §1.6's posture toward everything it cannot read.

**`reflection`, not `grammar`** (`01` F8.1, `03` §3). The evidence is a contract
artifact the system publishes about itself rather than a parse of its
implementation, and `MAP_CONF_REFLECTION` bands it slightly below a grammar
parse for exactly that reason: the spec can be stale in a way the code cannot.

**This is the extractor most likely to agree with another one**, and that is the
point of `02` §10 C1: a Django route and an OpenAPI operation for one endpoint
must mint one URI. They do, because both hand `<METHOD> <path>` to the same
normalization -- which is where B1-CR-66's leading slash turned out to matter.
"""

import json
from collections.abc import Iterator, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

import yaml
from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from adopt_map.sourceversion import digest_payload

from adopt_extractors_web._routes import HTTP_METHODS, endpoint_fact

__all__ = ["MANIFEST", "OpenapiExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="web.openapi",
    version="1.0.0",
    pack="web",
    archetypes=["web"],
    kinds=["endpoint"],
    method="reflection",
)

_FRAMEWORK: Final[str] = "openapi"

#: Filenames that hold an OpenAPI document often enough to be worth opening. The
#: content check below is what actually decides, so a project using another name
#: is caught by the `openapi:`/`swagger:` marker rather than missed by this list.
_LIKELY_NAMES: Final[frozenset[str]] = frozenset(
    {"openapi.yaml", "openapi.yml", "openapi.json", "swagger.yaml", "swagger.yml", "swagger.json"}
)

_SUFFIXES: Final[frozenset[str]] = frozenset({".yaml", ".yml", ".json"})


class OpenapiExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return any(any(Path(root).rglob(f"*{suffix}")) for suffix in sorted(_SUFFIXES))

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `endpoint` per declared operation, in document order."""
        for entry in ctx.files():
            ctx.budget.check()
            name = PurePosixPath(entry.path).name.lower()
            if PurePosixPath(name).suffix not in _SUFFIXES:
                continue
            text = ctx.text(entry)
            if name not in _LIKELY_NAMES and not _looks_like_openapi(text):
                continue
            document = _load(text)
            if document is None:
                continue
            yield from _operations(document, entry.path, entry.blob_sha)


def _looks_like_openapi(text: str) -> bool:
    head = text[:4096].lower()
    return '"openapi"' in head or "openapi:" in head or "swagger:" in head or '"swagger"' in head


def _load(text: str) -> Mapping[str, Any] | None:
    """The document, or `None` when it is not a mapping we can read.

    `yaml.safe_load` **constructs no Python objects** -- it is the loader whose
    whole purpose is that a document cannot instantiate a class -- and it parses
    JSON too, since JSON is a YAML subset. So one call covers both encodings
    without a second dependency and without the `yaml.load` tag machinery that
    makes YAML dangerous.
    """
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(loaded, dict):
        return None
    if "paths" not in loaded:
        return None
    return loaded


def _operations(document: Mapping[str, Any], path: str, blob_sha: str) -> Iterator[SurfaceFact]:
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return
    order = 0
    for route in sorted(paths):
        operations = paths[route]
        if not isinstance(operations, dict):
            continue
        for method in HTTP_METHODS:
            operation = operations.get(method)
            if not isinstance(operation, dict):
                continue
            yield _fact(route, method, operation, path, blob_sha, order)
            order += 1


def _fact(
    route: str,
    method: str,
    operation: Mapping[str, Any],
    path: str,
    blob_sha: str,
    order: int,
) -> SurfaceFact:
    fact = endpoint_fact(
        method=method,
        path=route,
        framework=_FRAMEWORK,
        handler=None,
        handler_namespace="python",
        source=SourceRef(path=path, blob_sha=blob_sha),
        declaration_order=order,
    )
    attributes = dict(fact.attributes)
    attributes.update(
        {
            "parameters": _parameter_names(operation),
            "parameter_types": _parameter_types(operation),
            "request_schema_digest": _schema_digest(operation.get("requestBody")),
            "response_schema_digest": _schema_digest(operation.get("responses")),
            "status_codes": _status_codes(operation),
            "auth": _auth(operation),
            "summary": _text(operation.get("summary")),
            "description": _text(operation.get("description")),
            "tags": [str(tag) for tag in operation.get("tags", []) if isinstance(tag, str)],
            "operation_name": _text(operation.get("operationId")),
        }
    )
    return fact.model_copy(update={"attributes": attributes})


def _parameters(operation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    declared = operation.get("parameters")
    if not isinstance(declared, list):
        return []
    return [item for item in declared if isinstance(item, dict)]


def _parameter_names(operation: Mapping[str, Any]) -> list[str]:
    return sorted(
        str(item["name"]) for item in _parameters(operation) if isinstance(item.get("name"), str)
    )


def _parameter_types(operation: Mapping[str, Any]) -> dict[str, str]:
    types: dict[str, str] = {}
    for item in _parameters(operation):
        name = item.get("name")
        schema = item.get("schema")
        if (
            isinstance(name, str)
            and isinstance(schema, dict)
            and isinstance(schema.get("type"), str)
        ):
            types[name] = str(schema["type"])
    return types


def _status_codes(operation: Mapping[str, Any]) -> list[int]:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return []
    codes = []
    for key in responses:
        text = str(key)
        if text.isdigit():
            codes.append(int(text))
    return sorted(codes)


def _auth(operation: Mapping[str, Any]) -> str | None:
    """The security scheme names this operation requires, or `None`.

    An **empty** `security: []` is meaningful in OpenAPI -- it means the operation
    is explicitly public -- so it renders as `"none"` rather than falling through
    to `None`, which would be indistinguishable from an operation that declares
    nothing at all.
    """
    security = operation.get("security")
    if security is None:
        return None
    if not isinstance(security, list):
        return None
    if not security:
        return "none"
    names = sorted({name for entry in security if isinstance(entry, dict) for name in entry})
    return ",".join(names) if names else "none"


def _schema_digest(value: object) -> str | None:
    """A stable digest of a nested schema document, or `None` when absent.

    `02` §4.2 puts the request and response schema digests in the `endpoint`
    **semantic** projection, so this has to be a pure function of the schema's
    content: the same schema in two documents digests the same, and a reordered
    document digests the same as before. `digest_payload` canonicalizes with RFC
    8785 first, which is what makes both true.
    """
    if value is None:
        return None
    return digest_payload({"schema": _plain(value)})


def _plain(value: object) -> object:
    """The value as JSON types only.

    A YAML document can carry dates and other scalars RFC 8785 has no rendering
    for, and a digest that raised on one would fail a whole extraction over a
    field nobody reads. Round-tripping through `json` with a string fallback keeps
    the digest total, and `default=str` is deterministic for every scalar YAML
    produces.
    """
    return json.loads(json.dumps(value, default=str, sort_keys=True))


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None
