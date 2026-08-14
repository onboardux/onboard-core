"""The support graph: four nodes, three model pins, one edge list.

The pins are the point of this file. One is dated and therefore pinned, one is a
`-latest` alias that can change under the deployment without a commit, and one is
resolved from the environment at start-up and cannot be read from this tree at
all.
"""

import os

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph

from app.prompts import ANSWER_GROUNDED, ESCALATION_TEMPLATE, TRIAGE_CLASSIFIER
from app.retrieval import search
from app.tools import lookup_order

triage_model = ChatOpenAI(model="gpt-4o-latest", temperature=0)
answer_model = ChatAnthropic(model="claude-sonnet-4-5-20250929", temperature=0, max_tokens=1024)
rerank_model = ChatOpenAI(model=os.environ["SUPPORT_RERANK_MODEL"], temperature=0)


def triage(state: dict) -> dict:
    """Classify the incoming message; reads `prompts/triage_classifier.md`."""
    return {"intent": triage_model.invoke(TRIAGE_CLASSIFIER.read_text())}


def retrieve(state: dict) -> dict:
    """Fetch grounding passages for the classified intent."""
    return {"passages": search(state["question"])}


def answer(state: dict) -> dict:
    """Draft the grounded answer; reads `prompts/answer_grounded.md`."""
    return {"draft": answer_model.invoke(ANSWER_GROUNDED.read_text())}


def escalate(state: dict) -> dict:
    """Draft the escalation note from the in-code template."""
    return {"note": answer_model.invoke(ESCALATION_TEMPLATE.format(**state))}


def build_graph() -> StateGraph:
    graph = StateGraph(dict)
    graph.add_node("triage", triage)
    graph.add_node("retrieve", retrieve)
    graph.add_node("answer", answer)
    graph.add_node("escalate", escalate)
    graph.add_edge("triage", "retrieve")
    graph.add_edge("retrieve", "answer")
    graph.add_edge("answer", "escalate")
    graph.set_entry_point("triage")
    return graph
