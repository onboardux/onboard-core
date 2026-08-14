"""`web.integrations` -- outbound HTTP calls, as `endpoint` with `direction: outbound`.

`02` §3.1 rule 4: *"Outbound integrations are `endpoint` with `namespace='http'`
and `direction: outbound` in attributes -- same kind, different attribute, so
coverage arithmetic stays simple."* No new kind, no new namespace.

**Only a literal URL mints.** `requests.get(settings.BILLING_URL)` names a
referent this extractor cannot resolve without evaluating the client's settings,
and a fact keyed `settings.BILLING_URL` would be an identity for a variable rather
than for a service. The call is therefore not minted, which understates the
integration surface -- stated plainly rather than papered over, and exactly the
kind of thing `01` F8.1's *"substantial truth sits outside the repo"* warns a
reader about.

**The host and path are kept; the query string is not.** `02` §3.2 rule 5 excludes
query strings from an `endpoint` key, and a query string is also the most common
place a literal URL carries a token.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from tree_sitter import Node

from adopt_extractors_web._grammar import matches, node_text, parse, string_value
from adopt_extractors_web._routes import ANY_METHOD, HTTP_METHODS, endpoint_fact

__all__ = ["MANIFEST", "IntegrationsExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="web.integrations",
    version="1.0.0",
    pack="web",
    archetypes=["web"],
    kinds=["endpoint"],
    method="grammar",
)

_FRAMEWORK: Final[str] = "http-client"

_LANGUAGES: Final[tuple[str, ...]] = ("python", "javascript", "typescript")

#: `requests.get("https://...")`, `httpx.post(...)`, `session.put(...)`.
_PY_PATTERN: Final[str] = """
(call
  function: (attribute object: (identifier) @client attribute: (identifier) @verb)
  arguments: (argument_list . (string) @url)) @call
"""

#: `fetch("https://...")` and `axios.get("https://...")`.
_JS_PATTERN: Final[str] = """
[(call_expression
   function: (member_expression object: (identifier) @client property: (property_identifier) @verb)
   arguments: (arguments . (string) @url))
 (call_expression
   function: (identifier) @client
   arguments: (arguments . (string) @url))] @call
"""

_CLIENTS: Final[frozenset[str]] = frozenset(
    {"requests", "httpx", "session", "client", "http", "axios", "fetch"}
)

_SCHEMES: Final[tuple[str, ...]] = ("http://", "https://")


class IntegrationsExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return any(Path(root).rglob("*.py")) or any(Path(root).rglob("*.ts"))

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One outbound `endpoint` per literal URL, deduplicated per file."""
        for language in _LANGUAGES:
            pattern = _PY_PATTERN if language == "python" else _JS_PATTERN
            for entry in ctx.files(language=language):
                ctx.budget.check()
                text = ctx.text(entry)
                if not any(scheme in text for scheme in _SCHEMES):
                    continue
                root, data = parse(language, text)
                yield from _calls(root, data, pattern, language, entry.path, entry.blob_sha)


def _calls(
    root: Node, data: bytes, pattern: str, language: str, path: str, blob_sha: str
) -> Iterator[SurfaceFact]:
    seen: set[str] = set()
    order = 0
    for capture in matches(language, pattern, root):
        urls = capture.get("url") or []
        clients = capture.get("client") or []
        if not urls:
            continue
        client = node_text(clients[0], data) if clients else ""
        if client.lower() not in _CLIENTS:
            continue
        url = string_value(urls[0], data)
        if not url.startswith(_SCHEMES):
            continue
        verbs = capture.get("verb") or []
        verb = node_text(verbs[0], data).lower() if verbs else ""
        method = verb.upper() if verb in HTTP_METHODS else ANY_METHOD
        target = _target(url)
        key = f"{method} {target}"
        if key in seen:
            # One call site per referent per file. A retry loop calling the same
            # endpoint three times is one integration, not three.
            continue
        seen.add(key)
        yield endpoint_fact(
            method=method,
            path=target,
            framework=_FRAMEWORK,
            handler=None,
            handler_namespace=language,
            source=SourceRef(path=path, start_line=urls[0].start_point[0] + 1, blob_sha=blob_sha),
            declaration_order=order,
            direction="outbound",
        )
        order += 1


def _target(url: str) -> str:
    """`https://billing.example.com/v1/charges?x=1` -> `billing.example.com/v1/charges`.

    **The host is kept and the scheme is dropped.** The host is what makes an
    outbound endpoint identifiable -- two vendors' `/v1/charges` are different
    referents -- while `http` versus `https` is a transport detail that would fork
    one referent the day somebody fixes the URL.

    The result is deliberately *not* prefixed with `//`: `02` §3.2 rule 4 collapses
    repeated separators before B1-CR-66's leading-slash rule runs, so a `//` would
    become `/` anyway and the docstring promising otherwise would be the only
    place it survived. Normalization renders this as
    `GET /billing.example.com/v1/charges`, which is stable, unique per host, and
    distinguishable from an inbound route by `direction: outbound` rather than by
    its spelling.
    """
    for scheme in _SCHEMES:
        if url.startswith(scheme):
            url = url[len(scheme) :]
            break
    for separator in ("?", "#"):
        url = url.split(separator, 1)[0]
    return url.rstrip("/") or "/"
