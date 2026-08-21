"""The AI pack -- what an AI deployment is made of, and none of it inferred.

Five extractors, one per referent v6.1 §6 names for Build 1: prompt files and
named prompts, tool/function schemas, pinned model identifiers, retrieval and
data-source configuration, and agent graph nodes.

**No model is called to find any of it**, which is the point. The archetype most
likely to invite an "ask a model what this agent does" shortcut is the one where
the deterministic answer is cheapest: a prompt is a string constant, a tool is a
decorated function, a model pin is a string literal, and a graph node is an
`add_node` call. Every one of those is structure, and `ast` reads structure
exactly.

**Two boundaries are stated here rather than discovered later.**

*A prompt built at runtime is not extracted.* `ChatPromptTemplate.from_messages`
with variables, an f-string assembled from three fragments, a prompt pulled from
a hub at import time -- none of these has text this pack can read without
executing the client's code, which it never does. What is extracted is the
constant text, and what is not shows up as a named miss on the curated
`--check-expected` list rather than as a silently thin map.

*A model pin is a whole string literal, never a substring.* `"gpt-5.4-nano"` is
a pin; a sentence in a docstring mentioning gpt-5.4-nano is prose. Matching
substrings would mint identities out of documentation and changelogs, which is
the same trap the generic pack's environment-variable pattern is narrow to
avoid.
"""

import ast
import json
import re
from collections.abc import Iterator, Mapping
from typing import Final

from adopt_map.keys import AGENT_GRAPH_NAMESPACE, model_provider_namespace, module_key, path_key
from adopt_map.observation import Observation, Span
from adopt_map.tree import SourceTree, TreeFile

__all__ = [
    "AgentGraphExtractor",
    "ModelPinExtractor",
    "PromptExtractor",
    "RetrievalConfigExtractor",
    "ToolSchemaExtractor",
]


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


def _whole_file(entry: TreeFile) -> Span:
    return Span(path=entry.path, start_line=1, end_line=1)


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _string_arg(call: ast.Call, index: int = 0) -> str | None:
    if len(call.args) <= index:
        return None
    argument = call.args[index]
    return (
        argument.value
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
        else None
    )


def _string_keyword(call: ast.Call, name: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg != name:
            continue
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    return None


def _option_names(call: ast.Call) -> list[str]:
    """Keyword argument names, sorted. What was configured, never its value.

    A vector store's `url` or an API key's variable name is client
    configuration; the *fact* that a connection was configured is the identity.
    Recording which knobs were turned says what the system does without carrying
    a client's endpoint into our digest input.
    """
    return sorted(keyword.arg for keyword in call.keywords if keyword.arg is not None)


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace and strip -- v6.1 §6's digest rule for a prompt.

    A re-indented prompt, a trailing newline added by an editor, and a line
    rewrapped by a formatter are the same prompt. Anything finer would make
    every prompt in the repository stale the first time someone ran a formatter,
    which is the false staleness H5 exists to remove.
    """
    return " ".join(text.split())


def _reads_as_prose(text: str) -> bool:
    """Whether a string is addressed to a reader rather than to a lookup table.

    One whitespace character is the whole test. It separates
    `"LANGSMITH_PROMPT_WORKSPACE_ID"` from a prompt without introducing a
    minimum length, which would be a tunable and therefore a number someone
    would eventually move until the map said what they wanted.
    """
    return any(character.isspace() for character in text.strip())


def _target_name(node: ast.expr) -> str | None:
    """The readable name of whatever an argument refers to."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _called_name(node)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class PromptExtractor:
    """Named prompts in code, and the files that are nothing but a prompt.

    A prompt is the most-changed and least-reviewed artefact in an AI system:
    it carries behaviour, it is edited without a code review, and nothing in a
    conventional inventory records that it exists. Both shapes are here because
    both are how real deployments hold one -- a `docs_agent_prompt = "..."`
    constant in a module, and a `prompts/system.md` read at start-up.
    """

    name = "ai.prompts"
    version = "1"

    #: A module-level assignment whose *name* declares it a prompt. Names, not
    #: contents: a heuristic over the string's text would call every long
    #: docstring a prompt, and an inventory that lists every string is not an
    #: inventory.
    _PROMPT_NAME: Final[re.Pattern[str]] = re.compile(
        r"(^|_)(prompt|prompts|instruction|instructions|system_message|template)($|_)",
        re.IGNORECASE,
    )

    #: Suffixes a prompt file is written in. `.py` is deliberately absent -- a
    #: Python file under `prompts/` is read for its named assignments instead,
    #: and reading it both ways would record the same prompt twice.
    _PROMPT_FILE_SUFFIXES: Final[frozenset[str]] = frozenset(
        {".md", ".txt", ".prompt", ".jinja", ".jinja2", ".j2", ".tmpl", ".mustache"}
    )

    _PROMPT_DIRS: Final[frozenset[str]] = frozenset({"prompt", "prompts"})

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.iter_suffix(".py"):
            module = _parse(tree, entry)
            if module is None:
                continue
            yield from self._named(entry, module)
        for entry in tree.files:
            if not self._is_prompt_file(entry):
                continue
            text = tree.text(entry)
            if text is None:
                continue
            yield Observation(
                kind="prompt",
                key=path_key(entry.path),
                namespace=None,
                attributes={"name": entry.name, "text": _normalize_whitespace(text)},
                span=_whole_file(entry),
                note="a file whose whole content is a prompt",
            )

    def _is_prompt_file(self, entry: TreeFile) -> bool:
        if entry.suffix not in self._PROMPT_FILE_SUFFIXES:
            return False
        parts = entry.path.split("/")
        in_prompt_dir = any(part.lower() in self._PROMPT_DIRS for part in parts[:-1])
        named_prompt = "prompt" in entry.name.lower() or entry.name.lower() == "instructions.md"
        return in_prompt_dir or named_prompt

    def _named(self, entry: TreeFile, module: ast.Module) -> Iterator[Observation]:
        # Module body only, not `ast.walk`: a prompt assembled inside a function
        # is a local variable, and the prompts a system *has* are the ones
        # something else can import.
        for statement in module.body:
            target, value = _module_assignment(statement)
            if target is None or value is None:
                continue
            if not self._PROMPT_NAME.search(target):
                continue
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                # An f-string, a `.format()` call or a template object: the text
                # is not decidable here. See the module docstring.
                continue
            if not _reads_as_prose(value.value):
                # `_PROMPT_WORKSPACE_ENV = "LANGSMITH_PROMPT_WORKSPACE_ID"` is an
                # environment-variable *name* that happens to contain the word,
                # and reference repository #2 has two of them. A prompt is text
                # addressed to a model; a single unbroken token never is. The
                # rule is structural rather than a length threshold, so there is
                # no number here for anyone to tune into a different answer.
                continue
            yield Observation(
                kind="prompt",
                key=module_key(entry.path, target),
                namespace=None,
                attributes={"name": target, "text": _normalize_whitespace(value.value)},
                span=_span(entry, statement),
                note="a named prompt constant",
            )


class ToolSchemaExtractor:
    """Tools a model can call: decorated functions, and declared JSON schemas.

    The digest input is the schema, because the schema is what the model sees.
    A renamed parameter is a different tool to a model even when the body is
    unchanged, and a widened type is a different contract -- both of which a
    reviewer needs told and neither of which a file hash would separate from a
    reformatting.
    """

    name = "ai.tool_schemas"
    version = "1"

    #: Decorators that turn a function into a model-callable tool. Matched on
    #: the final attribute, so `@tool`, `@tool(...)`, `@langchain.tools.tool`
    #: and `@mcp.tool()` are all the same declaration.
    _TOOL_DECORATORS: Final[frozenset[str]] = frozenset(
        {"tool", "tools", "function_tool", "ai_function", "openai_function"}
    )

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.iter_suffix(".py"):
            module = _parse(tree, entry)
            if module is None:
                continue
            yield from self._decorated(entry, module)
        for entry in tree.iter_suffix(".json"):
            yield from self._declared(tree, entry)

    def _decorated(self, entry: TreeFile, module: ast.Module) -> Iterator[Observation]:
        for node in ast.walk(module):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not any(self._is_tool_decorator(item) for item in node.decorator_list):
                continue
            yield Observation(
                kind="tool_schema",
                # One segment: a tool name is what the model is told, and it is
                # unique within the tool list it is offered in. Deriving it from
                # the module path would give the same tool two identities the
                # day it is moved between files.
                key=(node.name,),
                namespace=None,
                attributes={
                    "name": node.name,
                    "parameters": _signature(node),
                    "async": isinstance(node, ast.AsyncFunctionDef),
                },
                span=_span(entry, node),
                note="a decorated tool function",
            )

    def _is_tool_decorator(self, decorator: ast.expr) -> bool:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        named = (
            target.id
            if isinstance(target, ast.Name)
            else target.attr
            if isinstance(target, ast.Attribute)
            else None
        )
        return named in self._TOOL_DECORATORS

    def _declared(self, tree: SourceTree, entry: TreeFile) -> Iterator[Observation]:
        text = tree.text(entry)
        if text is None:
            return
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            return
        candidates = document if isinstance(document, list) else [document]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            declaration = _tool_declaration(candidate)
            if declaration is None:
                continue
            name, schema = declaration
            yield Observation(
                kind="tool_schema",
                key=(name,),
                namespace=None,
                # The schema whole, canonicalized by the digest's own sorted-key
                # rendering. A reordered `properties` block is the same schema
                # and must produce the same digest.
                attributes={"name": name, "schema": schema},
                span=_whole_file(entry),
                note="a declared tool schema",
            )


class ModelPinExtractor:
    """Pinned model identifiers -- which model this system actually runs on.

    The first question asked of an AI deployment at handover, and the one a
    conventional inventory cannot answer at all. A pin is recorded once per
    model whatever spelling reached it: `openai:gpt-5.4-nano` and a bare
    `gpt-5.4-nano` in another file are one referent, because they are one model.
    """

    name = "ai.model_pins"
    version = "1"

    #: Where a pin can live. Prose formats are absent on purpose: a README
    #: naming a model documents an intention, and minting an identity from it
    #: would put the documentation's opinion into the system's inventory.
    _SUFFIXES: Final[frozenset[str]] = frozenset(
        {".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml"}
    )

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        seen: set[str] = set()
        for entry in tree.files:
            if entry.suffix not in self._SUFFIXES:
                continue
            text = tree.text(entry)
            if text is None:
                continue
            for match in _MODEL_LITERAL.finditer(text):
                provider, model = _split_pin(match.group("pin"))
                if model in seen:
                    continue
                seen.add(model)
                # The *normalized* provider in both places. `google_genai:gemini`
                # and a bare `gemini-` reach here spelled differently and are one
                # model on one vendor; letting the raw spelling into the digest
                # input would make which file was read first decide the digest.
                namespace = model_provider_namespace(provider)
                yield Observation(
                    kind="model_pin",
                    key=(model,),
                    namespace=namespace,
                    attributes={"model": model, "provider": namespace or "unknown"},
                    span=Span(
                        path=entry.path,
                        start_line=_line_of(text, match.start()),
                        end_line=_line_of(text, match.end()),
                    ),
                )


class RetrievalConfigExtractor:
    """Where the system gets its context: vector stores, retrievers, MCP servers.

    Retrieval is the half of an AI system that fails quietly. A vector store
    pointed at a stale index answers every question confidently and wrongly, and
    nothing in the code changes when it happens -- so *that the configuration
    exists* is what an inventory has to record, ready for a probe to be bound to
    it in a later build.
    """

    name = "ai.retrieval"
    version = "1"

    #: Vector stores and index constructors, matched on the called name.
    _STORES: Final[frozenset[str]] = frozenset(
        {
            "AstraDBVectorStore",
            "Chroma",
            "ElasticsearchStore",
            "FAISS",
            "LanceDB",
            "Milvus",
            "MongoDBAtlasVectorSearch",
            "PGVector",
            "Pinecone",
            "PineconeVectorStore",
            "Qdrant",
            "QdrantVectorStore",
            "RedisVectorStore",
            "SupabaseVectorStore",
            "VectorStoreIndex",
            "Weaviate",
            "WeaviateVectorStore",
        }
    )

    #: Calls that turn something into a retriever.
    _RETRIEVERS: Final[frozenset[str]] = frozenset({"as_retriever", "from_documents", "from_texts"})

    #: Calls that declare MCP servers -- a data source by another name.
    _MCP_FACTORIES: Final[frozenset[str]] = frozenset(
        {"define_mcp_servers", "MultiServerMCPClient"}
    )

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.iter_suffix(".py"):
            module = _parse(tree, entry)
            if module is None:
                continue
            for node in ast.walk(module):
                if not isinstance(node, ast.Call):
                    continue
                yield from self._store(entry, node)
                yield from self._mcp_servers(entry, node)
        for entry in tree.iter_suffix(".json"):
            yield from self._mcp_json(tree, entry)

    def _store(self, entry: TreeFile, call: ast.Call) -> Iterator[Observation]:
        called = _called_name(call)
        if called is None:
            return
        if called in self._STORES:
            provider = called
        elif called in self._RETRIEVERS:
            # `Chroma.from_documents(...)` names the store on the left of the
            # dot; a bare `.as_retriever()` on a variable does not, and records
            # the call site rather than guessing which store it belongs to.
            owner = call.func.value if isinstance(call.func, ast.Attribute) else None
            provider = (_target_name(owner) if owner is not None else None) or called
        else:
            return
        yield Observation(
            kind="retrieval_config",
            key=module_key(entry.path, provider),
            namespace=None,
            attributes={"provider": provider, "options": _option_names(call)},
            span=_span(entry, call),
            note=f"{called}() configures retrieval",
        )

    def _mcp_servers(self, entry: TreeFile, call: ast.Call) -> Iterator[Observation]:
        if _called_name(call) not in self._MCP_FACTORIES:
            return
        for keyword in call.keywords:
            if keyword.arg not in {"mcp_servers", "connections", "servers"}:
                continue
            if not isinstance(keyword.value, ast.Dict):
                continue
            for name_node, config in zip(keyword.value.keys, keyword.value.values, strict=True):
                if name_node is None or not isinstance(name_node, ast.Constant):
                    continue
                if not isinstance(name_node.value, str):
                    continue
                yield Observation(
                    kind="retrieval_config",
                    key=(name_node.value,),
                    namespace=None,
                    attributes={
                        "provider": "mcp",
                        "server": name_node.value,
                        "options": _dict_keys(config),
                    },
                    span=_span(entry, name_node),
                    note="an MCP server declaration",
                )

    def _mcp_json(self, tree: SourceTree, entry: TreeFile) -> Iterator[Observation]:
        text = tree.text(entry)
        if text is None:
            return
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(document, dict):
            return
        servers = document.get("mcpServers")
        if not isinstance(servers, dict):
            return
        for name, config in sorted(servers.items()):
            yield Observation(
                kind="retrieval_config",
                key=(str(name),),
                namespace=None,
                attributes={
                    "provider": "mcp",
                    "server": str(name),
                    "options": sorted(config) if isinstance(config, dict) else [],
                },
                span=_whole_file(entry),
                note="an MCP server declaration",
            )


class AgentGraphExtractor:
    """Agent graph nodes -- the steps an agent can take.

    **Nodes, never edges.** Relationship edges are explicitly deferred for
    Build 1, and recording half of a graph as if it were the whole thing is
    worse than recording none of it: a reader would take the node list for a
    control flow it does not describe.
    """

    name = "ai.agent_graph"
    version = "1"

    #: Calls that construct an agent. The agent itself is a node in the sense
    #: an FDE means the word: a named thing that runs and can be pointed at.
    _AGENT_FACTORIES: Final[frozenset[str]] = frozenset(
        {
            "AgentExecutor",
            "create_agent",
            "create_react_agent",
            "create_tool_calling_agent",
            "define_deep_agent",
            "initialize_agent",
        }
    )

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.iter_suffix(".py"):
            module = _parse(tree, entry)
            if module is None:
                continue
            for node in ast.walk(module):
                if not isinstance(node, ast.Call):
                    continue
                yield from self._add_node(entry, node)
            yield from self._agents(entry, module)

    def _add_node(self, entry: TreeFile, call: ast.Call) -> Iterator[Observation]:
        if _called_name(call) != "add_node" or not call.args:
            return
        # `add_node("plan", plan_fn)` names the node; `add_node(plan_fn)` takes
        # the function's own name, which is what LangGraph does at runtime.
        first = _string_arg(call)
        target = _target_name(call.args[1]) if len(call.args) > 1 else None
        node_name = first or _target_name(call.args[0])
        if node_name is None:
            return
        yield Observation(
            kind="symbol",
            key=module_key(entry.path, node_name),
            namespace=AGENT_GRAPH_NAMESPACE,
            attributes={
                "node": node_name,
                "target": target or node_name,
                "registration": "add_node",
            },
            span=_span(entry, call),
        )

    def _agents(self, entry: TreeFile, module: ast.Module) -> Iterator[Observation]:
        for statement in module.body:
            target, value = _module_assignment(statement)
            if target is None or not isinstance(value, ast.Call):
                continue
            called = _called_name(value)
            if called not in self._AGENT_FACTORIES:
                continue
            node_name = _string_keyword(value, "name") or target
            yield Observation(
                kind="symbol",
                key=module_key(entry.path, node_name),
                namespace=AGENT_GRAPH_NAMESPACE,
                attributes={"node": node_name, "target": called, "registration": "agent"},
                span=_span(entry, statement),
                note=f"{called}() constructs an agent",
            )


def _module_assignment(statement: ast.stmt) -> tuple[str | None, ast.expr | None]:
    """`name = value` or `name: T = value` at module level, else `(None, None)`."""
    if isinstance(statement, ast.Assign):
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            return None, None
        return statement.targets[0].id, statement.value
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return statement.target.id, statement.value
    return None, None


def _signature(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, str]]:
    """Parameter names and their annotations, as the schema a model is shown.

    Annotations go through `ast.unparse`, which **canonicalizes** them:
    `dict[str,  int]` and `dict[str, int]` unparse identically, so reformatting
    a signature cannot move the digest while widening a type does. `self` and
    `cls` are dropped -- they are dispatch, not schema.
    """
    arguments = function.args
    return [
        {
            "name": argument.arg,
            "annotation": ast.unparse(argument.annotation)
            if argument.annotation is not None
            else "",
        }
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
        if argument.arg not in {"self", "cls"}
    ]


def _tool_declaration(candidate: Mapping[str, object]) -> tuple[str, object] | None:
    """`(name, schema)` if this mapping is an OpenAI- or Anthropic-shaped tool.

    Both vendors' wire formats and the bare `{"name", "parameters"}` form are
    accepted, because a repository holds whichever one its provider wanted and
    an inventory that recognized only one would be an inventory of our
    preferences.
    """
    inner = candidate.get("function")
    if candidate.get("type") == "function" and isinstance(inner, dict):
        wrapped = inner.get("name")
        if isinstance(wrapped, str) and wrapped:
            return wrapped, inner.get("parameters", {})
    name = candidate.get("name")
    if not isinstance(name, str) or not name:
        return None
    for field in ("parameters", "input_schema", "inputSchema"):
        schema = candidate.get(field)
        # A JSON-Schema object, not merely any dict: `{"name": "x",
        # "parameters": {"a": 1}}` in a config file is not a tool declaration,
        # and treating it as one would fill the map with configuration blocks.
        if isinstance(schema, dict) and ("properties" in schema or schema.get("type") == "object"):
            return name, schema
    return None


def _dict_keys(node: ast.expr) -> list[str]:
    """The literal string keys of an AST dict, sorted. Never its values."""
    if not isinstance(node, ast.Dict):
        return []
    return sorted(
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    )


def _split_pin(pin: str) -> tuple[str | None, str]:
    """`('openai', 'gpt-5.4-nano')` from either spelling of the same pin."""
    provider, separator, model = pin.partition(":")
    if separator:
        return provider, model
    return _PROVIDER_OF_FAMILY.get(_family(pin)), pin


def _family(model: str) -> str:
    lowered = model.lower()
    for family in _PROVIDER_OF_FAMILY:
        if lowered.startswith(family):
            return family
    return ""


def _line_of(text: str, index: int) -> int:
    """1-based line number of a character offset."""
    return text.count("\n", 0, index) + 1


#: Which provider serves a bare model family. Only families whose names are
#: unambiguous across vendors are listed: a bare `llama-3` is served by a dozen
#: hosts, and naming one would be a guess recorded as a fact.
_PROVIDER_OF_FAMILY: Final[dict[str, str]] = {
    "gpt-": "openai",
    "text-embedding-": "openai",
    "claude-": "anthropic",
    "gemini-": "google",
    "mistral-": "mistral",
    "deepseek-": "deepseek",
}

#: A model pin **is the whole string literal**. The literal is anchored at both
#: quotes, so a model name inside a sentence is prose and stays prose.
_MODEL_LITERAL: Final[re.Pattern[str]] = re.compile(
    r"""["'](?P<pin>"""
    r"""(?:(?:openai|anthropic|google_genai|google|azure_openai|azure|bedrock|groq"""
    r"""|mistralai|ollama|together|fireworks|cohere|baseten|vertexai|xai):)?"""
    r"""(?:gpt-|o[134]-|claude-|gemini-|text-embedding-|mistral-|deepseek-|llama-?\d)"""
    r"""[A-Za-z0-9][A-Za-z0-9._-]*"""
    r""")["']""",
)
