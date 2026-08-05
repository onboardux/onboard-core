"""`resolve_freshness` -- the level/rule matrix and the sensor-health override.

*Fails when* a level stops being consulted, when the deciding rule stops matching
the state it produced, or when connector silence starts reading as stability.
*Matters because* freshness is what the Answer path qualifies or refuses on: an
item wrongly reported `fresh` is a confident wrong answer, which is the one
failure mode this substrate exists to make impossible. *No other instrument
catches it because* the propagation property proves *how many* items stale
without proving *why*, and the CUJs walk one path each.

**No test here sleeps.** Every cadence case advances a `ManualClock`
(implementation spec §5).
"""

import datetime as _dt
from collections.abc import Callable
from dataclasses import replace

import pytest

from adopt_const import SENSOR_MISSED_CADENCE_MULTIPLIER
from adopt_freshness import (
    RULE_BINDING_RETIRED,
    RULE_BINDING_STALE,
    RULE_ITEM_RETIRED,
    RULE_ITEM_STATE,
    RULE_SENSOR_MISSED_CADENCE,
    RULE_SENSOR_UNHEALTHY,
    RULE_SOURCE_IDENTITY_DEAD,
    resolve_freshness,
)
from adopt_obs import AdoptError, ManualClock
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft, doctor
from adopt_store.api import SqliteStoreHandle

#: A cadence short enough that the tests read clearly. Not a tunable: the tunable
#: is the multiplier, and it is imported.
# const-sync: ok -- a fixture cadence, chosen for legibility, not a product value.
_CADENCE_SECONDS = 600


def _bound_item(
    store: SqliteStoreHandle, scope: Scope, *, key: str, is_load_bearing: bool = True
) -> tuple[str, str, str]:
    """`(item_id, binding_id, identity_id)` for one item bound to one identity."""
    identity = store.identities().observe(scope=scope, kind="endpoint", namespace=None, key=key)
    item_id, _ = store.items().create(
        scope=scope,
        kind="answer",
        title=f"About {key}",
        revision=KnowledgeRevisionDraft(authority_class="human_confirmed", body_md="v1"),
    )
    binding_id, _ = store.bindings().create(
        item_id=item_id,
        identity_id=identity.id,
        is_load_bearing=is_load_bearing,
        revision=BindingRevisionDraft(status="active", locator_rung=1),
    )
    return item_id, binding_id, identity.id


@pytest.mark.unit
class TestTheLevelAndRuleMatrix:
    def test_an_untouched_item_resolves_its_own_state_at_the_revision_level(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope, s4_clock: ManualClock
    ) -> None:
        """The control: nothing has happened, so nothing overrides."""
        item_id, _, _ = _bound_item(s4_store, s4_scope, key="POST /v1/orders")

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "unverified"
        assert resolution.level == "knowledge_revision"
        assert resolution.deciding_rule == RULE_ITEM_STATE

    def test_a_retired_item_is_terminal(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_item_freshness: Callable[..., None],
    ) -> None:
        """No sensor and no binding change makes a withdrawn answer relevant
        again, which is why this is checked before everything else."""
        item_id, binding_id, _ = _bound_item(s4_store, s4_scope, key="POST /v1/orders")
        set_item_freshness(item_id=item_id, state="retired")
        s4_store.bindings().retire(binding_id=binding_id, reason="gone too")

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "retired"
        assert resolution.level == "knowledge_revision"
        assert resolution.deciding_rule == RULE_ITEM_RETIRED

    def test_a_dead_load_bearing_identity_stales_at_the_source_level(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope, s4_clock: ManualClock
    ) -> None:
        item_id, _, identity_id = _bound_item(s4_store, s4_scope, key="POST /v1/orders")
        s4_store.identities().retire(identity_id=identity_id, reason="endpoint removed")

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "stale"
        assert resolution.level == "source"
        assert resolution.deciding_rule == RULE_SOURCE_IDENTITY_DEAD

    def test_a_retired_load_bearing_binding_stales_at_the_binding_level(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope, s4_clock: ManualClock
    ) -> None:
        item_id, binding_id, _ = _bound_item(s4_store, s4_scope, key="POST /v1/orders")
        s4_store.bindings().retire(binding_id=binding_id, reason="no longer describes it")

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "stale"
        assert resolution.level == "binding"
        assert resolution.deciding_rule == RULE_BINDING_RETIRED

    def test_a_stale_load_bearing_binding_stales_the_item(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_binding_freshness: Callable[..., None],
    ) -> None:
        """`binding.freshness_state` is per-binding, not only per-item
        (PRD F8.2), so the binding's own state has to be able to decide."""
        item_id, binding_id, _ = _bound_item(s4_store, s4_scope, key="POST /v1/orders")
        set_binding_freshness(binding_id=binding_id, state="stale")

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "stale"
        assert resolution.level == "binding"
        assert resolution.deciding_rule == RULE_BINDING_STALE

    def test_a_non_load_bearing_binding_never_stales_the_item(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_binding_freshness: Callable[..., None],
    ) -> None:
        """**The rule CUJ-4 exists for.** Blanket propagation would mass-false-
        stale on shared referents -- one shared utility touching 200 items would
        stale all 200, which trains people to ignore staleness entirely."""
        item_id, binding_id, identity_id = _bound_item(
            s4_store, s4_scope, key="POST /v1/orders", is_load_bearing=False
        )
        set_binding_freshness(binding_id=binding_id, state="stale")
        s4_store.identities().retire(identity_id=identity_id, reason="endpoint removed")

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "unverified"
        assert resolution.deciding_rule == RULE_ITEM_STATE

    def test_resolving_an_absent_item_refuses_rather_than_defaulting(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope, s4_clock: ManualClock
    ) -> None:
        """Returning a state for something that does not exist is how an absent
        record becomes a confident answer."""
        with pytest.raises(AdoptError) as caught:
            resolve_freshness(s4_store.freshness_records(), "ki_missing", clock=s4_clock)

        assert caught.value.code == "SCOPE_VIOLATION"

    def test_resolve_writes_nothing(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_item_freshness: Callable[..., None],
    ) -> None:
        """Contracts §6: *"The function writes nothing."* A resolver that wrote
        its own conclusion back would make the stored state unfalsifiable."""
        item_id, binding_id, _ = _bound_item(s4_store, s4_scope, key="POST /v1/orders")
        set_item_freshness(item_id=item_id, state="fresh")
        s4_store.bindings().retire(binding_id=binding_id, reason="gone")

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "stale"
        stored = s4_store.items().get(item_id)
        assert stored is not None
        assert stored.freshness_state == "fresh", "resolve persisted its own conclusion"


@pytest.mark.unit
class TestSensorFacadeRefusals:
    """The writes `SensorFacade` refuses, and why each refusal exists.

    *Fails when* a sensor can be registered without an environment, or a
    heartbeat recorded against a channel nothing knows about. *Matters because*
    both produce rows the freshness override then reads: a sensor with no
    environment gates nothing and looks like coverage, and an orphan heartbeat is
    evidence attached to no claim. *No other instrument catches it because* the
    override tests all start from a well-formed sensor.
    """

    def test_a_sensor_needs_a_system_and_an_environment(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """A sensor that did not say which environment it watches would gate the
        freshness of knowledge it never observed."""
        firm_only = replace(s4_scope, engagement=None, system=None, environment=None)

        with pytest.raises(AdoptError) as caught:
            s4_store.sensors().register(scope=firm_only, kind="webhook")

        assert caught.value.code == "SCOPE_VIOLATION"

    def test_a_heartbeat_for_an_unknown_sensor_is_refused(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        with pytest.raises(AdoptError) as caught:
            s4_store.sensors().heartbeat(sensor_id="sen_missing", outcome="success")

        assert caught.value.code == "SCOPE_VIOLATION"

    def test_degrading_an_unknown_sensor_is_refused(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        with pytest.raises(AdoptError) as caught:
            s4_store.sensors().degrade(sensor_id="sen_missing", health="FAILED", reason="drill")

        assert caught.value.code == "SCOPE_VIOLATION"

    def test_a_new_sensor_is_unverified_rather_than_healthy(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """`HEALTHY` would be a claim about a channel that has never run, and the
        override would then read a brand-new sensor as evidence that a system is
        being watched."""
        sensor = s4_store.sensors().register(scope=s4_scope, kind="webhook")

        assert sensor.health == "UNVERIFIED"

    def test_an_empty_poll_is_a_success(self, s4_store: SqliteStoreHandle, s4_scope: Scope) -> None:
        """A poll that correctly found no changes is the channel **working**.
        Treating it as a failure would make a quiet system look like a broken
        one, and the override would fire on every well-behaved connector."""
        sensor = s4_store.sensors().register(scope=s4_scope, kind="ci")

        s4_store.sensors().heartbeat(sensor_id=sensor.id, outcome="empty")

        after = s4_store.sensors().get(sensor.id)
        assert after is not None
        assert after.health == "HEALTHY"
        assert after.last_success_at is not None

    def test_degrading_leaves_the_last_observation_timestamps_alone(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """Nothing was observed, so nothing about what was last observed changed.
        Moving them would erase how long the channel has actually been blind."""
        sensor = s4_store.sensors().register(scope=s4_scope, kind="webhook")
        s4_store.sensors().heartbeat(sensor_id=sensor.id, outcome="success", observed_event=True)
        before = s4_store.sensors().get(sensor.id)
        assert before is not None

        s4_store.sensors().degrade(
            sensor_id=sensor.id, health="FAILED", reason="credential revoked"
        )

        after = s4_store.sensors().get(sensor.id)
        assert after is not None
        assert after.last_success_at == before.last_success_at
        assert after.last_event_at == before.last_event_at
        assert after.degradation_reason == "credential revoked"


@pytest.mark.unit
class TestTheSensorHealthOverride:
    """PRD F8.4 and CUJ-5. Connector silence is never read as system stability."""

    def _fresh_item_with_sensor(
        self,
        store: SqliteStoreHandle,
        scope: Scope,
        set_item_freshness: Callable[..., None],
        *,
        cadence: int | None = _CADENCE_SECONDS,
    ) -> tuple[str, str]:
        item_id, _, _ = _bound_item(store, scope, key="POST /v1/orders")
        set_item_freshness(item_id=item_id, state="fresh")
        sensor = store.sensors().register(
            scope=scope, kind="webhook", expected_cadence_seconds=cadence
        )
        store.sensors().heartbeat(sensor_id=sensor.id, outcome="success")
        return item_id, sensor.id

    def test_a_healthy_sensor_leaves_a_fresh_item_fresh(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_item_freshness: Callable[..., None],
    ) -> None:
        """The control. Without it the override rows below would pass on a
        resolver that never returned `fresh` at all."""
        item_id, _ = self._fresh_item_with_sensor(s4_store, s4_scope, set_item_freshness)

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "fresh"
        assert resolution.deciding_rule == RULE_ITEM_STATE

    @pytest.mark.parametrize("health", ["DEGRADED", "FAILED", "DISABLED", "UNVERIFIED", "STALE"])
    def test_any_sensor_outside_healthy_blocks_fresh(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_item_freshness: Callable[..., None],
        health: str,
    ) -> None:
        """*Any* sensor, not only `DEGRADED`. The vocabulary has six values and
        five of them mean the channel is not reporting reliably."""
        item_id, sensor_id = self._fresh_item_with_sensor(s4_store, s4_scope, set_item_freshness)
        s4_store.sensors().degrade(sensor_id=sensor_id, health=health, reason="drill")  # type: ignore[arg-type]

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "observation_stale"
        assert resolution.level == "system"
        assert resolution.deciding_rule == RULE_SENSOR_UNHEALTHY
        assert resolution.sensor_id == sensor_id

    def test_a_heartbeat_just_inside_the_cadence_window_stays_fresh(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_item_freshness: Callable[..., None],
    ) -> None:
        item_id, _ = self._fresh_item_with_sensor(s4_store, s4_scope, set_item_freshness)
        s4_clock.advance(_dt.timedelta(seconds=_CADENCE_SECONDS * SENSOR_MISSED_CADENCE_MULTIPLIER))

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "fresh", "the boundary is exclusive; at the limit is inside"

    def test_a_heartbeat_past_the_cadence_window_resolves_observation_stale(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_item_freshness: Callable[..., None],
    ) -> None:
        item_id, sensor_id = self._fresh_item_with_sensor(s4_store, s4_scope, set_item_freshness)
        s4_clock.advance(
            _dt.timedelta(seconds=_CADENCE_SECONDS * SENSOR_MISSED_CADENCE_MULTIPLIER + 1)
        )

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "observation_stale"
        assert resolution.deciding_rule == RULE_SENSOR_MISSED_CADENCE
        assert resolution.sensor_id == sensor_id

    def test_a_sensor_that_has_never_reported_is_not_healthy(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_item_freshness: Callable[..., None],
    ) -> None:
        """CUJ-5's failure branch: *no heartbeat arrives at all*. A channel that
        has never reported is not a channel that is working."""
        item_id, _, _ = _bound_item(s4_store, s4_scope, key="POST /v1/orders")
        set_item_freshness(item_id=item_id, state="fresh")
        s4_store.sensors().register(
            scope=s4_scope, kind="webhook", expected_cadence_seconds=_CADENCE_SECONDS
        )

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "observation_stale"

    def test_a_null_cadence_disables_the_check_and_doctor_says_so(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_item_freshness: Callable[..., None],
    ) -> None:
        """**The silent failure this finding exists for.** With no cadence there
        is no deadline, so a channel that stopped reporting a month ago still
        reads `HEALTHY` and nothing anywhere says otherwise."""
        item_id, sensor_id = self._fresh_item_with_sensor(
            s4_store, s4_scope, set_item_freshness, cadence=None
        )
        s4_clock.advance(_dt.timedelta(days=30))

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)
        findings = doctor(s4_store)

        assert resolution.state == "fresh", "with no cadence there is nothing to be past"
        cadence_findings = [f for f in findings if f.subject_id == sensor_id]
        assert cadence_findings, findings
        assert cadence_findings[0].code == "FRESHNESS_SENSOR_DEGRADED"
        assert "expected_cadence_seconds is NULL" in cadence_findings[0].detail

    def test_the_override_does_not_touch_an_item_that_is_already_stale(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_item_freshness: Callable[..., None],
    ) -> None:
        """Contracts §6 blocks `fresh` and nothing else. `stale` is the stronger,
        more actionable statement and replacing it with `observation_stale` would
        lose the distinction the vocabulary carries."""
        item_id, binding_id, _ = _bound_item(s4_store, s4_scope, key="POST /v1/orders")
        set_item_freshness(item_id=item_id, state="fresh")
        sensor = s4_store.sensors().register(
            scope=s4_scope, kind="webhook", expected_cadence_seconds=_CADENCE_SECONDS
        )
        s4_store.sensors().degrade(sensor_id=sensor.id, health="DEGRADED", reason="drill")
        s4_store.bindings().retire(binding_id=binding_id, reason="gone")

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "stale"
        assert resolution.level == "binding"

    def test_a_sensor_in_another_environment_does_not_override(
        self,
        s4_store: SqliteStoreHandle,
        s4_scope: Scope,
        s4_clock: ManualClock,
        set_item_freshness: Callable[..., None],
    ) -> None:
        """The item names an environment, so only that environment's channels
        gate it. A store-wide override would make one degraded staging webhook
        qualify every answer in production."""
        assert s4_scope.system is not None
        item_id, _, _ = _bound_item(s4_store, s4_scope, key="POST /v1/orders")
        set_item_freshness(item_id=item_id, state="fresh")
        staging = s4_store.scope().create_environment(
            system_id=s4_scope.system.id, slug="staging", name="Staging"
        )
        other_scope = s4_store.scope().resolve("northwind/acme-erp/orders-api/staging")
        assert other_scope.environment is not None and other_scope.environment.id == staging.id
        sensor = s4_store.sensors().register(
            scope=other_scope, kind="webhook", expected_cadence_seconds=_CADENCE_SECONDS
        )
        s4_store.sensors().degrade(sensor_id=sensor.id, health="FAILED", reason="drill")

        resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

        assert resolution.state == "fresh"
