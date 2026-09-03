-- GENERATED FROM schema/canonical.yaml -- DO NOT EDIT.
-- Regenerate with `adopt-schema generate`. A hand edit is SCHEMA_GENERATED_DRIFT
-- and CI fails on it, because a hand-edited realization means the manifest has
-- silently stopped being the single source of truth.

-- schema-version: 4

-- back-out: none is needed, and none is possible in place. This migration
-- only CREATEs tables introduced at schema version 4; it alters nothing
-- that existed before, so every earlier query reads exactly what it read
-- before it ran. A binary too old for version 4 opens the store read-only
-- with SCHEMA_VERSION_TOO_NEW rather than misreading it -- that read-only open
-- IS the recovery path. Removal is `retired_in_version` in
-- schema/canonical.yaml, which keeps the physical object and writes it NULL.

PRAGMA user_version = 4;

-- The human disposition of one derived coverage gap; existence stays derived.
CREATE TABLE coverage_gap (
  id TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL REFERENCES identity(id),
  gap_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved','waived')),
  owner_actor_id TEXT,
  note TEXT,
  waived_until TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_coverage_gap_key ON coverage_gap(gap_key);
CREATE INDEX idx_coverage_gap_status ON coverage_gap(status);
