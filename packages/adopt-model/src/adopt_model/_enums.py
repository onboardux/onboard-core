"""Enum vocabularies, generated from the canonical manifest."""

# GENERATED FROM schema/canonical.yaml -- DO NOT EDIT.
# Regenerate with `adopt-schema generate`. A hand edit is SCHEMA_GENERATED_DRIFT
# and CI fails on it, because a hand-edited realization means the manifest has
# silently stopped being the single source of truth.

from typing import Literal

__all__ = [
    "ApprovalSubject",
    "Archetype",
    "AssignmentReason",
    "AuthorityClass",
    "BindingStatus",
    "ChangeSource",
    "ConfidenceLabel",
    "ConnectorMode",
    "ConnectorStatus",
    "ControlPlane",
    "DeathCause",
    "DecidedBy",
    "DeploymentMode",
    "DiffMethod",
    "Disposition",
    "EscalationBranch",
    "EscalationChannel",
    "EscalationStatus",
    "FreshnessState",
    "HeartbeatOutcome",
    "IdentityKind",
    "IdentityStatus",
    "ImpactClass",
    "ItemKind",
    "KnowledgePlane",
    "LifecycleState",
    "LocatorRung",
    "OwnershipScope",
    "ProbeDefinitionStatus",
    "ProbeOutcome",
    "ReviewResolution",
    "SafePath",
    "SensorHealth",
    "SensorKind",
    "SilentRepairState",
    "SourceType",
    "Tier",
    "ValueEventType",
    "Verification",
]

ApprovalSubject = Literal[
    "knowledge_revision",
    "probe_revision",
    "binding_revision",
]
Archetype = Literal[
    "web",
    "platform",
    "lowcode",
    "data",
    "ai",
]
AssignmentReason = Literal[
    "handover",
    "departure",
    "escalation",
    "policy",
]
AuthorityClass = Literal[
    "artifact_observed",
    "behavior_observed",
    "human_confirmed",
]
BindingStatus = Literal[
    "active",
    "moved",
    "retired",
]
ChangeSource = Literal[
    "artifact",
    "provider",
    "data",
]
ConfidenceLabel = Literal[
    "measured",
    "buyer_reported",
    "modelled",
    "low_confidence",
]
ConnectorMode = Literal[
    "outbound_relay",
    "local_only",
]
ConnectorStatus = Literal[
    "active",
    "degraded",
    "revoked",
]
ControlPlane = Literal[
    "vendor",
    "customer",
]
DeathCause = Literal[
    "referent_retired",
    "contradicted_by_observation",
    "unused_past_threshold",
]
DecidedBy = Literal[
    "cascade_step_1",
    "cascade_step_2",
    "cascade_step_3",
    "cascade_step_4",
    "model",
]
DeploymentMode = Literal[
    "mode_1",
    "mode_2",
    "mode_3",
]
DiffMethod = Literal[
    "exact",
    "embedding_sim",
    "llm_judge",
    "contract_delta",
]
Disposition = Literal[
    "open",
    "defect_filed",
    "drift_accepted",
]
EscalationBranch = Literal[
    "ungrounded",
    "stale",
    "bug_report",
]
EscalationChannel = Literal[
    "slack",
    "teams",
    "servicenow",
    "jsm",
    "portal",
    "email",
]
EscalationStatus = Literal[
    "open",
    "answered",
    "promoted",
    "reassigned",
]
FreshnessState = Literal[
    "fresh",
    "stale",
    "unverified",
    "retired",
    "observation_stale",
]
HeartbeatOutcome = Literal[
    "success",
    "empty",
    "failure",
    "skipped",
]
IdentityKind = Literal[
    "endpoint",
    "db_field",
    "state_transition",
    "symbol",
    "metadata_component",
    "prompt",
    "tool_schema",
    "model_pin",
    "retrieval_config",
    "flag",
    "job",
    "config_key",
    "ui_component",
]
IdentityStatus = Literal[
    "active",
    "moved",
    "dead",
]
ImpactClass = Literal[
    "BINDING_INTACT_RENDER_ONLY",
    "BINDING_INTACT_SEMANTICS_CHANGED",
    "BINDING_MOVED",
    "BINDING_DEAD",
    "UNBOUND_NEW",
]
ItemKind = Literal[
    "answer",
    "procedure",
    "rationale",
    "surface",
    "recipe",
]
KnowledgePlane = Literal[
    "customer",
    "firm",
    "vendor_isolated",
]
LifecycleState = Literal[
    "DISCOVERED",
    "SETUP",
    "PILOT",
    "LIVE",
    "PAUSED",
    "DEGRADED",
    "ARCHIVED",
    "DISCONNECTED",
]
LocatorRung = Literal[
    1,  # const-sync: ok -- manifest enum value, not a tunable.
    2,  # const-sync: ok -- manifest enum value, not a tunable.
    3,  # const-sync: ok -- manifest enum value, not a tunable.
    4,  # const-sync: ok -- manifest enum value, not a tunable.
    5,  # const-sync: ok -- manifest enum value, not a tunable.
]
OwnershipScope = Literal[
    "system",
    "engagement",
    "practice",
    "pooled",
]
ProbeDefinitionStatus = Literal[
    "active",
    "retired",
]
ProbeOutcome = Literal[
    "success",
    "diff",
    "failure",
    "blocked_by_manifest",
]
ReviewResolution = Literal[
    "confirmed",
    "corrected",
    "rejected",
]
SafePath = Literal[
    "mock",
    "sandbox",
    "shadow",
]
SensorHealth = Literal[
    "HEALTHY",
    "DEGRADED",
    "STALE",
    "FAILED",
    "DISABLED",
    "UNVERIFIED",
]
SensorKind = Literal[
    "webhook",
    "ci",
    "audit_trail",
    "probe",
    "trace",
    "contract",
]
SilentRepairState = Literal[
    "DISABLED",
    "DETECTION_ONLY",
    "REVIEW_REQUIRED",
    "SILENT_ELIGIBLE",
    "SUSPENDED",
]
SourceType = Literal[
    "commit",
    "pr",
    "ticket",
    "adr",
    "probe_output",
    "trace",
    "human",
]
Tier = Literal[
    "T0",
    "T1",
    "T2",
    "T3",
    "T4",
]
ValueEventType = Literal[
    "question.received",
    "question.grounded",
    "question.reused_confirmed_canon",
    "question.escalated",
    "question.reopened",
    "correction.confirmed",
    "correction.minutes_spent",
    "change.detected",
    "knowledge.marked_stale",
    "content.regenerated",
    "content.reviewed",
    "content.retired",
    "review.minutes_spent",
    "handover.generated",
    "handover.correction_minutes",
    "sensor_unavailable",
    "fde_reengagement_required",
]
Verification = Literal[
    "verified",
    "unverified",
    "conflicted",
]
