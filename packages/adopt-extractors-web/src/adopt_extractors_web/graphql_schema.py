"""`web.graphql` -- SDL root fields as `endpoint`s keyed `<Type>.<field>`.

`02` §3.1 gives GraphQL the `graphql` namespace and a `<Type>.<field>` key, so a
schema's `Query.orders` is one endpoint and `Mutation.createOrder` is another.
**Only root operation types mint**: a field on `type Order` is a shape inside a
response, not something a client can call, and minting one would inflate the
endpoint count with things no caller addresses.

**`graphql-core` earns its slot** where `openapi-spec-validator` did not
(B1-CR-65): it adds one MIT distribution with no runtime transitives, and it
parses SDL into an AST rather than validating it -- which is exactly the job.
Hand-rolling a GraphQL parser is the trade B1-CR-50 refused in the other
direction, and SDL has enough syntax (descriptions, directives, interfaces,
extensions) to make a regex version wrong on real schemas.
"""

from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from graphql import GraphQLSyntaxError, parse
from graphql.language import ast

__all__ = ["MANIFEST", "GraphqlExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="web.graphql",
    version="1.0.0",
    pack="web",
    archetypes=["web"],
    kinds=["endpoint"],
    method="reflection",
)

_FRAMEWORK: Final[str] = "graphql"

#: The three root operation types. A field on any other type is part of a
#: response shape rather than an address, and does not mint.
_ROOT_TYPES: Final[frozenset[str]] = frozenset({"Query", "Mutation", "Subscription"})

_SUFFIXES: Final[frozenset[str]] = frozenset({".graphql", ".gql", ".graphqls"})


class GraphqlExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return any(any(Path(root).rglob(f"*{suffix}")) for suffix in sorted(_SUFFIXES))

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `endpoint` per root field, in file then declaration order."""
        for entry in ctx.files():
            ctx.budget.check()
            if PurePosixPath(entry.path).suffix.lower() not in _SUFFIXES:
                continue
            text = ctx.text(entry)
            try:
                document = parse(text)
            except GraphQLSyntaxError:
                # An unparseable schema yields nothing and no invention.
                # `01` §1.6: silence beats guessing.
                continue
            yield from _fields(document, entry.path, entry.blob_sha)


def _fields(document: ast.DocumentNode, path: str, blob_sha: str) -> Iterator[SurfaceFact]:
    order = 0
    for definition in document.definitions:
        if not isinstance(definition, ast.ObjectTypeDefinitionNode | ast.ObjectTypeExtensionNode):
            continue
        type_name = definition.name.value
        if type_name not in _ROOT_TYPES:
            continue
        for field in definition.fields or ():
            yield _fact(type_name, field, path, blob_sha, order)
            order += 1


def _fact(
    type_name: str,
    field: ast.FieldDefinitionNode,
    path: str,
    blob_sha: str,
    order: int,
) -> SurfaceFact:
    key = f"{type_name}.{field.name.value}"
    arguments = tuple(field.arguments or ())
    return SurfaceFact(
        identity_kind="endpoint",
        namespace="graphql",
        local_key=key,
        title=key,
        attributes={
            "path": key,
            "framework": _FRAMEWORK,
            "operation_name": field.name.value,
            "parameters": sorted(argument.name.value for argument in arguments),
            "parameter_types": {
                argument.name.value: _type_name(argument.type) for argument in arguments
            },
            "response_schema_digest": None,
            "declaration_order": order,
            "description": field.description.value if field.description else None,
        },
        source_refs=[
            SourceRef(
                path=path,
                start_line=field.loc.start_token.line if field.loc else None,
                blob_sha=blob_sha,
            )
        ],
    )


def _type_name(node: ast.TypeNode) -> str:
    """A type reference rendered back to SDL -- `[Order!]!` and so on.

    Rendered rather than reduced to the bare name, because nullability and list
    depth are part of the contract: a field going from `[Order!]!` to `[Order]`
    is a semantic change to the caller, and flattening both to `Order` would put
    them in the same digest.
    """
    if isinstance(node, ast.NonNullTypeNode):
        return f"{_type_name(node.type)}!"
    if isinstance(node, ast.ListTypeNode):
        return f"[{_type_name(node.type)}]"
    if isinstance(node, ast.NamedTypeNode):
        return node.name.value
    # `TypeNode` is the open base of the three cases above. A fourth would be a
    # graphql-core addition, and naming it `unknown` keeps the digest stable
    # rather than raising inside an extractor over a client's schema.
    return "unknown"
