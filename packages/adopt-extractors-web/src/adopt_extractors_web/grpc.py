"""`web.grpc` -- `service` / `rpc` declarations from `.proto` **source**.

`02` §3.1 gives gRPC the `grpc` namespace and a `<service>.<method>` key.

**Source, not compiled descriptors** (B1-CR-65, OD-12). `03` §2 named
`grpcio-tools`, which reads a `FileDescriptorSet` -- an artefact produced by a
build step, which a client repository usually does not commit. What it does commit
is the `.proto` file, and the grammar pack this build already takes carries a
`proto` grammar. So the extractor reads the artefact that is actually there, and
three distributions including a ~10 MB binary wheel stay out of the signed binary.

**The package prefixes the service name.** `package orders.v1;` plus
`service OrderService` is `orders.v1.OrderService`, which is what a client dials
and what appears on the wire. Dropping the package would fork against any other
extractor -- or any later build -- that reads the same service from a descriptor.
"""

from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from adopt_map.sourceversion import digest_payload
from tree_sitter import Node

from adopt_extractors_web._grammar import matches, node_text, parse

__all__ = ["MANIFEST", "GrpcExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="web.grpc",
    version="1.0.0",
    pack="web",
    archetypes=["web"],
    kinds=["endpoint"],
    method="grammar",
)

_FRAMEWORK: Final[str] = "grpc"

_SUFFIX: Final[str] = ".proto"

#: `package orders.v1;`
_PACKAGE_PATTERN: Final[str] = "(package) @package"

#: `service OrderService { ... }` with its whole body, so each `rpc` can be
#: attributed to the service that declares it. A flat `rpc` query would lose that
#: association and every method would need a nearest-preceding-service guess.
_SERVICE_PATTERN: Final[str] = """
(service_name) @name
"""

_RPC_PATTERN: Final[str] = """
(rpc_name) @name
"""


class GrpcExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return any(Path(root).rglob(f"*{_SUFFIX}"))

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `endpoint` per `rpc`, keyed `<package>.<service>.<method>`."""
        for entry in ctx.files():
            ctx.budget.check()
            if PurePosixPath(entry.path).suffix.lower() != _SUFFIX:
                continue
            text = ctx.text(entry)
            root, data = parse("proto", text)
            package = _package(root, data)
            yield from _services(root, data, package, entry.path, entry.blob_sha)


def _package(root: Node, data: bytes) -> str:
    for capture in matches("proto", _PACKAGE_PATTERN, root):
        for node in capture.get("package") or []:
            declared = node_text(node, data).strip().removeprefix("package").strip(" ;\t\n")
            if declared:
                return declared
    return ""


def _services(
    root: Node, data: bytes, package: str, path: str, blob_sha: str
) -> Iterator[SurfaceFact]:
    """Every `rpc`, attributed to its enclosing `service`.

    The association is taken from the tree rather than from source order: an
    `rpc` node's ancestors include its service definition, so walking up is
    exact where "the service declared most recently above this line" is a guess
    that a commented-out service breaks.
    """
    order = 0
    for capture in matches("proto", _RPC_PATTERN, root):
        for node in capture.get("name") or []:
            service = _enclosing_service(node, data)
            if service is None:
                continue
            method = node_text(node, data).strip()
            qualified = ".".join(part for part in (package, service, method) if part)
            yield SurfaceFact(
                identity_kind="endpoint",
                namespace="grpc",
                local_key=qualified,
                title=qualified,
                attributes={
                    "path": qualified,
                    "framework": _FRAMEWORK,
                    "operation_name": method,
                    "request_schema_digest": _message_digest(node, data, index=0),
                    "response_schema_digest": _message_digest(node, data, index=1),
                    "declaration_order": order,
                },
                source_refs=[
                    SourceRef(
                        path=path,
                        start_line=node.start_point[0] + 1,
                        blob_sha=blob_sha,
                    )
                ],
            )
            order += 1


def _enclosing_service(node: Node, data: bytes) -> str | None:
    current: Node | None = node.parent
    while current is not None:
        if current.type == "service":
            for child in current.children:
                if child.type == "service_name":
                    return node_text(child, data).strip()
            return None
        current = current.parent
    return None


def _message_digest(rpc_name: Node, data: bytes, *, index: int) -> str | None:
    """A digest of the request (`index=0`) or response (`index=1`) message type.

    The message *name* rather than its resolved fields: resolving a `.proto`
    import graph is a second build's worth of work, and the name is what changes
    when the contract changes. Digested rather than stored raw so that it sits in
    the same shape as `web.openapi`'s schema digests -- `02` §4.2 asks for a
    digest in the semantic projection, and two extractors putting different kinds
    of value in one projection field is how a comparison stops meaning anything.
    """
    parent = rpc_name.parent
    if parent is None:
        return None
    messages = [child for child in parent.children if child.type == "message_or_enum_type"]
    if index >= len(messages):
        return None
    return digest_payload({"message": node_text(messages[index], data).strip()})
