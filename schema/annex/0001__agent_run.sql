-- Runtime annex, version 1 -- contracts §12, ratified as CR-08 on 2026-08-06.
--
-- This file is NOT part of the canonical schema and NOT part of `schema_version`.
-- It is hand-written on purpose: the manifest is the authority for everything a
-- client can be handed, and `agent_run` is deliberately not that. It holds
-- `AgentRunner` idempotency and in-client audit, it is never exported, and no
-- bundle has a file for it (contracts §11 -- `blobs` is empty at schema
-- version 3 precisely because `agent_run.output_ref` lives here instead).
--
-- It sits under `schema/annex/` rather than `schema/migrations/` so that the
-- separation is visible in the tree rather than asserted in a comment. Putting
-- it in the migration chain would fold it into `schema_version`, which is the
-- one thing CR-08 decided against; putting it inside the package would hide the
-- only DDL in this repository that no emitter produced.
--
-- back-out: drop the annex file. There is nothing to migrate and nothing to
-- preserve -- an annex row records that a run happened and what it cost, and a
-- lost annex costs a replay, not a fact about a client's system.
-- annex-version: 1

CREATE TABLE IF NOT EXISTS agent_run (
  id              TEXT PRIMARY KEY,
  scope_ref       TEXT NOT NULL,                -- firm/engagement slugs; not an FK across stores
  idempotency_key TEXT NOT NULL,
  skill_ref       TEXT NOT NULL, skill_sha256 TEXT NOT NULL, inputs_sha256 TEXT NOT NULL,
  adapter         TEXT NOT NULL, model TEXT NOT NULL, params_hash TEXT NOT NULL,
  status          TEXT NOT NULL,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, wall_ms INTEGER,
  trace_json      TEXT NOT NULL,
  output_ref      TEXT,                         -- blob ref; output text is never inlined
  created_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_idem ON agent_run(scope_ref, idempotency_key);
