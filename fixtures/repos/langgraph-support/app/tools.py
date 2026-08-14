"""Four tools the graph may call.

Each declares its name, its parameters and whether it changes anything — the
three things `02` §4.2's `tool_schema` semantic projection asks for.
"""

from langchain_core.tools import tool


@tool("lookup_order")
def lookup_order(order_id: str) -> dict:
    """Read one order by id. Read-only."""
    return {"order_id": order_id}


@tool("search_knowledge_base")
def search_knowledge_base(query: str, top_k: int = 5) -> list:
    """Search the support knowledge base. Read-only."""
    return []


@tool("issue_refund")
def issue_refund(order_id: str, amount_cents: int, reason: str) -> dict:
    """Issue a refund against an order. **Writes.**"""
    return {"order_id": order_id, "refunded": amount_cents, "reason": reason}


@tool("create_ticket")
def create_ticket(summary: str, severity: str) -> dict:
    """Open a support ticket. **Writes.**"""
    return {"summary": summary, "severity": severity}
