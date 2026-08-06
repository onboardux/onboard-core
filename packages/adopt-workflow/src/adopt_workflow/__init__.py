"""The Workflow facade, the in-process test backend, the purity checker.

Contracts §10.2 · implementation spec §4.14 · PRD F14.

Invariants this package carries: **no DBOS symbol appears here** -- the only
importer in the programme is `plane_workflow.dbos_backend`, in the closed
repository -- workflow bodies are pure, checked at decoration time by
`assert_pure` and across the tree by the `workflow-body-purity` contract, and
**no Build 0 OSS-side command uses durable workflows**, so the OSS CLI never
requires Postgres.
"""

from adopt_workflow.api import (
    TERMINAL_STATUSES,
    Backoff,
    RetryPolicy,
    StepContext,
    WorkflowClient,
    WorkflowContext,
    WorkflowHandle,
    WorkflowStatus,
    backoff_delays_ms,
    validate_idempotency_key,
)
from adopt_workflow.decorators import (
    REGISTRY,
    ScheduledDefinition,
    StepDefinition,
    WorkflowDefinition,
    clear_registry,
    resolve,
    scheduled,
    step,
    workflow,
)
from adopt_workflow.inproc import InProcessWorkflowClient, Journal
from adopt_workflow.purity import assert_pure, find_impure_workflow_bodies

__all__ = [
    "REGISTRY",
    "TERMINAL_STATUSES",
    "Backoff",
    "InProcessWorkflowClient",
    "Journal",
    "RetryPolicy",
    "ScheduledDefinition",
    "StepContext",
    "StepDefinition",
    "WorkflowClient",
    "WorkflowContext",
    "WorkflowDefinition",
    "WorkflowHandle",
    "WorkflowStatus",
    "assert_pure",
    "backoff_delays_ms",
    "clear_registry",
    "find_impure_workflow_bodies",
    "resolve",
    "scheduled",
    "step",
    "validate_idempotency_key",
    "workflow",
]
