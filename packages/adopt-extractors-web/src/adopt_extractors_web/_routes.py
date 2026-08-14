"""What every route extractor in this pack agrees on.

Three decisions live here rather than in each framework's module, because a
framework disagreeing with another about any of them **forks the identity** --
`02` §3.1 rule 3 is that two extractors describing one referent produce
byte-identical URIs, and three of this pack's extractors can describe the same
endpoint.

1. **The key is `<METHOD> <path>`** and the path is handed through raw.
   `02` §3.2's normalization owns the parameter syntax, the case and the slashes,
   and B1-CR-66 added the leading one. An extractor that pre-normalized would be
   a second implementation of a rule whose entire purpose is having one.
2. **A method that is not declared is `ANY`, not a guess.** Django's `path()`
   routes *every* method to its view, and so does Express's `app.use`. `ANY` is a
   statement about what the framework declares; inventing `GET` would be the
   guess `01` §1.6 forbids, and it would fork against an OpenAPI document that
   says `GET` for real.
3. **`handler_symbol` carries the handler's `local_key`, never a URI** --
   B1-CR-67. An extractor has no way to mint one (`02` §7 obligation 6) and no
   framework step rewrites an attribute; the URI-valued edge is the `handled_by`
   relation, which the writer mints from the same `(kind, namespace, local_key)`.
"""

from typing import Final

from adopt_map.minting import ANY_METHOD
from adopt_map.schemas import FactRelation, SourceRef, SurfaceFact

__all__ = [
    "ANY_METHOD",
    "HTTP_METHODS",
    "endpoint_fact",
]

#: The methods a framework can declare. Lower-case here and uppercased by
#: `02` §3.2 rule 3 at the mint site, so this list never has to agree with the
#: normalizer's about case.
HTTP_METHODS: Final[tuple[str, ...]] = (
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "trace",
)

#: Re-exported, not redefined. `ANY` has to be a token `02` §3.2's normalization
#: recognises -- it takes the leading-slash rule like every other method -- so it
#: lives in `adopt_map.minting` beside the pattern that reads it. Defining it here
#: is what let `ANY admin` escape that rule while `GET /admin` received it.


def endpoint_fact(
    *,
    method: str,
    path: str,
    framework: str,
    handler: str | None,
    handler_namespace: str,
    source: SourceRef,
    declaration_order: int,
    direction: str = "inbound",
) -> SurfaceFact:
    """One inbound or outbound HTTP endpoint, in this pack's agreed shape.

    Args:
        method: A `HTTP_METHODS` member or `ANY_METHOD`.
        path: The route **as the framework declares it**. Not normalized here.
        framework: `django` · `fastapi` · `express` · `openapi` · … Recorded
            because `02` §4.2 puts it in the *semantic* projection: a route that
            moved framework is a changed referent, not a re-render.
        handler: The handler's symbol `local_key`, or `None` when the route
            declares none that can be read statically.
        handler_namespace: The handler symbol's language namespace.
        source: Where the route was declared.
        declaration_order: Position in the file. `02` §4.2 puts this in the
            **presentation** projection, so reordering routes is a render-only
            change rather than a semantic one.
        direction: `inbound` for a served route, `outbound` for a call this
            system makes (`02` §3.1 rule 4).
    """
    relations = []
    if handler is not None:
        relations.append(
            FactRelation(
                predicate="handled_by",
                target_kind="symbol",
                target_namespace=handler_namespace,
                target_local_key=handler,
            )
        )
    return SurfaceFact(
        identity_kind="endpoint",
        namespace="http",
        local_key=f"{method} {path}",
        title=f"{method} {path}",
        attributes={
            "http_method": method,
            "path": path,
            "framework": framework,
            "handler_symbol": handler,
            "direction": direction,
            "declaration_order": declaration_order,
        },
        relations=relations,
        source_refs=[source],
    )
