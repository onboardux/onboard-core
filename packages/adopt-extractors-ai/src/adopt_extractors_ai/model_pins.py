"""`ai.model_pins` -- which model, pinned how, at which call site.

`01` F8.8: *"A floating model pin (a `-latest`-style alias) gets its own callout.
It is the single highest-value finding this pack produces."* That is the whole
argument for this extractor: a floating pin means the deployment's behaviour can
change with no commit, no deploy and no notification, and nothing else in a
client's repository says so out loud.

**Three stabilities, and the third is the honest one.**

| `pin_stability` | When | Also |
|---|---|---|
| `pinned` | The id carries a date or an explicit version | — |
| `floating` | The id is an alias the provider resolves -- `-latest`, or a family name with no version | The `surface.md` callout |
| `unknown` | The id is resolved from the environment at start-up | `outside_vcs=True` |

**A runtime-resolved pin is outside-VCS but not opaque**, and the distinction is
load-bearing. `01` F8.6 is about *where a setting lives*; F8.7 is about *content
we cannot read*. The id is unreadable, but the provider, the temperature and the
call site are all right there in the file -- so the semantic digest still covers
them, and a temperature change on a runtime-resolved pin still writes a revision.
Marking it opaque would null the digest and silently swallow that change, which
is B1-CR-44's failure wearing F8.7's clothes.
"""

import re
from collections.abc import Iterator
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.fileindex import FileEntry
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from tree_sitter import Node

from adopt_extractors_ai._grammar import (
    is_module_level,
    keyword_arguments,
    matches,
    node_text,
    parse,
    string_value,
)

__all__ = ["MANIFEST", "ModelPinsExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="ai.model_pins",
    version="1.0.0",
    pack="ai",
    archetypes=["ai"],
    kinds=["model_pin"],
    method="grammar",
)

#: `name = SomeClient(model=..., temperature=...)`. The assignment target is the
#: call site: it is what the rest of the module refers to, and it is stable
#: across reformatting in a way a line number is not.
#:
#: Module level is asserted afterwards through `is_module_level` rather than
#: anchored in the pattern -- the `(module (expression_statement ...))` anchor
#: matches nothing on the pinned grammar, and matching nothing is silent.
_ASSIGNED_CALL_PATTERN: Final[str] = """
(assignment
  left: (identifier) @site
  right: (call function: [(identifier) @fn (attribute attribute: (identifier) @fn)]) @call) @assign
"""

#: The keyword arguments that name a model, in preference order.
_MODEL_KEYWORDS: Final[tuple[str, ...]] = ("model", "model_name", "model_id", "deployment_name")

#: `02` §3.1 gives `model_pin` three namespaces: `anthropic`, `openai`, `local`.
#: Resolved from the callee first and the model id second, because a client class
#: names its provider more reliably than an id does.
_PROVIDER_BY_CALLEE: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("anthropic", ("anthropic", "claude")),
    ("openai", ("openai", "azurechat", "azureopenai")),
    ("local", ("ollama", "llamacpp", "vllm", "local", "gpt4all", "lmstudio")),
)
_PROVIDER_BY_MODEL: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("anthropic", ("claude",)),
    ("openai", ("gpt-", "o1-", "o3-", "text-embedding-")),
    ("local", ("llama", "mistral", "qwen", "phi-")),
)

#: A dated or explicitly versioned id: `claude-sonnet-4-5-20250929`,
#: `gpt-4o-2024-08-06`, `model:v3`.
_PINNED_RE: Final[re.Pattern[str]] = re.compile(r"(?:\d{8}|\d{4}-\d{2}-\d{2}|[:@]v?\d+(?:\.\d+)*)$")

#: An alias the provider resolves at call time.
_FLOATING_MARKERS: Final[tuple[str, ...]] = ("latest", "current", "preview", "stable")

#: `os.environ["X"]`, `os.environ.get("X")`, `os.getenv("X")`, `getenv("X")`.
_ENV_READ_PATTERN: Final[str] = """
[(subscript
   value: (attribute attribute: (identifier) @attr)
   subscript: (string) @var) @env
 (call
   function: [(identifier) @fname (attribute attribute: (identifier) @fname)]
   arguments: (argument_list . (string) @var)) @env]
"""
_ENV_READERS: Final[tuple[str, ...]] = ("environ", "getenv")


class ModelPinsExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        for entry in ctx.files(language="python"):
            ctx.budget.check()
            text = ctx.text(entry)
            root, data = parse("python", text)
            for capture in matches("python", _ASSIGNED_CALL_PATTERN, root):
                fact = _fact(capture, data, entry)
                if fact is not None:
                    yield fact


def _fact(capture: dict[str, list[Node]], data: bytes, entry: FileEntry) -> SurfaceFact | None:
    sites = capture.get("site") or []
    callees = capture.get("fn") or []
    calls = capture.get("call") or []
    assignments = capture.get("assign") or []
    if not (sites and callees and calls and assignments):
        return None
    if not is_module_level(assignments[0]):
        # A client constructed inside a function is still a pin, but its call
        # site is the function rather than the variable, and this pack does not
        # yet read one. Recorded as nothing rather than keyed by a name that
        # would collide across functions.
        return None

    keywords = keyword_arguments(calls[0], data)
    model_node = next((keywords[name] for name in _MODEL_KEYWORDS if name in keywords), None)
    if model_node is None:
        return None

    model_id, outside_vcs = _model_id(model_node, data)
    if not model_id:
        return None

    callee = node_text(callees[0], data)
    provider = _provider(callee, model_id)
    if provider is None:
        return None

    site = node_text(sites[0], data)
    return SurfaceFact(
        identity_kind="model_pin",
        namespace=provider,
        local_key=f"{model_id}@{site}",
        title=f"{model_id} at {site}",
        attributes={
            "provider": provider,
            "model_id": model_id,
            "pin_stability": _stability(model_id, outside_vcs=outside_vcs),
            "temperature": _number(keywords.get("temperature"), data),
            "max_tokens": _integer(keywords.get("max_tokens"), data),
            "top_p": _number(keywords.get("top_p"), data),
            "alias_display_name": callee,
        },
        source_refs=[
            SourceRef(
                path=entry.path,
                start_line=calls[0].start_point[0] + 1,
                blob_sha=entry.blob_sha,
            )
        ],
        outside_vcs=outside_vcs,
    )


def _model_id(node: Node, data: bytes) -> tuple[str, bool]:
    """`(model id, resolved outside version control)`.

    An environment read renders as `${VAR}` -- the shape an operator recognizes,
    and one that cannot be mistaken for a literal id.
    """
    if node.type == "string":
        return string_value(node, data), False
    for capture in matches("python", _ENV_READ_PATTERN, node):
        readers = [node_text(item, data) for item in (capture.get("attr") or [])]
        readers += [node_text(item, data) for item in (capture.get("fname") or [])]
        variables = capture.get("var") or []
        if variables and any(reader in _ENV_READERS for reader in readers):
            return f"${{{string_value(variables[0], data)}}}", True
    return "", False


def _provider(callee: str, model_id: str) -> str | None:
    lowered = callee.lower()
    for provider, markers in _PROVIDER_BY_CALLEE:
        if any(marker in lowered for marker in markers):
            return provider
    lowered_model = model_id.lower()
    for provider, markers in _PROVIDER_BY_MODEL:
        if any(marker in lowered_model for marker in markers):
            return provider
    # `02` §3.1 rule 1: the namespace enum is a convention, not a guess. A client
    # this pack cannot attribute produces no fact rather than a `model_pin` filed
    # under a provider nobody observed.
    return None


def _stability(model_id: str, *, outside_vcs: bool) -> str:
    if outside_vcs:
        return "unknown"
    lowered = model_id.lower()
    if any(marker in lowered for marker in _FLOATING_MARKERS):
        return "floating"
    if _PINNED_RE.search(lowered):
        return "pinned"
    # A family name with no version is resolved by the provider to whatever is
    # current, which is the same exposure a `-latest` alias carries and is
    # reported as such rather than as a pin nobody made.
    return "floating"


def _number(node: Node | None, data: bytes) -> float | None:
    if node is None or node.type not in {"integer", "float"}:
        return None
    return float(node_text(node, data))


def _integer(node: Node | None, data: bytes) -> int | None:
    if node is None or node.type != "integer":
        return None
    return int(node_text(node, data))
