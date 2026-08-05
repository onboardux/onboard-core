"""The G0 fixture store: one populated row in **every** exportable table.

CUJ-6 step 1 says *"a fully populated store covering every exportable table"*,
and that word is the whole point. A round trip proven over the seven tables that
happen to have a facade proves the round trip for seven tables; the promise is
about the bundle a client keeps, and a client's bundle carries all thirty-six.

**Rows are written as validated models rather than through the facades**, and the
reason is worth stating so it is not read as a shortcut. Twenty-nine of the
thirty-six tables have no facade at all -- they belong to items 8 through 12 --
so a facade-only fixture could not exist. Writing one anyway, to satisfy a
fixture, would be inventing the semantics of `escalation` and `review_item` two
builds before the sprint that owns them. What G0 asserts is a property of the
*bundle*: that whatever a store holds survives export and import unchanged. The
facades are exercised by CUJ-1 and CUJ-2, which is where facade behaviour
belongs.

The scope chain is the exception and does go through `ScopeFacade`, because
`system_lifecycle_event` exists only as the side effect of a real state change
(implementation spec §4.5) and a hand-written one would be a row no code path
produces.

Every timestamp comes from an injected `ManualClock`; nothing here sleeps or
reads the wall clock.
"""

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Final

from pydantic import BaseModel

from adopt_identity import build_uri
from adopt_model import MODEL_FOR_TABLE
from adopt_obs import ManualClock, new_id
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

__all__ = ["FIXTURE_START", "build_fixture_store"]

#: A fixed instant. Every row derives from it, so a fixture built twice in one
#: process differs only in its ULIDs -- which is what the round trip compares
#: against itself, never across runs.
FIXTURE_START: Final[_dt.datetime] = _dt.datetime(2026, 8, 5, 12, 0, 0, tzinfo=_dt.UTC)

_FIRM_SLUG: Final[str] = "northwind"
_ENGAGEMENT_SLUG: Final[str] = "acme-erp"
_SYSTEM_SLUG: Final[str] = "orders-api"
_ENVIRONMENT_SLUG: Final[str] = "prod"


class _Writer:
    """Insert validated models, one table at a time, inside one transaction."""

    def __init__(self, handle: SqliteStoreHandle, clock: ManualClock) -> None:
        self._records = handle.import_records()
        self._clock = clock
        self.rows: dict[str, list[BaseModel]] = {}

    def now(self) -> _dt.datetime:
        return self._clock.now()

    def add(self, table: str, **values: Any) -> BaseModel:
        """Build one row from the generated model and remember it.

        The model is the authority on the columns, so a fixture naming a column
        the schema does not have fails here rather than producing a bundle that
        disagrees with `export.schema.json`.
        """
        model = MODEL_FOR_TABLE[table].model_validate(values)
        self.rows.setdefault(table, []).append(model)
        return model

    def flush(self) -> None:
        for table, models in self.rows.items():
            self._records.insert_rows(table, models)


@dataclass(frozen=True, slots=True)
class _Ids:
    """The four scope ids, flattened once so no row has to walk the chain."""

    firm: str
    engagement: str
    system: str
    environment: str


def _flatten(scope: Scope) -> _Ids:
    """A four-level scope's ids, refusing a chain that did not resolve fully.

    `Scope` guarantees only `firm` (a partial path is a legitimate resolution),
    so the fixture asserts its own precondition rather than letting a `None`
    become a `NOT NULL` violation four tables later.
    """
    if scope.engagement is None or scope.system is None or scope.environment is None:
        raise AssertionError(f"the fixture scope did not resolve to four levels: {scope.path()}")
    return _Ids(
        firm=scope.firm.id,
        engagement=scope.engagement.id,
        system=scope.system.id,
        environment=scope.environment.id,
    )


def _seed_scope(handle: SqliteStoreHandle) -> Scope:
    """The `02` §4 worked example, created through the real facade."""
    facade = handle.scope()
    firm = facade.create_firm(slug=_FIRM_SLUG, name="Northwind LLP")
    engagement = facade.create_engagement(
        firm_id=firm.id, slug=_ENGAGEMENT_SLUG, name="ACME ERP rollout"
    )
    system = facade.create_system(engagement_id=engagement.id, slug=_SYSTEM_SLUG, name="Orders API")
    facade.create_environment(system_id=system.id, slug=_ENVIRONMENT_SLUG, name="Production")
    # A real lifecycle transition, so `system_lifecycle_event` holds the row the
    # only code path that writes it produced.
    facade.transition(system_id=system.id, to_state="LIVE", reason="pilot signed off")
    return facade.resolve(f"{_FIRM_SLUG}/{_ENGAGEMENT_SLUG}/{_SYSTEM_SLUG}/{_ENVIRONMENT_SLUG}")


def _seed_rest(writer: _Writer, scope: Scope) -> None:
    """One row per remaining exportable table, in foreign-key order.

    Deliberately linear and explicit. A loop over the manifest inventing values
    per column type would produce rows that satisfy the DDL and mean nothing --
    and the first thing it would get wrong is the enum vocabularies, which are
    the part a round trip most needs to carry intact.
    """
    ids = _flatten(scope)
    now = writer.now()
    later = now + _dt.timedelta(minutes=5)

    classifier = writer.add(
        "classifier_version",
        id=new_id("clsv"),
        version_label="cascade-2026.08",
        training_data_categories="synthetic",
        model_card_ref=None,
        released_at=now,
        retired_at=None,
    )

    writer.add(
        "approval",
        id=new_id("apr"),
        firm_id=ids.firm,
        engagement_id=ids.engagement,
        subject_type="knowledge_revision",
        subject_id=new_id("krev"),
        actor_id="actor:fde",
        approved_at=now,
        scope_note="handover pack",
        expires_at=later,
    )
    writer.add(
        "connector",
        id=new_id("conn"),
        system_id=ids.system,
        mode="local_only",
        registered_at=now,
        last_seen_at=now,
        version="0.3.0",
        status="active",
    )
    writer.add(
        "ownership_assignment",
        id=new_id("own"),
        system_id=ids.system,
        engagement_id=ids.engagement,
        scope="system",
        actor_or_group_id="group:platform",
        is_group=True,
        effective_from=now,
        effective_to=None,
        assigned_by="actor:fde",
        assignment_reason="handover",
    )
    writer.add(
        "audit_event",
        id=new_id("aud"),
        firm_id=ids.firm,
        system_id=ids.system,
        event_type="export.requested",
        actor_id="actor:fde",
        subject_ref="bundle",
        detail=None,
        occurred_at=now,
    )
    writer.add(
        "value_baseline",
        id=new_id("vb"),
        system_id=ids.system,
        captured_at=now,
        metric="question_minutes",
        value=42.5,
        unit="minutes",
        confidence_label="measured",
        captured_by="actor:fde",
        method_note=None,
    )
    writer.add(
        "value_event",
        id=new_id("ve"),
        system_id=ids.system,
        occurred_at=now,
        event_type="question.received",
        minutes=3.5,
        actor_id="actor:fde",
        source_ref=None,
        confidence_label="modelled",
    )
    writer.add(
        "observability_boundary",
        id=new_id("ob"),
        system_id=ids.system,
        environment_id=ids.environment,
        tier="T2",
        covered="endpoints",
        not_covered="internal jobs",
        knowledge_plane_location="customer",
        control_plane_location="customer",
        # The one `json` column in schema version 3, and therefore the one place
        # the canonical key ordering is exercised by real data.
        permitted_outbound_categories=["metadata_only", "aggregate_metrics"],
        last_successful_observation_at=now,
        safe_probe_status="verified",
        owner_actor_id="actor:fde",
        contractual_approval_ref="MSA-2026-11",
        declared_at=now,
        contractual=True,
    )

    # -- identity and its revision chain ---------------------------------
    identity_id = new_id("idn")
    writer.add(
        "identity",
        id=identity_id,
        uri=build_uri(scope, "endpoint", None, "POST /v1/orders"),
        firm_id=ids.firm,
        engagement_id=ids.engagement,
        system_id=ids.system,
        environment_id=ids.environment,
        identity_kind="endpoint",
        namespace=None,
        local_key="POST /v1/orders",
        first_seen=now,
        last_seen=now,
        covered_cache=True,
        covered_cache_at=now,
        retention_policy_id=None,
    )
    identity_revision_id = new_id("irev")
    writer.add(
        "identity_revision",
        id=identity_revision_id,
        identity_id=identity_id,
        extractor="fixture",
        extractor_version="1",
        source_version="abc123",
        confidence=0.99,
        alias_of_identity_id=None,
        status="active",
        supersedes_revision_id=None,
        created_at=now,
        created_by_actor_id="actor:fde",
    )

    # -- knowledge and its revision chain --------------------------------
    item_id = new_id("ki")
    knowledge_revision_id = new_id("krev")
    writer.add(
        "knowledge_item",
        id=item_id,
        firm_id=ids.firm,
        engagement_id=ids.engagement,
        system_id=ids.system,
        environment_id=ids.environment,
        kind="answer",
        title="How orders are submitted",
        current_revision_id=knowledge_revision_id,
        freshness_state="fresh",
        data_residency_region="eu-west-1",
        retention_policy_id=None,
        created_at=now,
        updated_at=now,
    )
    writer.add(
        "knowledge_revision",
        id=knowledge_revision_id,
        item_id=item_id,
        body_md="Submit a POST to `/v1/orders`.",
        recipe_json=None,
        authority_class="artifact_observed",
        verification="verified",
        confidence=0.95,
        snapshot_date=now,
        source_version="abc123",
        classifier_version_id=classifier.id,  # type: ignore[attr-defined]
        supersedes_revision_id=None,
        created_at=now,
        created_by_actor_id="actor:fde",
    )
    writer.add("death_condition", item_id=item_id, condition="referent_retired", threshold=None)
    writer.add("audience_tag", item_id=item_id, audience="engineering")
    writer.add(
        "provenance",
        id=new_id("prov"),
        revision_id=knowledge_revision_id,
        source_type="commit",
        source_ref="abc123",
        observed_at=now,
    )

    # -- binding and its revision chain ----------------------------------
    binding_id = new_id("bnd")
    binding_revision_id = new_id("brev")
    writer.add(
        "binding",
        id=binding_id,
        item_id=item_id,
        identity_id=identity_id,
        current_revision_id=binding_revision_id,
        is_load_bearing=True,
        freshness_state="fresh",
        created_at=now,
    )
    writer.add(
        "binding_revision",
        id=binding_revision_id,
        binding_id=binding_id,
        extractor="fixture",
        extractor_version="1",
        confidence=0.9,
        locator_rung=2,
        status="active",
        supersedes_revision_id=None,
        created_at=now,
        created_by_actor_id="actor:fde",
    )
    writer.add(
        "conflict",
        id=new_id("cf"),
        identity_id=identity_id,
        intent_revision_id=knowledge_revision_id,
        actual_revision_id=None,
        detected_at=now,
        disposition="open",
    )

    # -- sensing ----------------------------------------------------------
    sensor_id = new_id("sen")
    writer.add(
        "sensor",
        id=sensor_id,
        system_id=ids.system,
        environment_id=ids.environment,
        kind="ci",
        health="HEALTHY",
        expected_cadence_seconds=3600,
        last_attempted_at=now,
        last_success_at=now,
        last_event_at=now,
        credential_expires_at=later,
        safe_path_verified_at=now,
        observed_volume_baseline=12.0,
        missing_event_threshold=3,
        degradation_reason=None,
        owner_actor_id="actor:fde",
        remediation_status=None,
    )
    writer.add(
        "sensor_heartbeat",
        sensor_id=sensor_id,
        observed_at=now,
        outcome="success",
        detail=None,
    )

    change_event_id = new_id("ce")
    writer.add(
        "change_event",
        id=change_event_id,
        system_id=ids.system,
        environment_id=ids.environment,
        source="artifact",
        detected_at=now,
        referent="POST /v1/orders",
        batch_key="pr-4711",
        raw=None,
    )
    writer.add(
        "classification",
        id=new_id("cls"),
        change_event_id=change_event_id,
        identity_id=identity_id,
        # The one column whose Python field name differs from its column name:
        # `class` is a keyword, so the model calls it `class_` with an alias.
        **{"class": "BINDING_INTACT_RENDER_ONLY"},
        confidence=0.97,
        decided_by="cascade_step_1",
        classifier_version_id=classifier.id,  # type: ignore[attr-defined]
        evidence="whitespace only",
        acted_silently=True,
        sampled_for_audit=True,
        audit_verdict="agreed",
        created_at=now,
    )
    writer.add(
        "silent_repair_eligibility",
        id=new_id("sre"),
        system_id=ids.system,
        environment_id=ids.environment,
        archetype="web",
        identity_kind="endpoint",
        extractor_version="1",
        classifier_version_id=classifier.id,  # type: ignore[attr-defined]
        state="DETECTION_ONLY",
        observed_event_count=12,
        precision_ci_lower=0.96,
        state_changed_at=now,
        state_reason="below minimum labelled events",
    )

    # -- probes -----------------------------------------------------------
    probe_id = new_id("pd")
    probe_revision_id = new_id("pdrev")
    writer.add(
        "probe_definition",
        id=probe_id,
        system_id=ids.system,
        environment_id=ids.environment,
        name="orders smoke",
        current_revision_id=probe_revision_id,
        schedule_cron="0 * * * *",
        created_at=now,
    )
    writer.add(
        "probe_definition_revision",
        id=probe_revision_id,
        probe_definition_id=probe_id,
        interaction="POST /v1/orders",
        safe_path="sandbox",
        diff_method="exact",
        status="active",
        capability_manifest='{"deny_by_default":true}',
        artifact_signature=None,
        approved_by="actor:fde",
        approved_at=now,
        approval_expires_at=later,
        supersedes_revision_id=None,
        created_at=now,
    )
    baseline_id = new_id("bv")
    writer.add(
        "baseline_version",
        id=baseline_id,
        probe_definition_revision_id=probe_revision_id,
        environment_id=ids.environment,
        model_provider_version=None,
        retrieval_dataset_version=None,
        tool_schema_version="v1",
        feature_flags=None,
        judge_model=None,
        judge_policy=None,
        fixture_version="1",
        redaction_policy="strict",
        recorded_output="201 Created",
        fingerprint="sha256:abc",
        created_at=now,
        approved_at=now,
    )
    probe_run_id = new_id("prun")
    writer.add(
        "probe_run",
        id=probe_run_id,
        probe_definition_revision_id=probe_revision_id,
        baseline_version_id=baseline_id,
        started_at=now,
        finished_at=later,
        outcome="success",
        cleanup_verified=True,
    )
    writer.add(
        "probe_observation",
        id=new_id("pobs"),
        probe_run_id=probe_run_id,
        output="201 Created",
        fingerprint="sha256:abc",
        similarity=1.0,
        judge_verdict=None,
    )

    # -- review and escalation -------------------------------------------
    batch_id = new_id("rb")
    writer.add(
        "review_batch",
        id=batch_id,
        system_id=ids.system,
        batch_key="pr-4711",
        item_count=1,
        draft_md="One item changed.",
        owner_actor_id="actor:fde",
        opened_at=now,
        resolved_at=later,
        resolution="confirmed",
        review_minutes=4.0,
    )
    writer.add(
        "review_item",
        id=new_id("ri"),
        review_batch_id=batch_id,
        item_id=item_id,
        proposed_revision_id=knowledge_revision_id,
        resolution="confirmed",
    )
    writer.add(
        "escalation",
        id=new_id("esc"),
        system_id=ids.system,
        question="Does the endpoint still accept partial orders?",
        branch="ungrounded",
        prior_revision_id=knowledge_revision_id,
        status="open",
        channel="slack",
        owner_actor_id="actor:fde",
        answered_by=None,
        candidate_revision_id=None,
        opened_at=now,
        answered_at=None,
    )


def build_fixture_store(handle: SqliteStoreHandle, clock: ManualClock) -> Scope:
    """Populate ``handle`` across every exportable table. Returns the scope."""
    scope = _seed_scope(handle)
    writer = _Writer(handle, clock)
    _seed_rest(writer, scope)
    with handle.backend.transaction():
        writer.flush()
    return scope
