"""The AI pack: what it extracts, and the four things it deliberately does not.

Every test here passes the defect sentence, and the negative cases carry as much
weight as the positive ones. An extractor that finds everything it should and
also finds documentation, lookup-table constants and prose is not a better
extractor -- it fills a client's inventory with entries nobody can act on, and
each one is a permanent URI.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from adopt_map import Observation, SourceTree
from adopt_map.packs import ai


def _tree(root: Path, files: dict[str, str]) -> SourceTree:
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return SourceTree.scan(root)


def _keys(observations: Iterator[Observation]) -> list[str]:
    return ["/".join(observation.key) for observation in observations]


@pytest.mark.unit
def test_a_named_prompt_is_extracted_with_whitespace_normalized_text(tmp_path: Path) -> None:
    """*Fails when* a prompt constant is missed, or its digest input keeps the
    layout. *Matters because* v6.1 §6 fixes a prompt's attribute set as
    whitespace-normalized text, so that re-indenting or rewrapping a prompt --
    the single most common edit a formatter makes to one -- cannot manufacture
    staleness. *No other instrument catches it because* the digest tests operate
    on attribute dicts an extractor already built, and cannot see what an
    extractor chose to put in one.
    """
    tree = _tree(
        tmp_path,
        {"app/prompts.py": ('system_prompt = """You are helpful.\n\n    Answer   briefly.\n"""\n')},
    )

    found = list(ai.PromptExtractor().extract(tree))

    assert _keys(iter(found)) == ["app/prompts/system_prompt"]
    assert found[0].attributes["text"] == "You are helpful. Answer briefly."
    assert found[0].kind == "prompt"


@pytest.mark.unit
def test_a_constant_naming_an_env_var_is_not_a_prompt(tmp_path: Path) -> None:
    """*Fails when* `_PROMPT_API_KEY_ENV = "LANGSMITH_PROMPT_API_KEY"` is
    recorded as a prompt. *Matters because* reference repository #2 has exactly
    two of these, and both were extracted on the pack's first real run -- a
    prompt inventory that lists environment-variable names is one an FDE stops
    reading. *No other instrument catches it because* the observation is
    perfectly well formed: right kind, plausible key, non-empty text.
    """
    tree = _tree(
        tmp_path,
        {"app/provenance.py": '_PROMPT_WORKSPACE_ENV = "LANGSMITH_PROMPT_WORKSPACE_ID"\n'},
    )

    assert list(ai.PromptExtractor().extract(tree)) == []


@pytest.mark.unit
def test_a_prompt_assembled_at_runtime_is_skipped_rather_than_guessed(tmp_path: Path) -> None:
    """*Fails when* an f-string prompt is recorded with whatever text `unparse`
    produced. *Matters because* that text is the *template*, not the prompt: it
    contains `{variable}` placeholders the model never sees, so the digest would
    describe a string the system never sends. *No other instrument catches it
    because* an f-string is a perfectly good `ast` node and yields a plausible
    string on demand.
    """
    tree = _tree(
        tmp_path,
        {"app/p.py": 'name = "x"\nsystem_prompt = f"You are {name} and you help"\n'},
    )

    assert list(ai.PromptExtractor().extract(tree)) == []


@pytest.mark.unit
def test_a_decorated_tool_carries_its_signature_as_the_schema(tmp_path: Path) -> None:
    """*Fails when* a `@tool` function is missed or its parameters dropped.
    *Matters because* the signature **is** the schema the model is shown, so a
    renamed or retyped parameter is a changed contract even when the body is
    untouched. *No other instrument catches it because* nothing else in the
    build reads a decorator's meaning.
    """
    tree = _tree(
        tmp_path,
        {
            "app/tools.py": (
                "from langchain.tools import tool\n\n\n"
                "@tool\n"
                "async def search_docs(query: str, limit: int = 5) -> list[str]:\n"
                '    """Search."""\n'
                "    return []\n"
            )
        },
    )

    found = list(ai.ToolSchemaExtractor().extract(tree))

    assert len(found) == 1
    assert found[0].kind == "tool_schema"
    assert found[0].key == ("search_docs",)
    assert found[0].attributes["parameters"] == [
        {"name": "query", "annotation": "str"},
        {"name": "limit", "annotation": "int"},
    ]
    assert found[0].attributes["async"] is True


@pytest.mark.unit
def test_a_json_object_that_is_not_a_tool_schema_is_not_one(tmp_path: Path) -> None:
    """*Fails when* any `{"name": ..., "parameters": ...}` mapping is read as a
    tool. *Matters because* that shape is ordinary configuration -- a job
    definition, a chart spec, a fixture -- and a repository full of JSON would
    mint a `tool_schema` for each. *No other instrument catches it because* the
    positive case below accepts the same two keys, so only a negative case can
    show where the line is.
    """
    tree = _tree(
        tmp_path,
        {
            "conf/job.json": json.dumps({"name": "nightly", "parameters": {"retries": 3}}),
            "conf/tool.json": json.dumps(
                {
                    "name": "get_weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                }
            ),
        },
    )

    found = list(ai.ToolSchemaExtractor().extract(tree))

    assert _keys(iter(found)) == ["get_weather"]


@pytest.mark.unit
def test_one_model_is_one_identity_however_it_is_spelled(tmp_path: Path) -> None:
    """*Fails when* `openai:gpt-5.4-nano` and a bare `gpt-5.4-nano` become two
    identities, or land in two provider namespaces. *Matters because* they are
    one model, and two identities for one referent means two coverage answers,
    two binding targets, and a permanent split nothing later reconciles -- a URI
    is never rewritten. *No other instrument catches it because* both spellings
    are individually correct, so each looks right in isolation.
    """
    tree = _tree(
        tmp_path,
        {
            "app/a.py": 'MODEL = "openai:gpt-5.4-nano"\n',
            "app/b.py": 'FALLBACK = "gpt-5.4-nano"\n',
        },
    )

    found = list(ai.ModelPinExtractor().extract(tree))

    assert len(found) == 1
    assert found[0].key == ("gpt-5.4-nano",)
    assert found[0].namespace == "openai"
    assert found[0].attributes == {"model": "gpt-5.4-nano", "provider": "openai"}


@pytest.mark.unit
def test_a_model_named_in_prose_is_not_a_pin(tmp_path: Path) -> None:
    """*Fails when* a model name inside a sentence mints an identity. *Matters
    because* a comment reading "we migrated from gpt-4o last year" documents
    history, and recording it would put a model the system does not run into the
    inventory of what it does. *No other instrument catches it because* the
    resulting `model_pin` is indistinguishable from a real one downstream.
    """
    tree = _tree(
        tmp_path,
        {"app/notes.py": '"""We used to run gpt-4o-mini here; claude-3-opus before that."""\n'},
    )

    assert list(ai.ModelPinExtractor().extract(tree)) == []


@pytest.mark.unit
def test_an_mcp_server_is_a_retrieval_config(tmp_path: Path) -> None:
    """*Fails when* a declared MCP server is missed. *Matters because* for a
    docs agent the MCP server **is** the retrieval layer -- there is no vector
    store to find -- and an AI inventory that records no data source for a
    retrieval-augmented system has missed the half that fails quietly. *No other
    instrument catches it because* the vector-store rule looks for constructors
    this repository shape never calls.
    """
    tree = _tree(
        tmp_path,
        {
            "connectors/mcp.py": (
                "from managed_deepagents.connectors import define_mcp_servers\n\n"
                "connector = define_mcp_servers(\n"
                "    mcp_servers={\n"
                '        "langchain-docs": {"transport": "http", "url": "https://x/mcp"},\n'
                "    },\n"
                ")\n"
            )
        },
    )

    found = list(ai.RetrievalConfigExtractor().extract(tree))

    assert len(found) == 1
    assert found[0].kind == "retrieval_config"
    assert found[0].key == ("langchain-docs",)
    # The keys that were configured, never their values: a client's endpoint is
    # not an attribute of what the system is.
    assert found[0].attributes["options"] == ["transport", "url"]
    assert "https://x/mcp" not in json.dumps(found[0].attributes)


@pytest.mark.unit
def test_agent_graph_nodes_are_extracted_and_edges_are_not(tmp_path: Path) -> None:
    """*Fails when* `add_edge` calls become identities. *Matters because*
    relationship edges are explicitly out of scope for Build 1, and recording
    half a graph as though it were the whole thing is worse than recording none:
    a reader takes the list for a control flow it does not describe. *No other
    instrument catches it because* an edge extractor would produce well-formed
    `symbol` rows that look exactly like nodes.
    """
    tree = _tree(
        tmp_path,
        {
            "app/graph.py": (
                "from langgraph.graph import StateGraph\n\n"
                "builder = StateGraph(dict)\n"
                'builder.add_node("plan", plan_step)\n'
                'builder.add_node("act", act_step)\n'
                'builder.add_edge("plan", "act")\n'
            )
        },
    )

    found = list(ai.AgentGraphExtractor().extract(tree))

    assert _keys(iter(found)) == ["app/graph/plan", "app/graph/act"]
    assert all(observation.namespace == "agent_graph" for observation in found)
    assert all(observation.attributes["registration"] == "add_node" for observation in found)


@pytest.mark.unit
def test_an_agent_constructor_names_the_node_it_declares(tmp_path: Path) -> None:
    """*Fails when* `define_deep_agent(name="docs_agent")` is recorded under the
    variable it was assigned to rather than under the name it declares.
    *Matters because* the declared name is what appears in traces, dashboards
    and the deployment -- it is the name a person would search for -- while the
    local variable is an implementation detail of one module. *No other
    instrument catches it because* both produce a valid identity, and the wrong
    one is only wrong when somebody tries to find it.
    """
    tree = _tree(
        tmp_path,
        {
            "agent.py": (
                "from managed_deepagents import define_deep_agent\n\n"
                'agent = define_deep_agent(name="docs_agent", model="openai:gpt-5.4-nano")\n'
            )
        },
    )

    found = list(ai.AgentGraphExtractor().extract(tree))

    assert _keys(iter(found)) == ["agent/docs_agent"]
    assert found[0].attributes["registration"] == "agent"
