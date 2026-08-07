"""The runtime annex port: contracts §12, owner-ratified as CR-08 on 2026-08-06.

**What the annex is for, in one sentence.** PRD F13.5 says a replay returns the
recorded result with **zero** provider calls, and a promise like that needs
somewhere durable to look. `agent_run` is that place.

**Why a port rather than a store import.** `no-raw-sqlite` names `adopt_agent` a
source module and follows indirect chains, so this package may not depend on
`adopt_store` at all -- reaching the driver through a helper is exactly what the
contract catches. So the seam declares the protocol and is handed a realization
structurally, the pattern `adopt_scope.ScopeRecords` set and CR-34 and CR-37
extended to coverage, freshness and export. The realization lives in
`adopt_store.annex`, which is the package permitted to hold a dialect.

**Why the annex is not in the canonical manifest**, restated here because it is
the question a reader of this file will have: contracts §12 and CR-08. Extending
the canonical DDL would put agent audit inside `schema_version` and therefore
inside the export -- and a handover bundle would then carry a record of every
model call we made about a client's system, which is our operational data and
not their knowledge. It would also weaken the guarantee the schema's
completeness rests on, that no later build item writes a migration.

**The lookup is keyed by `(scope_ref, idempotency_key)`, not by key alone.** That
is §12's unique index and it matters: two engagements may legitimately choose the
same key, and a global key space would return one client's recorded run to
another's replay.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AgentRunRecord", "AnnexRecords"]


class AgentRunRecord(BaseModel):
    """One `agent_run` row. Contracts §12, column for column.

    `trace_json` is the rendered `Trace` and `output_ref` is a blob reference:
    **output text is never inlined**, which is §12's own comment and AI spec
    §8.3's requirement. A record that carried the output would make the annex
    the one place a client's model responses accumulate in plain text, which is
    the opposite of what an in-client audit trail is for.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    scope_ref: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    skill_ref: str = Field(min_length=1)
    skill_sha256: str = Field(min_length=1)
    inputs_sha256: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    model: str = Field(min_length=1)
    params_hash: str = Field(min_length=1)
    status: str = Field(min_length=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    wall_ms: int | None = Field(default=None, ge=0)
    trace_json: str
    output_ref: str | None = None
    created_at: str = Field(min_length=1)


class AnnexRecords(Protocol):
    """Two operations, and deliberately no third.

    There is **no update and no delete**. A run happened; recording that it
    happened differently later would make the audit trail a claim rather than a
    record. Retention is a separate concern with its own constant
    (`WORKFLOW_RUN_RETENTION_DAYS` is the workflow half; the annex's pruning is
    an operator action, not a seam API), and neither is a reason to give this
    protocol a setter.
    """

    def find_run(self, *, scope_ref: str, idempotency_key: str) -> AgentRunRecord | None:
        """The recorded run for this key in this scope, or `None`.

        `None` means "no run has been recorded", which is the only condition
        under which the seam may call a provider at all.
        """
        ...

    def record_run(self, record: AgentRunRecord) -> AgentRunRecord:
        """Persist a completed run and return **what is stored afterwards**.

        The return value is the point. Two callers may race to record the same
        `(scope_ref, idempotency_key)`, and the loser must be handed the
        winner's record rather than an error: the caller asked what the result
        for this key is, and idempotency means both are told the same thing.
        Raising would send the loser back to a provider call that has already
        been paid for -- the double-spend the annex exists to prevent.

        A realization that returned the caller's own record unchanged would
        satisfy the type and lose the guarantee, so `test_annex.py` asserts the
        loser observes the *winner's* run id.
        """
        ...
