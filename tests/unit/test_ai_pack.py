"""The `ai` pack's six extractors, over the fixture they ship with -- `05` S1.5.

**One table per question, not one file per extractor.** The eight `02` §7
obligations are the conformance suite's (`tests/conformance/`), and repeating
them here would be the pyramid duplicate `03` §7's test budget bans. What is left
is what only these extractors can get wrong: **which key they mint** (`02` §3.1)
and **which of three stabilities a pin carries** (`01` F8.8).

*Defect sentence.* Fails when an `ai` extractor stops recovering a referent the
fixture declares, mints a key outside `02` §3.1's convention, or mis-bands a
model pin; matters because a key outside the convention forks the identity
against the next extractor that describes the same referent, and a mis-banded pin
is the floating-pin callout going quiet on a deployment whose behaviour can
change without a commit; no other instrument catches it, because a forked key is
a perfectly valid URI and a mis-banded pin is a perfectly valid fact.
"""

from pathlib import Path

import pytest
from adopt_extractors_ai import pack
from adopt_extractors_ai.evalsets import NAMESPACE as EVALSET_NAMESPACE
from adopt_extractors_common.config import ConfigExtractor
from adopt_extractors_common.stub_tree import StubTreeExtractor
from adopt_map.documents import declares_owned_document
from adopt_map.schemas import SurfaceFact

from tests.build1_conftest import context_for

pytestmark = pytest.mark.unit

_TREE = Path("fixtures/repos/langgraph-support")


def _facts() -> dict[str, list[SurfaceFact]]:
    """Every `ai` fact over the fixture, by extractor id. Built once per test
    rather than at import, so a failure names the test that provoked it."""
    ctx = context_for(_TREE, archetype="ai")
    return {extractor.manifest().id: list(extractor.extract(ctx)) for extractor in pack()}


def _keys(facts: list[SurfaceFact]) -> set[tuple[str, str | None, str]]:
    return {(fact.identity_kind, fact.namespace, fact.local_key) for fact in facts}


#: `(extractor id, kind, namespace, local_key)` the fixture declares. Derived
#: from the fixture's specification -- its README table and the files it holds --
#: never from a previous run's output.
_EXPECTED: tuple[tuple[str, str, str, str], ...] = (
    # `01` F8.2's three prompt locations, and `02` §3.1's three namespaces.
    ("ai.prompts", "prompt", "file", "prompts/answer_grounded.md"),
    ("ai.prompts", "prompt", "file", "prompts/triage_classifier.md"),
    ("ai.prompts", "prompt", "file", "app/prompts.py#ESCALATION_TEMPLATE"),
    ("ai.prompts", "prompt", "console", "support-greeting"),
    ("ai.prompts", "prompt", "db", "faq_answer"),
    # `<model-id>@<call-site>`, per `02` §3.1.
    ("ai.model_pins", "model_pin", "openai", "gpt-4o-latest@triage_model"),
    ("ai.model_pins", "model_pin", "anthropic", "claude-sonnet-4-5-20250929@answer_model"),
    ("ai.model_pins", "model_pin", "openai", "${SUPPORT_RERANK_MODEL}@rerank_model"),
    # Tool name, one identity per tool.
    ("ai.tools", "tool_schema", "langgraph", "lookup_order"),
    ("ai.tools", "tool_schema", "langgraph", "search_knowledge_base"),
    ("ai.tools", "tool_schema", "langgraph", "issue_refund"),
    ("ai.tools", "tool_schema", "langgraph", "create_ticket"),
    # `<index>.<parameter-path>`, one identity per parameter.
    ("ai.retrieval", "retrieval_config", "pgvector", "orders_kb.top_k"),
    ("ai.retrieval", "retrieval_config", "pgvector", "orders_kb.embedding_model"),
    ("ai.retrieval", "retrieval_config", "pgvector", "orders_kb.chunk_size"),
    ("ai.retrieval", "retrieval_config", "pgvector", "orders_kb.chunk_overlap"),
    ("ai.retrieval", "retrieval_config", "pgvector", "orders_kb.rerank_model"),
    ("ai.retrieval", "retrieval_config", "pgvector", "orders_kb.display_label"),
    # Cases and thresholds, both behaviour-bearing.
    ("ai.evalsets", "config_key", EVALSET_NAMESPACE, "support_quality.cases.refund_policy"),
    ("ai.evalsets", "config_key", EVALSET_NAMESPACE, "support_quality.cases.order_status"),
    ("ai.evalsets", "config_key", EVALSET_NAMESPACE, "support_quality.cases.out_of_scope"),
    ("ai.evalsets", "config_key", EVALSET_NAMESPACE, "support_quality.thresholds.faithfulness"),
    ("ai.evalsets", "config_key", EVALSET_NAMESPACE, "support_quality.thresholds.refusal_rate"),
    # Graph nodes are `symbol`s keyed like every other Python declaration.
    ("ai.graph", "symbol", "python", "app.graph.triage"),
    ("ai.graph", "symbol", "python", "app.graph.retrieve"),
    ("ai.graph", "symbol", "python", "app.graph.answer"),
    ("ai.graph", "symbol", "python", "app.graph.escalate"),
)


@pytest.mark.parametrize(
    ("extractor_id", "kind", "namespace", "local_key"),
    _EXPECTED,
    ids=[f"{row[0]}:{row[3]}" for row in _EXPECTED],
)
def test_the_pack_mints_the_key_the_convention_fixes(
    extractor_id: str, kind: str, namespace: str, local_key: str
) -> None:
    """One row per referent the fixture declares, keyed as `02` §3.1 fixes it."""
    facts = _facts()[extractor_id]
    assert (kind, namespace, local_key) in _keys(facts), (
        f"{extractor_id} did not mint {kind}/{namespace}/{local_key}; it minted "
        f"{sorted(key for _k, _n, key in _keys(facts))}"
    )


_STABILITY: tuple[tuple[str, str], ...] = (
    ("gpt-4o-latest@triage_model", "floating"),
    ("claude-sonnet-4-5-20250929@answer_model", "pinned"),
    ("${SUPPORT_RERANK_MODEL}@rerank_model", "unknown"),
)


@pytest.mark.parametrize(("local_key", "stability"), _STABILITY, ids=[row[1] for row in _STABILITY])
def test_pin_stability_bands_the_three_cases(local_key: str, stability: str) -> None:
    """`01` F8.8's three states, each reachable from the fixture.

    The floating case is the one with a consequence: it is the only finding this
    pack gives its own `surface.md` callout, and a pin mis-banded as `pinned`
    removes the callout without removing the exposure.
    """
    pins = {fact.local_key: fact for fact in _facts()["ai.model_pins"]}
    assert pins[local_key].attributes["pin_stability"] == stability


def test_a_runtime_resolved_pin_is_outside_vcs_but_not_opaque() -> None:
    """`01` F8.6 and F8.7 are different questions.

    The model id cannot be read from the tree, so the pin is outside version
    control -- but the provider, the temperature and the call site are all in the
    file, so the semantic digest still covers them. Marking it opaque would null
    that digest and silently swallow a temperature change, which is B1-CR-44's
    failure wearing F8.7's clothes.
    """
    pins = {fact.local_key: fact for fact in _facts()["ai.model_pins"]}
    runtime = pins["${SUPPORT_RERANK_MODEL}@rerank_model"]
    assert runtime.outside_vcs is True
    assert runtime.opaque is False
    assert runtime.attributes["temperature"] == 0.0


def test_a_console_prompt_carries_an_identity_and_no_content() -> None:
    """`01` F8.7: unreadable content produces the identity, the flags and nothing
    else. **The empty attribute set is the assertion** -- a body invented from the
    id would digest equal to itself on every later run and report that a prompt
    nobody can see never changes."""
    prompts = {fact.local_key: fact for fact in _facts()["ai.prompts"]}
    for key in ("support-greeting", "faq_answer"):
        assert prompts[key].opaque is True
        assert prompts[key].outside_vcs is True
        assert prompts[key].attributes == {}


def test_two_tools_with_different_parameters_digest_differently() -> None:
    """The parameter schema is inside the digest, so a signature change is a
    change. Three tools digesting identically is what an empty schema looks
    like, and it is how this extractor first shipped."""
    digests = {
        fact.local_key: fact.attributes["parameter_schema_digest"] for fact in _facts()["ai.tools"]
    }
    assert len(set(digests.values())) == len(digests)


def test_a_side_effect_flag_is_never_inferred_from_prose() -> None:
    """`02` §4.2 says *declared*. The fixture's tools say "**Writes.**" in their
    docstrings and declare no flag, so the flag stays unset: putting a judgement
    nobody made into the store is the invention `01` §1.6 forbids."""
    for fact in _facts()["ai.tools"]:
        assert fact.attributes["has_side_effects"] is None


def test_graph_edges_are_directed_calls_relations() -> None:
    """`02` §5.2: the framework creates no inverse. `triage -> retrieve` is what
    the edge list says, and only that."""
    nodes = {fact.local_key: fact for fact in _facts()["ai.graph"]}
    triage = nodes["app.graph.triage"]
    assert [(r.predicate, r.target_local_key) for r in triage.relations] == [
        ("calls", "app.graph.retrieve")
    ]
    assert nodes["app.graph.escalate"].relations == []


def test_graph_nodes_agree_with_stub_tree_byte_for_byte() -> None:
    """`01` F2.3: two extractors describing one referent mint one URI.

    `ai.graph` and `common.stub_tree` both name a Python declaration, and the
    fixture's four node functions are declared in the same file they are wired
    in. Agreeing keys means the writer receives two observations of one identity
    and merges them (B1-CR-68); disagreeing keys would fork the referent and
    double every symbol count on an AI tree -- which no other test would notice,
    because both URIs would be valid.
    """
    ctx = context_for(_TREE, archetype="ai")
    declarations = _keys(list(StubTreeExtractor().extract(ctx)))
    for fact in _facts()["ai.graph"]:
        assert (fact.identity_kind, fact.namespace, fact.local_key) in declarations


_OWNED: tuple[tuple[str, str], ...] = (
    ("config/retrieval.yaml", "ai.retrieval"),
    ("evals/support_quality.yaml", "ai.evalsets"),
)


@pytest.mark.parametrize(("path", "owner"), _OWNED, ids=[row[1] for row in _OWNED])
def test_common_config_defers_to_the_extractor_that_owns_a_document(path: str, owner: str) -> None:
    """B1-CR-67's rule, one sprint on.

    `common.config` reads every YAML file in a tree, so without the ownership
    rule the retrieval document mints `config_key/yaml/retrieval.top_k` **beside**
    `retrieval_config/pgvector/orders_kb.top_k`: two identities, two kinds, one
    setting, double-counted in every coverage figure. Asserted from both ends --
    the document declares an owner, and `common.config` emits nothing from it.
    """
    text = (_TREE / path).read_text(encoding="utf-8")
    assert declares_owned_document(text) == owner

    ctx = context_for(_TREE, archetype="ai")
    claimed = [fact for fact in ConfigExtractor().extract(ctx) if fact.namespace in {"yaml", "yml"}]
    assert claimed == []
