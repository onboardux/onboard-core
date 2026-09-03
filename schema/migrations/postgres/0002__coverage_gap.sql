-- GENERATED FROM schema/canonical.yaml -- DO NOT EDIT.
-- Regenerate with `adopt-schema generate`. A hand edit is SCHEMA_GENERATED_DRIFT
-- and CI fails on it, because a hand-edited realization means the manifest has
-- silently stopped being the single source of truth.

-- schema-version: 4

-- back-out: none is needed, and none is possible in place. This migration
-- only CREATEs tables introduced at schema version 4, each with its
-- derived row-level-security policy; it alters nothing that existed before.
-- Removal is `retired_in_version` in schema/canonical.yaml, which keeps the
-- physical object and writes it NULL.

-- The human disposition of one derived coverage gap; existence stays derived.
CREATE TABLE coverage_gap (
  id text PRIMARY KEY,
  identity_id text NOT NULL REFERENCES identity(id),
  gap_key text NOT NULL,
  status text NOT NULL CHECK (status IN ('open','acknowledged','resolved','waived')),
  owner_actor_id text,
  note text,
  waived_until timestamptz,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX idx_coverage_gap_key ON coverage_gap(gap_key);
CREATE INDEX idx_coverage_gap_status ON coverage_gap(status);

-- ═══════════════ ROW-LEVEL SECURITY, DERIVED FROM ROW SCOPE ═══════════════

-- Every policy below is generated from the table's declared `scope_ref`.

-- Editing one by hand makes isolation a property of this file rather than of

-- the manifest, and the next regeneration silently reverts it.

ALTER TABLE coverage_gap ENABLE ROW LEVEL SECURITY;
ALTER TABLE coverage_gap FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON coverage_gap
  USING      (EXISTS (SELECT 1 FROM identity p0_0 WHERE p0_0.id = coverage_gap.identity_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)))
  WITH CHECK (EXISTS (SELECT 1 FROM identity p0_0 WHERE p0_0.id = coverage_gap.identity_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)));
