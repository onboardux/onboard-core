-- GENERATED FROM schema/canonical.yaml -- DO NOT EDIT.
-- Regenerate with `adopt-schema generate`. A hand edit is SCHEMA_GENERATED_DRIFT
-- and CI fails on it, because a hand-edited realization means the manifest has
-- silently stopped being the single source of truth.

-- schema-version: 3

-- back-out: none. This is the initial creation of schema version 3, so the
-- back-out is to discard the store file and create a new one. There is no
-- in-place reversal, and none will be written: recovery from a newer store
-- is older code opening it read-only, which is why additive-only is enforced
-- mechanically rather than trusted.

PRAGMA user_version = 3;
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- One appended row per version-write event; the migration log.
CREATE TABLE schema_meta (
  schema_version INTEGER NOT NULL,
  export_version INTEGER NOT NULL,
  written_by TEXT NOT NULL,
  written_at TEXT NOT NULL
);

-- A released classifier build; referenced by revisions and classifications.
CREATE TABLE classifier_version (
  id TEXT PRIMARY KEY,
  version_label TEXT NOT NULL,
  training_data_categories TEXT NOT NULL,
  model_card_ref TEXT,
  released_at TEXT NOT NULL,
  retired_at TEXT
);

-- The delivery firm; the root of every scope chain.
CREATE TABLE firm (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- A recorded human approval of a revision.
CREATE TABLE approval (
  id TEXT PRIMARY KEY,
  subject_type TEXT NOT NULL CHECK (subject_type IN ('knowledge_revision','probe_revision','binding_revision')),
  subject_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  approved_at TEXT NOT NULL,
  scope_note TEXT,
  expires_at TEXT
);

-- A client engagement within a firm.
CREATE TABLE engagement (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firm(id),
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  client_label TEXT,
  started_at TEXT,
  ended_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (firm_id, slug)
);

-- A client system under an engagement.
CREATE TABLE system (
  id TEXT PRIMARY KEY,
  engagement_id TEXT NOT NULL REFERENCES engagement(id),
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  archetype TEXT CHECK (archetype IN ('web','platform','lowcode','data','ai')),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('DISCOVERED','SETUP','PILOT','LIVE','PAUSED','DEGRADED','ARCHIVED','DISCONNECTED')),
  deployment_mode TEXT CHECK (deployment_mode IN ('mode_1','mode_2','mode_3')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (engagement_id, slug)
);

-- A deployment environment of a system; mandatory in every identity URI.
CREATE TABLE environment (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  is_billable INTEGER NOT NULL DEFAULT 0,
  data_residency_region TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (system_id, slug)
);

-- Merges, splits and state transitions; a transition is never silent.
CREATE TABLE system_lifecycle_event (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  from_state TEXT CHECK (from_state IN ('DISCOVERED','SETUP','PILOT','LIVE','PAUSED','DEGRADED','ARCHIVED','DISCONNECTED')),
  to_state TEXT NOT NULL CHECK (to_state IN ('DISCOVERED','SETUP','PILOT','LIVE','PAUSED','DEGRADED','ARCHIVED','DISCONNECTED')),
  reason TEXT NOT NULL,
  related_system_id TEXT REFERENCES system(id),
  occurred_at TEXT NOT NULL,
  actor_id TEXT
);
CREATE INDEX idx_sle_system ON system_lifecycle_event(system_id, occurred_at);

-- A registered connector for a system.
CREATE TABLE connector (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  mode TEXT NOT NULL CHECK (mode IN ('outbound_relay','local_only')),
  registered_at TEXT NOT NULL,
  last_seen_at TEXT,
  version TEXT,
  status TEXT NOT NULL CHECK (status IN ('active','degraded','revoked'))
);

-- A coalesced set of changes presented to a human once.
CREATE TABLE review_batch (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  batch_key TEXT NOT NULL,
  item_count INTEGER NOT NULL,
  draft_md TEXT,
  owner_actor_id TEXT,
  opened_at TEXT NOT NULL,
  resolved_at TEXT,
  resolution TEXT CHECK (resolution IN ('confirmed','corrected','rejected')),
  review_minutes REAL
);

-- Who owns a system or engagement, and why they were assigned.
CREATE TABLE ownership_assignment (
  id TEXT PRIMARY KEY,
  system_id TEXT REFERENCES system(id),
  engagement_id TEXT REFERENCES engagement(id),
  scope TEXT NOT NULL CHECK (scope IN ('system','engagement','practice','pooled')),
  actor_or_group_id TEXT NOT NULL,
  is_group INTEGER NOT NULL DEFAULT 1,
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  assigned_by TEXT,
  assignment_reason TEXT CHECK (assignment_reason IN ('handover','departure','escalation','policy'))
);

-- An immutable record of who did what.
CREATE TABLE audit_event (
  id TEXT PRIMARY KEY,
  firm_id TEXT REFERENCES firm(id),
  system_id TEXT REFERENCES system(id),
  event_type TEXT NOT NULL,
  actor_id TEXT,
  subject_ref TEXT,
  detail TEXT,
  occurred_at TEXT NOT NULL
);
CREATE INDEX idx_audit_scope ON audit_event(firm_id, occurred_at);

-- A measured starting point a value claim is made against.
CREATE TABLE value_baseline (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  captured_at TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  confidence_label TEXT NOT NULL CHECK (confidence_label IN ('measured','buyer_reported','modelled','low_confidence')),
  captured_by TEXT,
  method_note TEXT
);

-- One ledger entry; the vocabulary is §8's value_event_type.
CREATE TABLE value_event (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  occurred_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  minutes REAL,
  actor_id TEXT,
  source_ref TEXT,
  confidence_label TEXT NOT NULL CHECK (confidence_label IN ('measured','buyer_reported','modelled','low_confidence'))
);
CREATE INDEX idx_value_system ON value_event(system_id, occurred_at);

-- A referent addressed by one canonical URI.
CREATE TABLE identity (
  id TEXT PRIMARY KEY,
  uri TEXT NOT NULL UNIQUE,
  firm_id TEXT NOT NULL REFERENCES firm(id),
  engagement_id TEXT NOT NULL REFERENCES engagement(id),
  system_id TEXT NOT NULL REFERENCES system(id),
  environment_id TEXT NOT NULL REFERENCES environment(id),
  identity_kind TEXT NOT NULL CHECK (identity_kind IN ('endpoint','db_field','state_transition','symbol','metadata_component','prompt','tool_schema','model_pin','retrieval_config','flag','job','config_key','ui_component')),
  namespace TEXT,
  local_key TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  covered_cache INTEGER NOT NULL DEFAULT 0,
  covered_cache_at TEXT,
  retention_policy_id TEXT
);
CREATE INDEX idx_identity_scope ON identity(system_id, environment_id);
CREATE INDEX idx_identity_kind ON identity(identity_kind);

-- A unit of knowledge; content lives in revisions.
CREATE TABLE knowledge_item (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firm(id),
  engagement_id TEXT NOT NULL REFERENCES engagement(id),
  system_id TEXT NOT NULL REFERENCES system(id),
  environment_id TEXT REFERENCES environment(id),
  kind TEXT NOT NULL CHECK (kind IN ('answer','procedure','rationale','surface','recipe')),
  title TEXT NOT NULL,
  current_revision_id TEXT,
  freshness_state TEXT NOT NULL CHECK (freshness_state IN ('fresh','stale','unverified','retired','observation_stale')),
  data_residency_region TEXT,
  retention_policy_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_ki_scope ON knowledge_item(system_id, environment_id);
CREATE INDEX idx_ki_freshness ON knowledge_item(system_id, freshness_state);

-- A change-sensing channel; its health gates freshness.
CREATE TABLE sensor (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  environment_id TEXT NOT NULL REFERENCES environment(id),
  kind TEXT NOT NULL CHECK (kind IN ('webhook','ci','audit_trail','probe','trace','contract')),
  health TEXT NOT NULL CHECK (health IN ('HEALTHY','DEGRADED','STALE','FAILED','DISABLED','UNVERIFIED')),
  expected_cadence_seconds INTEGER,
  last_attempted_at TEXT,
  last_success_at TEXT,
  last_event_at TEXT,
  credential_expires_at TEXT,
  safe_path_verified_at TEXT,
  observed_volume_baseline REAL,
  missing_event_threshold INTEGER,
  degradation_reason TEXT,
  owner_actor_id TEXT,
  remediation_status TEXT
);
CREATE INDEX idx_sensor_scope ON sensor(system_id, environment_id, health);

-- A named behavioral probe against a system environment.
CREATE TABLE probe_definition (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  environment_id TEXT NOT NULL REFERENCES environment(id),
  name TEXT NOT NULL,
  current_revision_id TEXT,
  schedule_cron TEXT,
  created_at TEXT NOT NULL
);

-- An observed change in a system environment.
CREATE TABLE change_event (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  environment_id TEXT NOT NULL REFERENCES environment(id),
  source TEXT NOT NULL CHECK (source IN ('artifact','provider','data')),
  detected_at TEXT NOT NULL,
  referent TEXT,
  batch_key TEXT,
  raw TEXT
);
CREATE INDEX idx_change_batch ON change_event(batch_key);

-- Whether a scope has earned the right to act without a human.
CREATE TABLE silent_repair_eligibility (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  environment_id TEXT REFERENCES environment(id),
  archetype TEXT NOT NULL,
  identity_kind TEXT NOT NULL,
  extractor_version TEXT,
  classifier_version_id TEXT REFERENCES classifier_version(id),
  state TEXT NOT NULL CHECK (state IN ('DISABLED','DETECTION_ONLY','REVIEW_REQUIRED','SILENT_ELIGIBLE','SUSPENDED')),
  observed_event_count INTEGER NOT NULL DEFAULT 0,
  precision_ci_lower REAL,
  state_changed_at TEXT NOT NULL,
  state_reason TEXT NOT NULL
);

-- What may be observed and what may leave; hard-limits every downstream claim.
CREATE TABLE observability_boundary (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  environment_id TEXT REFERENCES environment(id),
  tier TEXT NOT NULL CHECK (tier IN ('T0','T1','T2','T3','T4')),
  covered TEXT,
  not_covered TEXT,
  knowledge_plane_location TEXT NOT NULL CHECK (knowledge_plane_location IN ('customer','firm','vendor_isolated')),
  control_plane_location TEXT NOT NULL CHECK (control_plane_location IN ('vendor','customer')),
  permitted_outbound_categories TEXT NOT NULL DEFAULT '["metadata_only"]',
  last_successful_observation_at TEXT,
  safe_probe_status TEXT,
  owner_actor_id TEXT,
  contractual_approval_ref TEXT,
  declared_at TEXT NOT NULL,
  contractual INTEGER NOT NULL DEFAULT 0
);

-- An append-only observation of an identity; moves carry an alias.
CREATE TABLE identity_revision (
  id TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL REFERENCES identity(id),
  extractor TEXT,
  extractor_version TEXT,
  source_version TEXT,
  confidence REAL,
  alias_of_identity_id TEXT REFERENCES identity(id),
  status TEXT NOT NULL CHECK (status IN ('active','moved','dead')),
  supersedes_revision_id TEXT REFERENCES identity_revision(id),
  created_at TEXT NOT NULL,
  created_by_actor_id TEXT
);
CREATE INDEX idx_idrev_identity ON identity_revision(identity_id, created_at);

-- An append-only content revision of a knowledge item.
CREATE TABLE knowledge_revision (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL REFERENCES knowledge_item(id),
  body_md TEXT,
  recipe_json TEXT,
  authority_class TEXT NOT NULL CHECK (authority_class IN ('artifact_observed','behavior_observed','human_confirmed')),
  verification TEXT CHECK (verification IN ('verified','unverified','conflicted')),
  confidence REAL,
  snapshot_date TEXT,
  source_version TEXT,
  classifier_version_id TEXT REFERENCES classifier_version(id),
  supersedes_revision_id TEXT REFERENCES knowledge_revision(id),
  created_at TEXT NOT NULL,
  created_by_actor_id TEXT
);
CREATE INDEX idx_krev_item ON knowledge_revision(item_id, created_at);

-- The condition under which an item stops being true; every item needs one.
CREATE TABLE death_condition (
  item_id TEXT NOT NULL REFERENCES knowledge_item(id),
  condition TEXT NOT NULL CHECK (condition IN ('referent_retired','contradicted_by_observation','unused_past_threshold')),
  threshold TEXT,
  PRIMARY KEY (item_id, condition)
);

-- Who a knowledge item is for.
CREATE TABLE audience_tag (
  item_id TEXT NOT NULL REFERENCES knowledge_item(id),
  audience TEXT NOT NULL,
  PRIMARY KEY (item_id, audience)
);

-- Ties a knowledge item to the identity it describes.
CREATE TABLE binding (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL REFERENCES knowledge_item(id),
  identity_id TEXT NOT NULL REFERENCES identity(id),
  current_revision_id TEXT,
  is_load_bearing INTEGER NOT NULL DEFAULT 1,
  freshness_state TEXT NOT NULL CHECK (freshness_state IN ('fresh','stale','unverified','retired','observation_stale')),
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_binding_pair ON binding(item_id, identity_id);
CREATE INDEX idx_binding_identity ON binding(identity_id);

-- One observation attempt; silence is never read as stability.
CREATE TABLE sensor_heartbeat (
  sensor_id TEXT NOT NULL REFERENCES sensor(id),
  observed_at TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('success','empty','failure','skipped')),
  detail TEXT,
  PRIMARY KEY (sensor_id, observed_at)
);

-- An append-only probe definition; a revision without a safe path is unrepresentable.
CREATE TABLE probe_definition_revision (
  id TEXT PRIMARY KEY,
  probe_definition_id TEXT NOT NULL REFERENCES probe_definition(id),
  interaction TEXT NOT NULL,
  safe_path TEXT NOT NULL CHECK (safe_path IN ('mock','sandbox','shadow')),
  diff_method TEXT NOT NULL CHECK (diff_method IN ('exact','embedding_sim','llm_judge','contract_delta')),
  capability_manifest TEXT NOT NULL,
  artifact_signature TEXT,
  approved_by TEXT,
  approved_at TEXT,
  approval_expires_at TEXT,
  supersedes_revision_id TEXT REFERENCES probe_definition_revision(id),
  created_at TEXT NOT NULL
);

-- What a change means for one identity; the cascade is item 10.
CREATE TABLE classification (
  id TEXT PRIMARY KEY,
  change_event_id TEXT NOT NULL REFERENCES change_event(id),
  identity_id TEXT NOT NULL REFERENCES identity(id),
  class TEXT NOT NULL CHECK (class IN ('BINDING_INTACT_RENDER_ONLY','BINDING_INTACT_SEMANTICS_CHANGED','BINDING_MOVED','BINDING_DEAD','UNBOUND_NEW')),
  confidence REAL,
  decided_by TEXT NOT NULL CHECK (decided_by IN ('cascade_step_1','cascade_step_2','cascade_step_3','cascade_step_4','model')),
  classifier_version_id TEXT REFERENCES classifier_version(id),
  evidence TEXT NOT NULL,
  acted_silently INTEGER NOT NULL DEFAULT 0,
  sampled_for_audit INTEGER NOT NULL DEFAULT 0,
  audit_verdict TEXT,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_cls_pair ON classification(change_event_id, identity_id);

-- Where a revision's claim came from.
CREATE TABLE provenance (
  id TEXT PRIMARY KEY,
  revision_id TEXT NOT NULL REFERENCES knowledge_revision(id),
  source_type TEXT NOT NULL CHECK (source_type IN ('commit','pr','ticket','adr','probe_output','trace','human')),
  source_ref TEXT NOT NULL,
  observed_at TEXT
);
CREATE INDEX idx_prov_rev ON provenance(revision_id);

-- An append-only observation of a binding.
CREATE TABLE binding_revision (
  id TEXT PRIMARY KEY,
  binding_id TEXT NOT NULL REFERENCES binding(id),
  extractor TEXT,
  extractor_version TEXT,
  confidence REAL,
  locator_rung INTEGER CHECK (locator_rung BETWEEN 1 AND 5),
  status TEXT NOT NULL CHECK (status IN ('active','moved','retired')),
  supersedes_revision_id TEXT REFERENCES binding_revision(id),
  created_at TEXT NOT NULL,
  created_by_actor_id TEXT
);
CREATE INDEX idx_brev_binding ON binding_revision(binding_id, created_at);

-- Bet 4 -- intent and reality disagree; representable, never resolved away.
CREATE TABLE conflict (
  id TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL REFERENCES identity(id),
  intent_revision_id TEXT REFERENCES knowledge_revision(id),
  actual_revision_id TEXT REFERENCES knowledge_revision(id),
  detected_at TEXT NOT NULL,
  disposition TEXT NOT NULL CHECK (disposition IN ('open','defect_filed','drift_accepted'))
);

-- The recorded environment a probe's baseline output was captured under.
CREATE TABLE baseline_version (
  id TEXT PRIMARY KEY,
  probe_definition_revision_id TEXT NOT NULL REFERENCES probe_definition_revision(id),
  environment_id TEXT NOT NULL REFERENCES environment(id),
  model_provider_version TEXT,
  retrieval_dataset_version TEXT,
  tool_schema_version TEXT,
  feature_flags TEXT,
  judge_model TEXT,
  judge_policy TEXT,
  fixture_version TEXT,
  redaction_policy TEXT,
  recorded_output TEXT,
  fingerprint TEXT,
  created_at TEXT NOT NULL,
  approved_at TEXT
);

-- One knowledge item inside a review batch.
CREATE TABLE review_item (
  id TEXT PRIMARY KEY,
  review_batch_id TEXT NOT NULL REFERENCES review_batch(id),
  item_id TEXT NOT NULL REFERENCES knowledge_item(id),
  proposed_revision_id TEXT REFERENCES knowledge_revision(id),
  resolution TEXT CHECK (resolution IN ('confirmed','corrected','rejected'))
);

-- A question the system could not answer, and what happened to it.
CREATE TABLE escalation (
  id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL REFERENCES system(id),
  question TEXT,
  branch TEXT NOT NULL CHECK (branch IN ('ungrounded','stale','bug_report')),
  prior_revision_id TEXT REFERENCES knowledge_revision(id),
  status TEXT NOT NULL CHECK (status IN ('open','answered','promoted','reassigned')),
  channel TEXT CHECK (channel IN ('slack','teams','servicenow','jsm','portal','email')),
  owner_actor_id TEXT,
  answered_by TEXT,
  candidate_revision_id TEXT REFERENCES knowledge_revision(id),
  opened_at TEXT NOT NULL,
  answered_at TEXT
);

-- One execution of a probe revision.
CREATE TABLE probe_run (
  id TEXT PRIMARY KEY,
  probe_definition_revision_id TEXT NOT NULL REFERENCES probe_definition_revision(id),
  baseline_version_id TEXT REFERENCES baseline_version(id),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  outcome TEXT NOT NULL CHECK (outcome IN ('success','diff','failure','blocked_by_manifest')),
  cleanup_verified INTEGER NOT NULL DEFAULT 0
);

-- What a probe run observed.
CREATE TABLE probe_observation (
  id TEXT PRIMARY KEY,
  probe_run_id TEXT NOT NULL REFERENCES probe_run(id),
  output TEXT,
  fingerprint TEXT,
  similarity REAL,
  judge_verdict TEXT
);
