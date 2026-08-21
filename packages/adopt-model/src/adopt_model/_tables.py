"""One validating model per canonical table."""

# GENERATED FROM schema/canonical.yaml -- DO NOT EDIT.
# Regenerate with `adopt-schema generate`. A hand edit is SCHEMA_GENERATED_DRIFT
# and CI fails on it, because a hand-edited realization means the manifest has
# silently stopped being the single source of truth.

from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from adopt_model._enums import (
    ApprovalSubject,
    Archetype,
    AssignmentReason,
    AuthorityClass,
    BindingStatus,
    ChangeSource,
    ConfidenceLabel,
    ConnectorMode,
    ConnectorStatus,
    ControlPlane,
    DeathCause,
    DecidedBy,
    DeploymentMode,
    DiffMethod,
    Disposition,
    EscalationBranch,
    EscalationChannel,
    EscalationStatus,
    FreshnessState,
    HeartbeatOutcome,
    IdentityKind,
    IdentityStatus,
    ImpactClass,
    ItemKind,
    KnowledgePlane,
    LifecycleState,
    LocatorRung,
    OwnershipScope,
    ProbeDefinitionStatus,
    ProbeOutcome,
    ReviewResolution,
    SafePath,
    SensorHealth,
    SensorKind,
    SilentRepairState,
    SourceType,
    Tier,
    Verification,
)

__all__ = [
    "Approval",
    "AudienceTag",
    "AuditEvent",
    "BaselineVersion",
    "Binding",
    "BindingRevision",
    "ChangeEvent",
    "Classification",
    "ClassifierVersion",
    "Conflict",
    "Connector",
    "DeathCondition",
    "Engagement",
    "Environment",
    "Escalation",
    "Firm",
    "Identity",
    "IdentityRevision",
    "KnowledgeItem",
    "KnowledgeRevision",
    "ObservabilityBoundary",
    "OwnershipAssignment",
    "ProbeDefinition",
    "ProbeDefinitionRevision",
    "ProbeObservation",
    "ProbeRun",
    "Provenance",
    "ReviewBatch",
    "ReviewItem",
    "SchemaMeta",
    "Sensor",
    "SensorHeartbeat",
    "SilentRepairEligibility",
    "System",
    "SystemLifecycleEvent",
    "ValueBaseline",
    "ValueEvent",
]


_CONFIG = ConfigDict(
    # Strict and closed: the schema is the egress allowlist.
    extra="forbid",
    populate_by_name=True,
    # Several canonical columns legitimately start with `model_`
    # (`model_card_ref`, `model_provider_version`), which is pydantic's
    # protected prefix. The column names are the contract, so the
    # protection is released rather than the columns renamed.
    protected_namespaces=(),
)


class SchemaMeta(BaseModel):
    """One appended row per version-write event; the migration log."""

    model_config = _CONFIG

    schema_version: int
    export_version: int
    written_by: str
    written_at: AwareDatetime


class ClassifierVersion(BaseModel):
    """A released classifier build; referenced by revisions and classifications."""

    model_config = _CONFIG

    id: str
    version_label: str
    training_data_categories: str
    model_card_ref: str | None = None
    released_at: AwareDatetime
    retired_at: AwareDatetime | None = None


class Firm(BaseModel):
    """The delivery firm; the root of every scope chain."""

    model_config = _CONFIG

    id: str
    slug: str
    name: str
    created_at: AwareDatetime


class Engagement(BaseModel):
    """A client engagement within a firm."""

    model_config = _CONFIG

    id: str
    firm_id: str
    slug: str
    name: str
    client_label: str | None = None
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    created_at: AwareDatetime


class System(BaseModel):
    """A client system under an engagement."""

    model_config = _CONFIG

    id: str
    engagement_id: str
    slug: str
    name: str
    archetype: Archetype | None = None
    lifecycle_state: LifecycleState
    deployment_mode: DeploymentMode | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Approval(BaseModel):
    """A recorded human approval of a revision."""

    model_config = _CONFIG

    id: str
    firm_id: str
    engagement_id: str
    subject_type: ApprovalSubject
    subject_id: str
    actor_id: str
    approved_at: AwareDatetime
    scope_note: str | None = None
    expires_at: AwareDatetime | None = None


class Environment(BaseModel):
    """A deployment environment of a system; mandatory in every identity URI."""

    model_config = _CONFIG

    id: str
    system_id: str
    slug: str
    name: str
    is_billable: bool = False
    data_residency_region: str | None = None
    created_at: AwareDatetime


class SystemLifecycleEvent(BaseModel):
    """Merges, splits and state transitions; a transition is never silent."""

    model_config = _CONFIG

    id: str
    system_id: str
    from_state: LifecycleState | None = None
    to_state: LifecycleState
    reason: str
    related_system_id: str | None = None
    occurred_at: AwareDatetime
    actor_id: str | None = None


class Connector(BaseModel):
    """A registered connector for a system."""

    model_config = _CONFIG

    id: str
    system_id: str
    mode: ConnectorMode
    registered_at: AwareDatetime
    last_seen_at: AwareDatetime | None = None
    version: str | None = None
    status: ConnectorStatus


class ReviewBatch(BaseModel):
    """A coalesced set of changes presented to a human once."""

    model_config = _CONFIG

    id: str
    system_id: str
    batch_key: str
    item_count: int
    draft_md: str | None = None
    owner_actor_id: str | None = None
    opened_at: AwareDatetime
    resolved_at: AwareDatetime | None = None
    resolution: ReviewResolution | None = None
    review_minutes: float | None = None


class OwnershipAssignment(BaseModel):
    """Who owns a system or engagement, and why they were assigned."""

    model_config = _CONFIG

    id: str
    system_id: str | None = None
    engagement_id: str | None = None
    scope: OwnershipScope
    actor_or_group_id: str
    is_group: bool = True
    effective_from: AwareDatetime
    effective_to: AwareDatetime | None = None
    assigned_by: str | None = None
    assignment_reason: AssignmentReason | None = None


class AuditEvent(BaseModel):
    """An immutable record of who did what."""

    model_config = _CONFIG

    id: str
    firm_id: str | None = None
    system_id: str | None = None
    event_type: str
    actor_id: str | None = None
    subject_ref: str | None = None
    detail: str | None = None
    occurred_at: AwareDatetime


class ValueBaseline(BaseModel):
    """A measured starting point a value claim is made against."""

    model_config = _CONFIG

    id: str
    system_id: str
    captured_at: AwareDatetime
    metric: str
    value: float
    unit: str
    confidence_label: ConfidenceLabel
    captured_by: str | None = None
    method_note: str | None = None


class ValueEvent(BaseModel):
    """One ledger entry; the vocabulary is §8's value_event_type."""

    model_config = _CONFIG

    id: str
    system_id: str
    occurred_at: AwareDatetime
    event_type: str
    minutes: float | None = None
    actor_id: str | None = None
    source_ref: str | None = None
    confidence_label: ConfidenceLabel


class Identity(BaseModel):
    """A referent addressed by one canonical URI."""

    model_config = _CONFIG

    id: str
    uri: str
    firm_id: str
    engagement_id: str
    system_id: str
    environment_id: str
    identity_kind: IdentityKind
    namespace: str | None = None
    local_key: str
    first_seen: AwareDatetime
    last_seen: AwareDatetime
    covered_cache: bool = False
    covered_cache_at: AwareDatetime | None = None
    retention_policy_id: str | None = None


class KnowledgeItem(BaseModel):
    """A unit of knowledge; content lives in revisions."""

    model_config = _CONFIG

    id: str
    firm_id: str
    engagement_id: str
    system_id: str
    environment_id: str | None = None
    kind: ItemKind
    title: str
    current_revision_id: str | None = None
    freshness_state: FreshnessState
    data_residency_region: str | None = None
    retention_policy_id: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Sensor(BaseModel):
    """A change-sensing channel; its health gates freshness."""

    model_config = _CONFIG

    id: str
    system_id: str
    environment_id: str
    kind: SensorKind
    health: SensorHealth
    expected_cadence_seconds: int | None = None
    last_attempted_at: AwareDatetime | None = None
    last_success_at: AwareDatetime | None = None
    last_event_at: AwareDatetime | None = None
    credential_expires_at: AwareDatetime | None = None
    safe_path_verified_at: AwareDatetime | None = None
    observed_volume_baseline: float | None = None
    missing_event_threshold: int | None = None
    degradation_reason: str | None = None
    owner_actor_id: str | None = None
    remediation_status: str | None = None


class ProbeDefinition(BaseModel):
    """A named behavioral probe against a system environment."""

    model_config = _CONFIG

    id: str
    system_id: str
    environment_id: str
    name: str
    current_revision_id: str | None = None
    schedule_cron: str | None = None
    created_at: AwareDatetime


class ChangeEvent(BaseModel):
    """An observed change in a system environment."""

    model_config = _CONFIG

    id: str
    system_id: str
    environment_id: str
    source: ChangeSource
    detected_at: AwareDatetime
    referent: str | None = None
    batch_key: str | None = None
    raw: str | None = None


class SilentRepairEligibility(BaseModel):
    """Whether a scope has earned the right to act without a human."""

    model_config = _CONFIG

    id: str
    system_id: str
    environment_id: str | None = None
    archetype: str
    identity_kind: str
    extractor_version: str | None = None
    classifier_version_id: str | None = None
    state: SilentRepairState
    observed_event_count: int = 0
    precision_ci_lower: float | None = None
    state_changed_at: AwareDatetime
    state_reason: str


class ObservabilityBoundary(BaseModel):
    """What may be observed and what may leave; hard-limits every downstream claim."""

    model_config = _CONFIG

    id: str
    system_id: str
    environment_id: str | None = None
    tier: Tier
    covered: str | None = None
    not_covered: str | None = None
    knowledge_plane_location: KnowledgePlane
    control_plane_location: ControlPlane
    permitted_outbound_categories: dict[str, Any] | list[Any] = ["metadata_only"]
    last_successful_observation_at: AwareDatetime | None = None
    safe_probe_status: str | None = None
    owner_actor_id: str | None = None
    contractual_approval_ref: str | None = None
    declared_at: AwareDatetime
    contractual: bool = False


class IdentityRevision(BaseModel):
    """An append-only observation of an identity; moves carry an alias."""

    model_config = _CONFIG

    id: str
    identity_id: str
    extractor: str | None = None
    extractor_version: str | None = None
    source_version: str | None = None
    source_ref: str | None = None
    confidence: float | None = None
    alias_of_identity_id: str | None = None
    status: IdentityStatus
    supersedes_revision_id: str | None = None
    created_at: AwareDatetime
    created_by_actor_id: str | None = None


class KnowledgeRevision(BaseModel):
    """An append-only content revision of a knowledge item."""

    model_config = _CONFIG

    id: str
    item_id: str
    body_md: str | None = None
    recipe_json: str | None = None
    authority_class: AuthorityClass
    verification: Verification | None = None
    confidence: float | None = None
    snapshot_date: AwareDatetime | None = None
    source_version: str | None = None
    classifier_version_id: str | None = None
    supersedes_revision_id: str | None = None
    created_at: AwareDatetime
    created_by_actor_id: str | None = None


class DeathCondition(BaseModel):
    """The condition under which an item stops being true; every item needs one."""

    model_config = _CONFIG

    item_id: str
    condition: DeathCause
    threshold: str | None = None


class AudienceTag(BaseModel):
    """Who a knowledge item is for."""

    model_config = _CONFIG

    item_id: str
    audience: str


class Binding(BaseModel):
    """Ties a knowledge item to the identity it describes."""

    model_config = _CONFIG

    id: str
    item_id: str
    identity_id: str
    current_revision_id: str | None = None
    is_load_bearing: bool = True
    freshness_state: FreshnessState
    created_at: AwareDatetime


class SensorHeartbeat(BaseModel):
    """One observation attempt; silence is never read as stability."""

    model_config = _CONFIG

    sensor_id: str
    observed_at: AwareDatetime
    outcome: HeartbeatOutcome
    detail: str | None = None


class ProbeDefinitionRevision(BaseModel):
    """An append-only probe definition; a revision without a safe path is unrepresentable."""

    model_config = _CONFIG

    id: str
    probe_definition_id: str
    interaction: str
    safe_path: SafePath
    diff_method: DiffMethod
    status: ProbeDefinitionStatus
    capability_manifest: str
    artifact_signature: str | None = None
    approved_by: str | None = None
    approved_at: AwareDatetime | None = None
    approval_expires_at: AwareDatetime | None = None
    supersedes_revision_id: str | None = None
    created_at: AwareDatetime


class Classification(BaseModel):
    """What a change means for one identity; the cascade is item 10."""

    model_config = _CONFIG

    id: str
    change_event_id: str
    identity_id: str
    class_: ImpactClass = Field(alias="class")
    confidence: float | None = None
    decided_by: DecidedBy
    classifier_version_id: str | None = None
    evidence: str
    acted_silently: bool = False
    sampled_for_audit: bool = False
    audit_verdict: str | None = None
    created_at: AwareDatetime


class Provenance(BaseModel):
    """Where a revision's claim came from."""

    model_config = _CONFIG

    id: str
    revision_id: str
    source_type: SourceType
    source_ref: str
    observed_at: AwareDatetime | None = None


class BindingRevision(BaseModel):
    """An append-only observation of a binding."""

    model_config = _CONFIG

    id: str
    binding_id: str
    extractor: str | None = None
    extractor_version: str | None = None
    confidence: float | None = None
    locator_rung: LocatorRung | None = None
    status: BindingStatus
    supersedes_revision_id: str | None = None
    created_at: AwareDatetime
    created_by_actor_id: str | None = None


class Conflict(BaseModel):
    """Bet 4 -- intent and reality disagree; representable, never resolved away."""

    model_config = _CONFIG

    id: str
    identity_id: str
    intent_revision_id: str | None = None
    actual_revision_id: str | None = None
    detected_at: AwareDatetime
    disposition: Disposition


class BaselineVersion(BaseModel):
    """The recorded environment a probe's baseline output was captured under."""

    model_config = _CONFIG

    id: str
    probe_definition_revision_id: str
    environment_id: str
    model_provider_version: str | None = None
    retrieval_dataset_version: str | None = None
    tool_schema_version: str | None = None
    feature_flags: str | None = None
    judge_model: str | None = None
    judge_policy: str | None = None
    fixture_version: str | None = None
    redaction_policy: str | None = None
    recorded_output: str | None = None
    fingerprint: str | None = None
    created_at: AwareDatetime
    approved_at: AwareDatetime | None = None


class ReviewItem(BaseModel):
    """One knowledge item inside a review batch."""

    model_config = _CONFIG

    id: str
    review_batch_id: str
    item_id: str
    proposed_revision_id: str | None = None
    resolution: ReviewResolution | None = None


class Escalation(BaseModel):
    """A question the system could not answer, and what happened to it."""

    model_config = _CONFIG

    id: str
    system_id: str
    question: str | None = None
    branch: EscalationBranch
    prior_revision_id: str | None = None
    status: EscalationStatus
    channel: EscalationChannel | None = None
    owner_actor_id: str | None = None
    answered_by: str | None = None
    candidate_revision_id: str | None = None
    opened_at: AwareDatetime
    answered_at: AwareDatetime | None = None


class ProbeRun(BaseModel):
    """One execution of a probe revision."""

    model_config = _CONFIG

    id: str
    probe_definition_revision_id: str
    baseline_version_id: str | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    outcome: ProbeOutcome
    cleanup_verified: bool = False


class ProbeObservation(BaseModel):
    """What a probe run observed."""

    model_config = _CONFIG

    id: str
    probe_run_id: str
    output: str | None = None
    fingerprint: str | None = None
    similarity: float | None = None
    judge_verdict: str | None = None
