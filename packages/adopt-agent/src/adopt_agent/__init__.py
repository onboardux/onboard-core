"""The single model seam: budget, tracing, idempotency, adapters.

Contracts §10.1 · AI spec §1-§8 · implementation spec §4.13. Implemented at S7.

**Every model call in either repository passes through `Runner.run`.** The
`no-provider-sdk` import contract enforces the other half -- that provider
clients live only under `adapters/` -- so the rule survives contributors who
never read the AI spec.

**Nothing here imports an adapter eagerly**, and that is load-bearing twice
over: `adopt_store` depends on this package for the runtime-annex port, so an
eager import would drag a provider client into the store and break
`no-provider-sdk` correctly; and offline mode must refuse *before* the adapter
module loads, which is what makes "no socket opened" provable rather than
argued.

Invariants carried from S0 and still true: provider clients are imported only in
`adapters/`, budget logic lives only in `budget.py`, traces record digests and
never payloads, and no model identifier is hard-coded anywhere -- including as a
default, and including in this package's only data file, `prices.json`, whose
model names are price-table keys and select nothing.
"""

from adopt_agent.annex import AgentRunRecord, AnnexRecords
from adopt_agent.api import (
    Adapter,
    AdapterInfo,
    AdapterKind,
    AdapterResponse,
    AgentRequest,
    AgentResult,
    AgentRunner,
    AgentStatus,
    Artifact,
    Budget,
    Cost,
    ToolCall,
    ToolSpec,
    Trace,
    TraceStep,
)
from adopt_agent.budget import BudgetVerdict, Meter
from adopt_agent.pricing import ModelPrice, cost_usd, price_for, stale_rows
from adopt_agent.runner import Runner
from adopt_agent.schema_check import SchemaViolation, UnsupportedSchema, validate_against_schema
from adopt_agent.skills import LoadedSkill, load_skill

__all__ = [
    "Adapter",
    "AdapterInfo",
    "AdapterKind",
    "AdapterResponse",
    "AgentRequest",
    "AgentResult",
    "AgentRunRecord",
    "AgentRunner",
    "AgentStatus",
    "AnnexRecords",
    "Artifact",
    "Budget",
    "BudgetVerdict",
    "Cost",
    "LoadedSkill",
    "Meter",
    "ModelPrice",
    "Runner",
    "SchemaViolation",
    "ToolCall",
    "ToolSpec",
    "Trace",
    "TraceStep",
    "UnsupportedSchema",
    "cost_usd",
    "load_skill",
    "price_for",
    "stale_rows",
    "validate_against_schema",
]
