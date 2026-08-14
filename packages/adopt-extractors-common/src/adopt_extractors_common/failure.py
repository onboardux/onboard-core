"""`common.failure` -- failure surfaces, as `symbol` and `config_key` (`03` §5.10).

`02` §3.1 rule 5 is explicit and worth quoting, because it is the rule an
extractor author is most likely to break: *"Failure surfaces are `symbol`
(handlers) or `config_key` (retry policies, DLQ destinations, alert hooks). No
separate kind exists and none is invented."*

That matters more than it looks. A `failure_surface` kind would be the fourteenth
member of a closed enum (`01` F2.2), and every coverage denominator downstream
would then depend on whether a given system's failures happened to be modelled.
Mapping them onto the two existing kinds keeps coverage arithmetic comparable
across systems that handle failure very differently.

**Regex, and it says so.** These are recovered by pattern over text, so the
manifest declares `method="regex"` and the framework bands them at
`MAP_CONF_REGEX` (0.45) -- above `MAP_MIN_EMIT_CONFIDENCE` (0.40), so they are
knowledge with visibly low confidence rather than silent gaps. Declaring
`grammar` to get a better number would be the extractor assigning its own
confidence by the back door, which is what `02` §7 obligation 5 forbids.
"""

import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact

__all__ = ["MANIFEST", "FailureExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="common.failure",
    version="1.0.0",
    pack="common",
    archetypes=["web", "ai", "data"],
    kinds=["symbol", "config_key"],
    method="regex",
    fallback=None,
)

#: An exception-handler declaration. Python's `except X:` and the
#: `@app.errorhandler(...)` / `@exception_handler(...)` decorator families, which
#: is where a framework's failure path is actually named.
_HANDLER_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:@(?:\w+\.)*(?P<decorator>errorhandler|exception_handler|error_handler)\s*\("
    r"|except\s+(?P<exception>[A-Za-z_][\w.]*)\s*(?:as\s+\w+\s*)?:)",
    re.MULTILINE,
)

#: A retry, backoff, dead-letter or alert setting. These are `config_key`, not a
#: kind of their own (rule 5).
_POLICY_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<key>(?:max_)?retries|retry_backoff|retry_policy|dead_letter\w*|dlq\w*|"
    r"alert_(?:hook|channel|webhook)|on_failure)\b",
    re.IGNORECASE,
)

_SOURCE_SUFFIXES: Final[frozenset[str]] = frozenset({".py", ".ts", ".js", ".rb", ".go"})


class FailureExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        del root
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """Handlers as `symbol`, policies as `config_key`. Deterministic order."""
        for entry in ctx.files():
            ctx.budget.check()
            if PurePosixPath(entry.path).suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            text = ctx.text(entry)
            module = _module_of(entry.path)
            language = entry.language or "unknown"

            seen_handlers: set[str] = set()
            for match in _HANDLER_RE.finditer(text):
                name = match.group("exception") or match.group("decorator")
                key = f"{module}.handles.{name}"
                if key in seen_handlers:
                    continue
                seen_handlers.add(key)
                yield SurfaceFact(
                    identity_kind="symbol",
                    namespace=language,
                    local_key=key,
                    title=f"handles {name}",
                    attributes={"raises": [name]},
                    source_refs=[
                        SourceRef(
                            path=entry.path,
                            start_line=text.count("\n", 0, match.start()) + 1,
                            blob_sha=entry.blob_sha,
                        )
                    ],
                )

            seen_policies: set[str] = set()
            for match in _POLICY_RE.finditer(text):
                key = f"{module}.{match.group('key').lower()}"
                if key in seen_policies:
                    continue
                seen_policies.add(key)
                yield SurfaceFact(
                    identity_kind="config_key",
                    namespace="failure",
                    local_key=key,
                    title=match.group("key"),
                    # The key path and nothing else: a retry count read out of a
                    # source line is a value we did not parse, and recording one
                    # we guessed at would be worse than recording none.
                    attributes={"key_path": key},
                    source_refs=[
                        SourceRef(
                            path=entry.path,
                            start_line=text.count("\n", 0, match.start()) + 1,
                            blob_sha=entry.blob_sha,
                        )
                    ],
                )


def _module_of(path: str) -> str:
    """A dotted module path from a repo-relative file path."""
    relative = PurePosixPath(path)
    return ".".join([*relative.parts[:-1], relative.stem])
