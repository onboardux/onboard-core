-- GENERATED FROM schema/canonical.yaml -- DO NOT EDIT.
-- Regenerate with `adopt-schema generate`. A hand edit is SCHEMA_GENERATED_DRIFT
-- and CI fails on it, because a hand-edited realization means the manifest has
-- silently stopped being the single source of truth.

-- schema-version: 3

-- back-out: none. This is the initial creation of schema version 3, so the
-- back-out is to drop the database and create a new one. There is no in-place
-- reversal, and none will be written.

-- One appended row per version-write event; the migration log.
CREATE TABLE schema_meta (
  schema_version bigint NOT NULL,
  export_version bigint NOT NULL,
  written_by text NOT NULL,
  written_at timestamptz NOT NULL
);

-- A released classifier build; referenced by revisions and classifications.
CREATE TABLE classifier_version (
  id text PRIMARY KEY,
  version_label text NOT NULL,
  training_data_categories text NOT NULL,
  model_card_ref text,
  released_at timestamptz NOT NULL,
  retired_at timestamptz
);

-- The delivery firm; the root of every scope chain.
CREATE TABLE firm (
  id text PRIMARY KEY,
  slug text NOT NULL UNIQUE,
  name text NOT NULL,
  created_at timestamptz NOT NULL
);

-- A client engagement within a firm.
CREATE TABLE engagement (
  id text PRIMARY KEY,
  firm_id text NOT NULL REFERENCES firm(id),
  slug text NOT NULL,
  name text NOT NULL,
  client_label text,
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz NOT NULL,
  UNIQUE (firm_id, slug)
);

-- A client system under an engagement.
CREATE TABLE system (
  id text PRIMARY KEY,
  engagement_id text NOT NULL REFERENCES engagement(id),
  slug text NOT NULL,
  name text NOT NULL,
  archetype text CHECK (archetype IN ('web','platform','lowcode','data','ai')),
  lifecycle_state text NOT NULL CHECK (lifecycle_state IN ('DISCOVERED','SETUP','PILOT','LIVE','PAUSED','DEGRADED','ARCHIVED','DISCONNECTED')),
  deployment_mode text CHECK (deployment_mode IN ('mode_1','mode_2','mode_3')),
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  UNIQUE (engagement_id, slug)
);

-- A recorded human approval of a revision.
CREATE TABLE approval (
  id text PRIMARY KEY,
  firm_id text NOT NULL REFERENCES firm(id),
  engagement_id text NOT NULL REFERENCES engagement(id),
  subject_type text NOT NULL CHECK (subject_type IN ('knowledge_revision','probe_revision','binding_revision')),
  subject_id text NOT NULL,
  actor_id text NOT NULL,
  approved_at timestamptz NOT NULL,
  scope_note text,
  expires_at timestamptz
);

-- A deployment environment of a system; mandatory in every identity URI.
CREATE TABLE environment (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  slug text NOT NULL,
  name text NOT NULL,
  is_billable boolean NOT NULL DEFAULT false,
  data_residency_region text,
  created_at timestamptz NOT NULL,
  UNIQUE (system_id, slug)
);

-- Merges, splits and state transitions; a transition is never silent.
CREATE TABLE system_lifecycle_event (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  from_state text CHECK (from_state IN ('DISCOVERED','SETUP','PILOT','LIVE','PAUSED','DEGRADED','ARCHIVED','DISCONNECTED')),
  to_state text NOT NULL CHECK (to_state IN ('DISCOVERED','SETUP','PILOT','LIVE','PAUSED','DEGRADED','ARCHIVED','DISCONNECTED')),
  reason text NOT NULL,
  related_system_id text REFERENCES system(id),
  occurred_at timestamptz NOT NULL,
  actor_id text
);
CREATE INDEX idx_sle_system ON system_lifecycle_event(system_id, occurred_at);

-- A registered connector for a system.
CREATE TABLE connector (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  mode text NOT NULL CHECK (mode IN ('outbound_relay','local_only')),
  registered_at timestamptz NOT NULL,
  last_seen_at timestamptz,
  version text,
  status text NOT NULL CHECK (status IN ('active','degraded','revoked'))
);

-- A coalesced set of changes presented to a human once.
CREATE TABLE review_batch (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  batch_key text NOT NULL,
  item_count bigint NOT NULL,
  draft_md text,
  owner_actor_id text,
  opened_at timestamptz NOT NULL,
  resolved_at timestamptz,
  resolution text CHECK (resolution IN ('confirmed','corrected','rejected')),
  review_minutes double precision
);

-- Who owns a system or engagement, and why they were assigned.
CREATE TABLE ownership_assignment (
  id text PRIMARY KEY,
  system_id text REFERENCES system(id),
  engagement_id text REFERENCES engagement(id),
  scope text NOT NULL CHECK (scope IN ('system','engagement','practice','pooled')),
  actor_or_group_id text NOT NULL,
  is_group boolean NOT NULL DEFAULT true,
  effective_from timestamptz NOT NULL,
  effective_to timestamptz,
  assigned_by text,
  assignment_reason text CHECK (assignment_reason IN ('handover','departure','escalation','policy'))
);

-- An immutable record of who did what.
CREATE TABLE audit_event (
  id text PRIMARY KEY,
  firm_id text REFERENCES firm(id),
  system_id text REFERENCES system(id),
  event_type text NOT NULL,
  actor_id text,
  subject_ref text,
  detail text,
  occurred_at timestamptz NOT NULL
);
CREATE INDEX idx_audit_scope ON audit_event(firm_id, occurred_at);

-- A measured starting point a value claim is made against.
CREATE TABLE value_baseline (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  captured_at timestamptz NOT NULL,
  metric text NOT NULL,
  value double precision NOT NULL,
  unit text NOT NULL,
  confidence_label text NOT NULL CHECK (confidence_label IN ('measured','buyer_reported','modelled','low_confidence')),
  captured_by text,
  method_note text
);

-- One ledger entry; the vocabulary is §8's value_event_type.
CREATE TABLE value_event (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  occurred_at timestamptz NOT NULL,
  event_type text NOT NULL,
  minutes double precision,
  actor_id text,
  source_ref text,
  confidence_label text NOT NULL CHECK (confidence_label IN ('measured','buyer_reported','modelled','low_confidence'))
);
CREATE INDEX idx_value_system ON value_event(system_id, occurred_at);

-- A referent addressed by one canonical URI.
CREATE TABLE identity (
  id text PRIMARY KEY,
  uri text NOT NULL UNIQUE,
  firm_id text NOT NULL REFERENCES firm(id),
  engagement_id text NOT NULL REFERENCES engagement(id),
  system_id text NOT NULL REFERENCES system(id),
  environment_id text NOT NULL REFERENCES environment(id),
  identity_kind text NOT NULL CHECK (identity_kind IN ('endpoint','db_field','state_transition','symbol','metadata_component','prompt','tool_schema','model_pin','retrieval_config','flag','job','config_key','ui_component')),
  namespace text,
  local_key text NOT NULL,
  first_seen timestamptz NOT NULL,
  last_seen timestamptz NOT NULL,
  covered_cache boolean NOT NULL DEFAULT false,
  covered_cache_at timestamptz,
  retention_policy_id text
);
CREATE INDEX idx_identity_scope ON identity(system_id, environment_id);
CREATE INDEX idx_identity_kind ON identity(identity_kind);

-- A unit of knowledge; content lives in revisions.
CREATE TABLE knowledge_item (
  id text PRIMARY KEY,
  firm_id text NOT NULL REFERENCES firm(id),
  engagement_id text NOT NULL REFERENCES engagement(id),
  system_id text NOT NULL REFERENCES system(id),
  environment_id text REFERENCES environment(id),
  kind text NOT NULL CHECK (kind IN ('answer','procedure','rationale','surface','recipe')),
  title text NOT NULL,
  current_revision_id text,
  freshness_state text NOT NULL CHECK (freshness_state IN ('fresh','stale','unverified','retired','observation_stale')),
  data_residency_region text,
  retention_policy_id text,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);
CREATE INDEX idx_ki_scope ON knowledge_item(system_id, environment_id);
CREATE INDEX idx_ki_freshness ON knowledge_item(system_id, freshness_state);

-- A change-sensing channel; its health gates freshness.
CREATE TABLE sensor (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  environment_id text NOT NULL REFERENCES environment(id),
  kind text NOT NULL CHECK (kind IN ('webhook','ci','audit_trail','probe','trace','contract')),
  health text NOT NULL CHECK (health IN ('HEALTHY','DEGRADED','STALE','FAILED','DISABLED','UNVERIFIED')),
  expected_cadence_seconds bigint,
  last_attempted_at timestamptz,
  last_success_at timestamptz,
  last_event_at timestamptz,
  credential_expires_at timestamptz,
  safe_path_verified_at timestamptz,
  observed_volume_baseline double precision,
  missing_event_threshold bigint,
  degradation_reason text,
  owner_actor_id text,
  remediation_status text
);
CREATE INDEX idx_sensor_scope ON sensor(system_id, environment_id, health);

-- A named behavioral probe against a system environment.
CREATE TABLE probe_definition (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  environment_id text NOT NULL REFERENCES environment(id),
  name text NOT NULL,
  current_revision_id text,
  schedule_cron text,
  created_at timestamptz NOT NULL
);

-- An observed change in a system environment.
CREATE TABLE change_event (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  environment_id text NOT NULL REFERENCES environment(id),
  source text NOT NULL CHECK (source IN ('artifact','provider','data')),
  detected_at timestamptz NOT NULL,
  referent text,
  batch_key text,
  raw text
);
CREATE INDEX idx_change_batch ON change_event(batch_key);

-- Whether a scope has earned the right to act without a human.
CREATE TABLE silent_repair_eligibility (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  environment_id text REFERENCES environment(id),
  archetype text NOT NULL,
  identity_kind text NOT NULL,
  extractor_version text,
  classifier_version_id text REFERENCES classifier_version(id),
  state text NOT NULL CHECK (state IN ('DISABLED','DETECTION_ONLY','REVIEW_REQUIRED','SILENT_ELIGIBLE','SUSPENDED')),
  observed_event_count bigint NOT NULL DEFAULT 0,
  precision_ci_lower double precision,
  state_changed_at timestamptz NOT NULL,
  state_reason text NOT NULL
);

-- What may be observed and what may leave; hard-limits every downstream claim.
CREATE TABLE observability_boundary (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  environment_id text REFERENCES environment(id),
  tier text NOT NULL CHECK (tier IN ('T0','T1','T2','T3','T4')),
  covered text,
  not_covered text,
  knowledge_plane_location text NOT NULL CHECK (knowledge_plane_location IN ('customer','firm','vendor_isolated')),
  control_plane_location text NOT NULL CHECK (control_plane_location IN ('vendor','customer')),
  permitted_outbound_categories jsonb NOT NULL DEFAULT '["metadata_only"]'::jsonb,
  last_successful_observation_at timestamptz,
  safe_probe_status text,
  owner_actor_id text,
  contractual_approval_ref text,
  declared_at timestamptz NOT NULL,
  contractual boolean NOT NULL DEFAULT false
);

-- An append-only observation of an identity; moves carry an alias.
CREATE TABLE identity_revision (
  id text PRIMARY KEY,
  identity_id text NOT NULL REFERENCES identity(id),
  extractor text,
  extractor_version text,
  source_version text,
  confidence double precision,
  alias_of_identity_id text REFERENCES identity(id),
  status text NOT NULL CHECK (status IN ('active','moved','dead')),
  supersedes_revision_id text REFERENCES identity_revision(id),
  created_at timestamptz NOT NULL,
  created_by_actor_id text
);
CREATE INDEX idx_idrev_identity ON identity_revision(identity_id, created_at);

-- An append-only content revision of a knowledge item.
CREATE TABLE knowledge_revision (
  id text PRIMARY KEY,
  item_id text NOT NULL REFERENCES knowledge_item(id),
  body_md text,
  recipe_json text,
  authority_class text NOT NULL CHECK (authority_class IN ('artifact_observed','behavior_observed','human_confirmed')),
  verification text CHECK (verification IN ('verified','unverified','conflicted')),
  confidence double precision,
  snapshot_date timestamptz,
  source_version text,
  classifier_version_id text REFERENCES classifier_version(id),
  supersedes_revision_id text REFERENCES knowledge_revision(id),
  created_at timestamptz NOT NULL,
  created_by_actor_id text
);
CREATE INDEX idx_krev_item ON knowledge_revision(item_id, created_at);

-- The condition under which an item stops being true; every item needs one.
CREATE TABLE death_condition (
  item_id text NOT NULL REFERENCES knowledge_item(id),
  condition text NOT NULL CHECK (condition IN ('referent_retired','contradicted_by_observation','unused_past_threshold')),
  threshold text,
  PRIMARY KEY (item_id, condition)
);

-- Who a knowledge item is for.
CREATE TABLE audience_tag (
  item_id text NOT NULL REFERENCES knowledge_item(id),
  audience text NOT NULL,
  PRIMARY KEY (item_id, audience)
);

-- Ties a knowledge item to the identity it describes.
CREATE TABLE binding (
  id text PRIMARY KEY,
  item_id text NOT NULL REFERENCES knowledge_item(id),
  identity_id text NOT NULL REFERENCES identity(id),
  current_revision_id text,
  is_load_bearing boolean NOT NULL DEFAULT true,
  freshness_state text NOT NULL CHECK (freshness_state IN ('fresh','stale','unverified','retired','observation_stale')),
  created_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX idx_binding_pair ON binding(item_id, identity_id);
CREATE INDEX idx_binding_identity ON binding(identity_id);

-- One observation attempt; silence is never read as stability.
CREATE TABLE sensor_heartbeat (
  sensor_id text NOT NULL REFERENCES sensor(id),
  observed_at timestamptz NOT NULL,
  outcome text NOT NULL CHECK (outcome IN ('success','empty','failure','skipped')),
  detail text,
  PRIMARY KEY (sensor_id, observed_at)
);

-- An append-only probe definition; a revision without a safe path is unrepresentable.
CREATE TABLE probe_definition_revision (
  id text PRIMARY KEY,
  probe_definition_id text NOT NULL REFERENCES probe_definition(id),
  interaction text NOT NULL,
  safe_path text NOT NULL CHECK (safe_path IN ('mock','sandbox','shadow')),
  diff_method text NOT NULL CHECK (diff_method IN ('exact','embedding_sim','llm_judge','contract_delta')),
  capability_manifest text NOT NULL,
  artifact_signature text,
  approved_by text,
  approved_at timestamptz,
  approval_expires_at timestamptz,
  supersedes_revision_id text REFERENCES probe_definition_revision(id),
  created_at timestamptz NOT NULL
);

-- What a change means for one identity; the cascade is item 10.
CREATE TABLE classification (
  id text PRIMARY KEY,
  change_event_id text NOT NULL REFERENCES change_event(id),
  identity_id text NOT NULL REFERENCES identity(id),
  class text NOT NULL CHECK (class IN ('BINDING_INTACT_RENDER_ONLY','BINDING_INTACT_SEMANTICS_CHANGED','BINDING_MOVED','BINDING_DEAD','UNBOUND_NEW')),
  confidence double precision,
  decided_by text NOT NULL CHECK (decided_by IN ('cascade_step_1','cascade_step_2','cascade_step_3','cascade_step_4','model')),
  classifier_version_id text REFERENCES classifier_version(id),
  evidence text NOT NULL,
  acted_silently boolean NOT NULL DEFAULT false,
  sampled_for_audit boolean NOT NULL DEFAULT false,
  audit_verdict text,
  created_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX idx_cls_pair ON classification(change_event_id, identity_id);

-- Where a revision's claim came from.
CREATE TABLE provenance (
  id text PRIMARY KEY,
  revision_id text NOT NULL REFERENCES knowledge_revision(id),
  source_type text NOT NULL CHECK (source_type IN ('commit','pr','ticket','adr','probe_output','trace','human')),
  source_ref text NOT NULL,
  observed_at timestamptz
);
CREATE INDEX idx_prov_rev ON provenance(revision_id);

-- An append-only observation of a binding.
CREATE TABLE binding_revision (
  id text PRIMARY KEY,
  binding_id text NOT NULL REFERENCES binding(id),
  extractor text,
  extractor_version text,
  confidence double precision,
  locator_rung bigint CHECK (locator_rung BETWEEN 1 AND 5),
  status text NOT NULL CHECK (status IN ('active','moved','retired')),
  supersedes_revision_id text REFERENCES binding_revision(id),
  created_at timestamptz NOT NULL,
  created_by_actor_id text
);
CREATE INDEX idx_brev_binding ON binding_revision(binding_id, created_at);

-- Bet 4 -- intent and reality disagree; representable, never resolved away.
CREATE TABLE conflict (
  id text PRIMARY KEY,
  identity_id text NOT NULL REFERENCES identity(id),
  intent_revision_id text REFERENCES knowledge_revision(id),
  actual_revision_id text REFERENCES knowledge_revision(id),
  detected_at timestamptz NOT NULL,
  disposition text NOT NULL CHECK (disposition IN ('open','defect_filed','drift_accepted'))
);

-- The recorded environment a probe's baseline output was captured under.
CREATE TABLE baseline_version (
  id text PRIMARY KEY,
  probe_definition_revision_id text NOT NULL REFERENCES probe_definition_revision(id),
  environment_id text NOT NULL REFERENCES environment(id),
  model_provider_version text,
  retrieval_dataset_version text,
  tool_schema_version text,
  feature_flags text,
  judge_model text,
  judge_policy text,
  fixture_version text,
  redaction_policy text,
  recorded_output text,
  fingerprint text,
  created_at timestamptz NOT NULL,
  approved_at timestamptz
);

-- One knowledge item inside a review batch.
CREATE TABLE review_item (
  id text PRIMARY KEY,
  review_batch_id text NOT NULL REFERENCES review_batch(id),
  item_id text NOT NULL REFERENCES knowledge_item(id),
  proposed_revision_id text REFERENCES knowledge_revision(id),
  resolution text CHECK (resolution IN ('confirmed','corrected','rejected'))
);

-- A question the system could not answer, and what happened to it.
CREATE TABLE escalation (
  id text PRIMARY KEY,
  system_id text NOT NULL REFERENCES system(id),
  question text,
  branch text NOT NULL CHECK (branch IN ('ungrounded','stale','bug_report')),
  prior_revision_id text REFERENCES knowledge_revision(id),
  status text NOT NULL CHECK (status IN ('open','answered','promoted','reassigned')),
  channel text CHECK (channel IN ('slack','teams','servicenow','jsm','portal','email')),
  owner_actor_id text,
  answered_by text,
  candidate_revision_id text REFERENCES knowledge_revision(id),
  opened_at timestamptz NOT NULL,
  answered_at timestamptz
);

-- One execution of a probe revision.
CREATE TABLE probe_run (
  id text PRIMARY KEY,
  probe_definition_revision_id text NOT NULL REFERENCES probe_definition_revision(id),
  baseline_version_id text REFERENCES baseline_version(id),
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  outcome text NOT NULL CHECK (outcome IN ('success','diff','failure','blocked_by_manifest')),
  cleanup_verified boolean NOT NULL DEFAULT false
);

-- What a probe run observed.
CREATE TABLE probe_observation (
  id text PRIMARY KEY,
  probe_run_id text NOT NULL REFERENCES probe_run(id),
  output text,
  fingerprint text,
  similarity double precision,
  judge_verdict text
);

-- ═══════════════ ROW-LEVEL SECURITY, DERIVED FROM ROW SCOPE ═══════════════

-- Every policy below is generated from the table's declared `scope_ref`.

-- Editing one by hand makes isolation a property of this file rather than of

-- the manifest, and the next regeneration silently reverts it.

-- schema_meta: scope_level 'global' -- holds no client-scoped data, so no policy.

-- classifier_version: scope_level 'global' -- holds no client-scoped data, so no policy.

ALTER TABLE firm ENABLE ROW LEVEL SECURITY;
ALTER TABLE firm FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON firm
  USING      (firm.id = current_setting('adopt.firm_id', true))
  WITH CHECK (firm.id = current_setting('adopt.firm_id', true));

ALTER TABLE engagement ENABLE ROW LEVEL SECURITY;
ALTER TABLE engagement FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON engagement
  USING      (engagement.firm_id = current_setting('adopt.firm_id', true) AND engagement.id = current_setting('adopt.engagement_id', true))
  WITH CHECK (engagement.firm_id = current_setting('adopt.firm_id', true) AND engagement.id = current_setting('adopt.engagement_id', true));

ALTER TABLE system ENABLE ROW LEVEL SECURITY;
ALTER TABLE system FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON system
  USING      (EXISTS (SELECT 1 FROM engagement p0_0 WHERE p0_0.id = system.engagement_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.id = current_setting('adopt.engagement_id', true)))
  WITH CHECK (EXISTS (SELECT 1 FROM engagement p0_0 WHERE p0_0.id = system.engagement_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.id = current_setting('adopt.engagement_id', true)));

ALTER TABLE approval ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON approval
  USING      (approval.firm_id = current_setting('adopt.firm_id', true) AND approval.engagement_id = current_setting('adopt.engagement_id', true))
  WITH CHECK (approval.firm_id = current_setting('adopt.firm_id', true) AND approval.engagement_id = current_setting('adopt.engagement_id', true));

ALTER TABLE environment ENABLE ROW LEVEL SECURITY;
ALTER TABLE environment FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON environment
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = environment.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = environment.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE system_lifecycle_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_lifecycle_event FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON system_lifecycle_event
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = system_lifecycle_event.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = system_lifecycle_event.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE connector ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON connector
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = connector.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = connector.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE review_batch ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_batch FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON review_batch
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = review_batch.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = review_batch.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE ownership_assignment ENABLE ROW LEVEL SECURITY;
ALTER TABLE ownership_assignment FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON ownership_assignment
  USING      ((EXISTS (SELECT 1 FROM engagement p0_0 WHERE p0_0.id = ownership_assignment.engagement_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.id = current_setting('adopt.engagement_id', true)) OR EXISTS (SELECT 1 FROM system p0_1 WHERE p0_1.id = ownership_assignment.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_1.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true)))))
  WITH CHECK ((EXISTS (SELECT 1 FROM engagement p0_0 WHERE p0_0.id = ownership_assignment.engagement_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.id = current_setting('adopt.engagement_id', true)) OR EXISTS (SELECT 1 FROM system p0_1 WHERE p0_1.id = ownership_assignment.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_1.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true)))));

ALTER TABLE audit_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_event FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON audit_event
  USING      (audit_event.firm_id = current_setting('adopt.firm_id', true))
  WITH CHECK (audit_event.firm_id = current_setting('adopt.firm_id', true));

ALTER TABLE value_baseline ENABLE ROW LEVEL SECURITY;
ALTER TABLE value_baseline FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON value_baseline
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = value_baseline.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = value_baseline.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE value_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE value_event FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON value_event
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = value_event.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = value_event.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE identity ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON identity
  USING      (identity.firm_id = current_setting('adopt.firm_id', true) AND identity.engagement_id = current_setting('adopt.engagement_id', true))
  WITH CHECK (identity.firm_id = current_setting('adopt.firm_id', true) AND identity.engagement_id = current_setting('adopt.engagement_id', true));

ALTER TABLE knowledge_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_item FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON knowledge_item
  USING      (knowledge_item.firm_id = current_setting('adopt.firm_id', true) AND knowledge_item.engagement_id = current_setting('adopt.engagement_id', true))
  WITH CHECK (knowledge_item.firm_id = current_setting('adopt.firm_id', true) AND knowledge_item.engagement_id = current_setting('adopt.engagement_id', true));

ALTER TABLE sensor ENABLE ROW LEVEL SECURITY;
ALTER TABLE sensor FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON sensor
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = sensor.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = sensor.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE probe_definition ENABLE ROW LEVEL SECURITY;
ALTER TABLE probe_definition FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON probe_definition
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = probe_definition.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = probe_definition.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE change_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE change_event FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON change_event
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = change_event.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = change_event.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE silent_repair_eligibility ENABLE ROW LEVEL SECURITY;
ALTER TABLE silent_repair_eligibility FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON silent_repair_eligibility
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = silent_repair_eligibility.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = silent_repair_eligibility.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE observability_boundary ENABLE ROW LEVEL SECURITY;
ALTER TABLE observability_boundary FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON observability_boundary
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = observability_boundary.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = observability_boundary.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE identity_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity_revision FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON identity_revision
  USING      (EXISTS (SELECT 1 FROM identity p0_0 WHERE p0_0.id = identity_revision.identity_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)))
  WITH CHECK (EXISTS (SELECT 1 FROM identity p0_0 WHERE p0_0.id = identity_revision.identity_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)));

ALTER TABLE knowledge_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_revision FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON knowledge_revision
  USING      (EXISTS (SELECT 1 FROM knowledge_item p0_0 WHERE p0_0.id = knowledge_revision.item_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)))
  WITH CHECK (EXISTS (SELECT 1 FROM knowledge_item p0_0 WHERE p0_0.id = knowledge_revision.item_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)));

ALTER TABLE death_condition ENABLE ROW LEVEL SECURITY;
ALTER TABLE death_condition FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON death_condition
  USING      (EXISTS (SELECT 1 FROM knowledge_item p0_0 WHERE p0_0.id = death_condition.item_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)))
  WITH CHECK (EXISTS (SELECT 1 FROM knowledge_item p0_0 WHERE p0_0.id = death_condition.item_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)));

ALTER TABLE audience_tag ENABLE ROW LEVEL SECURITY;
ALTER TABLE audience_tag FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON audience_tag
  USING      (EXISTS (SELECT 1 FROM knowledge_item p0_0 WHERE p0_0.id = audience_tag.item_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)))
  WITH CHECK (EXISTS (SELECT 1 FROM knowledge_item p0_0 WHERE p0_0.id = audience_tag.item_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)));

ALTER TABLE binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE binding FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON binding
  USING      (EXISTS (SELECT 1 FROM knowledge_item p0_0 WHERE p0_0.id = binding.item_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)))
  WITH CHECK (EXISTS (SELECT 1 FROM knowledge_item p0_0 WHERE p0_0.id = binding.item_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)));

ALTER TABLE sensor_heartbeat ENABLE ROW LEVEL SECURITY;
ALTER TABLE sensor_heartbeat FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON sensor_heartbeat
  USING      (EXISTS (SELECT 1 FROM sensor p0_0 WHERE p0_0.id = sensor_heartbeat.sensor_id AND EXISTS (SELECT 1 FROM system p1_0 WHERE p1_0.id = p0_0.system_id AND EXISTS (SELECT 1 FROM engagement p2_0 WHERE p2_0.id = p1_0.engagement_id AND p2_0.firm_id = current_setting('adopt.firm_id', true) AND p2_0.id = current_setting('adopt.engagement_id', true)))))
  WITH CHECK (EXISTS (SELECT 1 FROM sensor p0_0 WHERE p0_0.id = sensor_heartbeat.sensor_id AND EXISTS (SELECT 1 FROM system p1_0 WHERE p1_0.id = p0_0.system_id AND EXISTS (SELECT 1 FROM engagement p2_0 WHERE p2_0.id = p1_0.engagement_id AND p2_0.firm_id = current_setting('adopt.firm_id', true) AND p2_0.id = current_setting('adopt.engagement_id', true)))));

ALTER TABLE probe_definition_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE probe_definition_revision FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON probe_definition_revision
  USING      (EXISTS (SELECT 1 FROM probe_definition p0_0 WHERE p0_0.id = probe_definition_revision.probe_definition_id AND EXISTS (SELECT 1 FROM system p1_0 WHERE p1_0.id = p0_0.system_id AND EXISTS (SELECT 1 FROM engagement p2_0 WHERE p2_0.id = p1_0.engagement_id AND p2_0.firm_id = current_setting('adopt.firm_id', true) AND p2_0.id = current_setting('adopt.engagement_id', true)))))
  WITH CHECK (EXISTS (SELECT 1 FROM probe_definition p0_0 WHERE p0_0.id = probe_definition_revision.probe_definition_id AND EXISTS (SELECT 1 FROM system p1_0 WHERE p1_0.id = p0_0.system_id AND EXISTS (SELECT 1 FROM engagement p2_0 WHERE p2_0.id = p1_0.engagement_id AND p2_0.firm_id = current_setting('adopt.firm_id', true) AND p2_0.id = current_setting('adopt.engagement_id', true)))));

ALTER TABLE classification ENABLE ROW LEVEL SECURITY;
ALTER TABLE classification FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON classification
  USING      (EXISTS (SELECT 1 FROM change_event p0_0 WHERE p0_0.id = classification.change_event_id AND EXISTS (SELECT 1 FROM system p1_0 WHERE p1_0.id = p0_0.system_id AND EXISTS (SELECT 1 FROM engagement p2_0 WHERE p2_0.id = p1_0.engagement_id AND p2_0.firm_id = current_setting('adopt.firm_id', true) AND p2_0.id = current_setting('adopt.engagement_id', true)))))
  WITH CHECK (EXISTS (SELECT 1 FROM change_event p0_0 WHERE p0_0.id = classification.change_event_id AND EXISTS (SELECT 1 FROM system p1_0 WHERE p1_0.id = p0_0.system_id AND EXISTS (SELECT 1 FROM engagement p2_0 WHERE p2_0.id = p1_0.engagement_id AND p2_0.firm_id = current_setting('adopt.firm_id', true) AND p2_0.id = current_setting('adopt.engagement_id', true)))));

ALTER TABLE provenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE provenance FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON provenance
  USING      (EXISTS (SELECT 1 FROM knowledge_revision p0_0 WHERE p0_0.id = provenance.revision_id AND EXISTS (SELECT 1 FROM knowledge_item p1_0 WHERE p1_0.id = p0_0.item_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.engagement_id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM knowledge_revision p0_0 WHERE p0_0.id = provenance.revision_id AND EXISTS (SELECT 1 FROM knowledge_item p1_0 WHERE p1_0.id = p0_0.item_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.engagement_id = current_setting('adopt.engagement_id', true))));

ALTER TABLE binding_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE binding_revision FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON binding_revision
  USING      (EXISTS (SELECT 1 FROM binding p0_0 WHERE p0_0.id = binding_revision.binding_id AND EXISTS (SELECT 1 FROM knowledge_item p1_0 WHERE p1_0.id = p0_0.item_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.engagement_id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM binding p0_0 WHERE p0_0.id = binding_revision.binding_id AND EXISTS (SELECT 1 FROM knowledge_item p1_0 WHERE p1_0.id = p0_0.item_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.engagement_id = current_setting('adopt.engagement_id', true))));

ALTER TABLE conflict ENABLE ROW LEVEL SECURITY;
ALTER TABLE conflict FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON conflict
  USING      (EXISTS (SELECT 1 FROM identity p0_0 WHERE p0_0.id = conflict.identity_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)))
  WITH CHECK (EXISTS (SELECT 1 FROM identity p0_0 WHERE p0_0.id = conflict.identity_id AND p0_0.firm_id = current_setting('adopt.firm_id', true) AND p0_0.engagement_id = current_setting('adopt.engagement_id', true)));

ALTER TABLE baseline_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_version FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON baseline_version
  USING      (EXISTS (SELECT 1 FROM probe_definition_revision p0_0 WHERE p0_0.id = baseline_version.probe_definition_revision_id AND EXISTS (SELECT 1 FROM probe_definition p1_0 WHERE p1_0.id = p0_0.probe_definition_id AND EXISTS (SELECT 1 FROM system p2_0 WHERE p2_0.id = p1_0.system_id AND EXISTS (SELECT 1 FROM engagement p3_0 WHERE p3_0.id = p2_0.engagement_id AND p3_0.firm_id = current_setting('adopt.firm_id', true) AND p3_0.id = current_setting('adopt.engagement_id', true))))))
  WITH CHECK (EXISTS (SELECT 1 FROM probe_definition_revision p0_0 WHERE p0_0.id = baseline_version.probe_definition_revision_id AND EXISTS (SELECT 1 FROM probe_definition p1_0 WHERE p1_0.id = p0_0.probe_definition_id AND EXISTS (SELECT 1 FROM system p2_0 WHERE p2_0.id = p1_0.system_id AND EXISTS (SELECT 1 FROM engagement p3_0 WHERE p3_0.id = p2_0.engagement_id AND p3_0.firm_id = current_setting('adopt.firm_id', true) AND p3_0.id = current_setting('adopt.engagement_id', true))))));

ALTER TABLE review_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_item FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON review_item
  USING      (EXISTS (SELECT 1 FROM review_batch p0_0 WHERE p0_0.id = review_item.review_batch_id AND EXISTS (SELECT 1 FROM system p1_0 WHERE p1_0.id = p0_0.system_id AND EXISTS (SELECT 1 FROM engagement p2_0 WHERE p2_0.id = p1_0.engagement_id AND p2_0.firm_id = current_setting('adopt.firm_id', true) AND p2_0.id = current_setting('adopt.engagement_id', true)))))
  WITH CHECK (EXISTS (SELECT 1 FROM review_batch p0_0 WHERE p0_0.id = review_item.review_batch_id AND EXISTS (SELECT 1 FROM system p1_0 WHERE p1_0.id = p0_0.system_id AND EXISTS (SELECT 1 FROM engagement p2_0 WHERE p2_0.id = p1_0.engagement_id AND p2_0.firm_id = current_setting('adopt.firm_id', true) AND p2_0.id = current_setting('adopt.engagement_id', true)))));

ALTER TABLE escalation ENABLE ROW LEVEL SECURITY;
ALTER TABLE escalation FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON escalation
  USING      (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = escalation.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))))
  WITH CHECK (EXISTS (SELECT 1 FROM system p0_0 WHERE p0_0.id = escalation.system_id AND EXISTS (SELECT 1 FROM engagement p1_0 WHERE p1_0.id = p0_0.engagement_id AND p1_0.firm_id = current_setting('adopt.firm_id', true) AND p1_0.id = current_setting('adopt.engagement_id', true))));

ALTER TABLE probe_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE probe_run FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON probe_run
  USING      (EXISTS (SELECT 1 FROM probe_definition_revision p0_0 WHERE p0_0.id = probe_run.probe_definition_revision_id AND EXISTS (SELECT 1 FROM probe_definition p1_0 WHERE p1_0.id = p0_0.probe_definition_id AND EXISTS (SELECT 1 FROM system p2_0 WHERE p2_0.id = p1_0.system_id AND EXISTS (SELECT 1 FROM engagement p3_0 WHERE p3_0.id = p2_0.engagement_id AND p3_0.firm_id = current_setting('adopt.firm_id', true) AND p3_0.id = current_setting('adopt.engagement_id', true))))))
  WITH CHECK (EXISTS (SELECT 1 FROM probe_definition_revision p0_0 WHERE p0_0.id = probe_run.probe_definition_revision_id AND EXISTS (SELECT 1 FROM probe_definition p1_0 WHERE p1_0.id = p0_0.probe_definition_id AND EXISTS (SELECT 1 FROM system p2_0 WHERE p2_0.id = p1_0.system_id AND EXISTS (SELECT 1 FROM engagement p3_0 WHERE p3_0.id = p2_0.engagement_id AND p3_0.firm_id = current_setting('adopt.firm_id', true) AND p3_0.id = current_setting('adopt.engagement_id', true))))));

ALTER TABLE probe_observation ENABLE ROW LEVEL SECURITY;
ALTER TABLE probe_observation FORCE  ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON probe_observation
  USING      (EXISTS (SELECT 1 FROM probe_run p0_0 WHERE p0_0.id = probe_observation.probe_run_id AND EXISTS (SELECT 1 FROM probe_definition_revision p1_0 WHERE p1_0.id = p0_0.probe_definition_revision_id AND EXISTS (SELECT 1 FROM probe_definition p2_0 WHERE p2_0.id = p1_0.probe_definition_id AND EXISTS (SELECT 1 FROM system p3_0 WHERE p3_0.id = p2_0.system_id AND EXISTS (SELECT 1 FROM engagement p4_0 WHERE p4_0.id = p3_0.engagement_id AND p4_0.firm_id = current_setting('adopt.firm_id', true) AND p4_0.id = current_setting('adopt.engagement_id', true)))))))
  WITH CHECK (EXISTS (SELECT 1 FROM probe_run p0_0 WHERE p0_0.id = probe_observation.probe_run_id AND EXISTS (SELECT 1 FROM probe_definition_revision p1_0 WHERE p1_0.id = p0_0.probe_definition_revision_id AND EXISTS (SELECT 1 FROM probe_definition p2_0 WHERE p2_0.id = p1_0.probe_definition_id AND EXISTS (SELECT 1 FROM system p3_0 WHERE p3_0.id = p2_0.system_id AND EXISTS (SELECT 1 FROM engagement p4_0 WHERE p4_0.id = p3_0.engagement_id AND p4_0.firm_id = current_setting('adopt.firm_id', true) AND p4_0.id = current_setting('adopt.engagement_id', true)))))));
