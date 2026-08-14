"""`ai.tools` -- the tools a deployment grants a model.

`02` §4.2's `tool_schema` semantic projection is *"tool name, parameter schema
digest, declared side-effect flag"*, and the third word of the third item does
the work: **declared**. A tool's docstring saying it writes something is prose,
and inferring a side-effect flag from prose would put a judgement nobody made
into the store. So `has_side_effects` is set only where the declaration carries
it, and is left unset otherwise -- the degrade ladder's rule (`01` F9.3) applied
to one field.

The parameter schema travels as a **digest** rather than as a structure, through
`adopt_map.sourceversion.digest_payload`, so this pack and the web pack cannot
disagree about the algorithm (`03` §3's one-home rule, applied to a function).
"""

from collections.abc import Iterator
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.fileindex import FileEntry
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from adopt_map.sourceversion import digest_payload
from tree_sitter import Node

from adopt_extractors_ai._grammar import (
    docstring_of,
    matches,
    node_text,
    parse,
    string_value,
)

__all__ = ["MANIFEST", "ToolsExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="ai.tools",
    version="1.0.0",
    pack="ai",
    archetypes=["ai"],
    kinds=["tool_schema"],
    method="grammar",
)

#: `@tool("lookup_order")` or `@tool` on a function definition.
#:
#: The docstring is **not** captured here. A function body's first statement is
#: an `expression_statement` wrapping a string in some grammar versions and a
#: bare string in others, so a pattern that names one shape silently matches
#: nothing on the other -- the defect that made the first run of this pack report
#: three identical parameter digests and no descriptions. `docstring_of` handles
#: both shapes in one place.
_DECORATED_PATTERN: Final[str] = """
(decorated_definition
  (decorator [(call function: [(identifier) @dec (attribute attribute: (identifier) @dec)]
                    arguments: (argument_list) @args)
              (identifier) @dec])
  definition: (function_definition
    name: (identifier) @name
    parameters: (parameters) @params) @definition) @decorated
"""

#: The decorator names that declare a tool. A closed list rather than "any
#: decorator with a string argument", because a decorator is the most common
#: shape in Python and the alternative mints a `tool_schema` per cache, retry and
#: route decorator in the tree.
_TOOL_DECORATORS: Final[tuple[str, ...]] = ("tool", "function_tool", "ai_function", "mcp_tool")

#: `02` §3.1 gives `tool_schema` four namespaces. Resolved from what the file
#: imports, because the decorator name is shared across frameworks and the import
#: is what says which one is in the tree.
_FRAMEWORK_BY_IMPORT: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("mcp", ("mcp",)),
    ("langgraph", ("langgraph", "langchain")),
    ("anthropic", ("anthropic",)),
    ("openai", ("openai",)),
)
_DEFAULT_FRAMEWORK: Final[str] = "langgraph"

#: The keyword arguments that *declare* a side effect, and what they mean.
#: `readonly=True` means no side effect; `writes=True` means one.
_SIDE_EFFECT_KEYWORDS: Final[tuple[tuple[str, bool], ...]] = (
    ("has_side_effects", True),
    ("writes", True),
    ("mutating", True),
    ("readonly", False),
    ("read_only", False),
)

#: Parameters that are not part of a tool's schema.
_IGNORED_PARAMETERS: Final[tuple[str, ...]] = ("self", "cls", "state", "config", "run_manager")


class ToolsExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        for entry in ctx.files(language="python"):
            ctx.budget.check()
            text = ctx.text(entry)
            framework = _framework(text)
            root, data = parse("python", text)
            for capture in matches("python", _DECORATED_PATTERN, root):
                fact = _fact(capture, data, entry, framework)
                if fact is not None:
                    yield fact


def _framework(text: str) -> str:
    lowered = text.lower()
    for framework, markers in _FRAMEWORK_BY_IMPORT:
        if any(f"import {marker}" in lowered or f"from {marker}" in lowered for marker in markers):
            return framework
    return _DEFAULT_FRAMEWORK


def _fact(
    capture: dict[str, list[Node]], data: bytes, entry: FileEntry, framework: str
) -> SurfaceFact | None:
    decorators = capture.get("dec") or []
    names = capture.get("name") or []
    parameters = capture.get("params") or []
    if not (decorators and names):
        return None
    if node_text(decorators[0], data) not in _TOOL_DECORATORS:
        return None

    declared = capture.get("args") or []
    tool_name = _declared_name(declared, data) or node_text(names[0], data)
    schema = _parameter_schema(parameters[0], data) if parameters else {}
    definitions = capture.get("definition") or []
    description = docstring_of(definitions[0], data) if definitions else None

    return SurfaceFact(
        identity_kind="tool_schema",
        namespace=framework,
        local_key=tool_name,
        title=tool_name,
        attributes={
            "tool_name": tool_name,
            "parameter_schema_digest": digest_payload(schema),
            "has_side_effects": _side_effect(declared, data),
            "description": description,
        },
        source_refs=[
            SourceRef(
                path=entry.path,
                start_line=names[0].start_point[0] + 1,
                blob_sha=entry.blob_sha,
            )
        ],
    )


def _declared_name(arguments: list[Node], data: bytes) -> str | None:
    """`@tool("lookup_order")` -> `lookup_order`."""
    for argument in arguments:
        for child in argument.named_children:
            if child.type == "string":
                return string_value(child, data)
    return None


def _side_effect(arguments: list[Node], data: bytes) -> bool | None:
    """The **declared** flag, or `None`. Never inferred from a docstring."""
    for argument in arguments:
        keywords = _keywords_of(argument, data)
        for name, meaning in _SIDE_EFFECT_KEYWORDS:
            value = keywords.get(name)
            if value is None:
                continue
            asserted = node_text(value, data) == "True"
            return asserted if meaning else not asserted
    return None


def _keywords_of(arguments: Node, data: bytes) -> dict[str, Node]:
    """`{name: value node}` for one **argument list**.

    `_grammar.keyword_arguments` takes a *call*; a decorator's arguments arrive
    here already unwrapped, and re-wrapping them to reuse that helper would be
    more code than the six lines below.
    """
    found: dict[str, Node] = {}
    for child in arguments.named_children:
        if child.type != "keyword_argument":
            continue
        name = child.child_by_field_name("name")
        value = child.child_by_field_name("value")
        if name is not None and value is not None:
            found[node_text(name, data)] = value
    return found


def _parameter_schema(parameters: Node, data: bytes) -> dict[str, object]:
    """`{parameter name: declared type}` -- the thing the digest covers."""
    schema: dict[str, object] = {}
    for child in parameters.named_children:
        name_node = _parameter_name(child)
        if name_node is None:
            continue
        name = node_text(name_node, data)
        if name in _IGNORED_PARAMETERS:
            continue
        annotation = child.child_by_field_name("type")
        schema[name] = node_text(annotation, data) if annotation is not None else None
    return schema


def _parameter_name(parameter: Node) -> Node | None:
    """The identifier in a parameter, whichever wrapper the grammar used.

    `order_id`, `order_id: str`, `top_k=5` and `top_k: int = 5` are four node
    types -- `identifier`, `typed_parameter`, `default_parameter` and
    `typed_default_parameter` -- and only two of them carry a `name` field. The
    first descendant identifier is the one shape all four share, which is why the
    schema was empty for every annotated tool before this: three tools digested
    `{}` and therefore digested *the same thing*.
    """
    if parameter.type == "identifier":
        return parameter
    named = parameter.child_by_field_name("name")
    if named is not None:
        return named
    for child in parameter.named_children:
        if child.type == "identifier":
            return child
    return None
