"""`SensorFacade` -- `Store.sensors()`, contracts §10.3.

**This facade records observations. It does not make them.** Emitting a
heartbeat means running a connector, polling an audit trail or receiving a
webhook, and every one of those belongs to items 8 and 10 (PRD F8 non-goals).
Build 0 owns the tables, the write path and the health vocabulary, and a facade
that also decided *when* a heartbeat happens would be Build 0 quietly
implementing the sensing layer it explicitly defers.

**Recording a heartbeat moves the sensor's health in the same transaction.** A
heartbeat row without the health it implies is a store where the freshness
override reads one fact and the audit trail another -- and the override is the
control that stops connector silence being read as stability, so it is the one
that must not be able to disagree with its evidence.
"""

import datetime as _dt

from adopt_model import Sensor, SensorHeartbeat
from adopt_model._enums import HeartbeatOutcome, SensorHealth, SensorKind
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, new_id, truncate_to_millisecond
from adopt_scope import Scope
from adopt_store.facades.records import SensorRecords

__all__ = ["SENSOR_ID_PREFIX", "SensorFacade"]

#: Registered in `adopt_obs.ids`; `new_id` refuses anything else.
SENSOR_ID_PREFIX = "sen"

#: A newly registered sensor has reported nothing, and `UNVERIFIED` is the
#: vocabulary's word for exactly that. `HEALTHY` would be a claim about a
#: channel that has never run, and the freshness override would then read a
#: brand-new sensor as evidence that a system is being watched.
INITIAL_SENSOR_HEALTH: SensorHealth = "UNVERIFIED"

#: Which outcomes count as the sensor having successfully observed. `empty` is a
#: success: a poll that correctly found no changes is the channel working, and
#: treating it as a failure would make a quiet system look like a broken one.
_SUCCESSFUL_OUTCOMES: frozenset[HeartbeatOutcome] = frozenset({"success", "empty"})

#: The health a heartbeat implies, per outcome. `skipped` leaves the sensor
#: `DEGRADED` rather than healthy, because a skipped observation is an
#: observation that did not happen.
_HEALTH_FOR_OUTCOME: dict[HeartbeatOutcome, SensorHealth] = {
    "success": "HEALTHY",
    "empty": "HEALTHY",
    "failure": "FAILED",
    "skipped": "DEGRADED",
}


class SensorFacade:
    """`Store.sensors()` -- contracts §10.3."""

    def __init__(self, records: SensorRecords, *, clock: Clock | None = None) -> None:
        self._records = records
        self._clock: Clock = clock if clock is not None else SystemClock()

    def _now(self) -> _dt.datetime:
        return truncate_to_millisecond(self._clock.now())

    def register(
        self,
        *,
        scope: Scope,
        kind: SensorKind,
        expected_cadence_seconds: int | None = None,
        owner_actor_id: str | None = None,
    ) -> Sensor:
        """Register a change-sensing channel for one environment of one system.

        Args:
            scope: Must resolve to a system **and** an environment; both columns
                are `NOT NULL`.
            kind: The declared channel vocabulary.
            expected_cadence_seconds: How often this sensor is expected to
                report. **Optional in the schema and reported by `store doctor`
                when absent**, because a NULL cadence silently disables the
                missed-heartbeat check rather than failing loudly.
            owner_actor_id: Who is accountable for the channel.

        Returns:
            The stored row, health `UNVERIFIED`.

        Raises:
            AdoptError: ``SCOPE_VIOLATION`` when the scope lacks a system or an
                environment.
        """
        if scope.system is None or scope.environment is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message="a sensor needs a system and an environment",
                hint="Resolve the scope to `firm/engagement/system/environment`. A sensor "
                "watches one environment; one that did not say which would gate the "
                "freshness of knowledge it never observed.",
            )

        row = Sensor(
            id=new_id(SENSOR_ID_PREFIX),
            system_id=scope.system.id,
            environment_id=scope.environment.id,
            kind=kind,
            health=INITIAL_SENSOR_HEALTH,
            expected_cadence_seconds=expected_cadence_seconds,
            owner_actor_id=owner_actor_id,
        )
        with self._records.transaction():
            self._records.insert_sensor(row)
        return row

    def heartbeat(
        self,
        *,
        sensor_id: str,
        outcome: HeartbeatOutcome,
        detail: str | None = None,
        observed_event: bool = False,
    ) -> SensorHeartbeat:
        """Record one observation attempt and the health it implies.

        Args:
            sensor_id: The channel that attempted the observation.
            outcome: What happened. `empty` is a success -- a poll that correctly
                found nothing is the channel working.
            detail: A short operator-facing note. Never a body, a payload or a
                source excerpt; the logger's deny-list is the second line of
                defence, not the first.
            observed_event: Whether the attempt actually carried a change, which
                advances `last_event_at` independently of `last_success_at`.

        Returns:
            The stored heartbeat.

        Raises:
            AdoptError: ``SCOPE_VIOLATION`` when `sensor_id` names no sensor.
        """
        sensor = self._records.get_sensor(sensor_id)
        if sensor is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message=f"{sensor_id} names no sensor",
                hint="Register the sensor first. A heartbeat for an unknown channel is a "
                "heartbeat nothing can be resolved against.",
            )

        observed_at = self._now()
        succeeded = outcome in _SUCCESSFUL_OUTCOMES
        row = SensorHeartbeat(
            sensor_id=sensor_id, observed_at=observed_at, outcome=outcome, detail=detail
        )

        with self._records.transaction():
            self._records.insert_heartbeat(row)
            self._records.update_sensor_health(
                sensor_id,
                health=_HEALTH_FOR_OUTCOME[outcome],
                degradation_reason=None if succeeded else f"last outcome was {outcome}",
                last_attempted_at=observed_at,
                last_success_at=observed_at if succeeded else sensor.last_success_at,
                last_event_at=observed_at if observed_event else sensor.last_event_at,
            )
        return row

    def degrade(self, *, sensor_id: str, health: SensorHealth, reason: str) -> None:
        """Move a sensor's health without a heartbeat.

        The path an operator or a connector supervisor uses when the channel
        itself reports a problem -- a revoked credential, a disabled webhook --
        rather than an observation failing. `last_success_at` and `last_event_at`
        are deliberately left where they were: nothing was observed, so nothing
        about what was last observed has changed.

        Raises:
            AdoptError: ``SCOPE_VIOLATION`` when `sensor_id` names no sensor.
        """
        sensor = self._records.get_sensor(sensor_id)
        if sensor is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message=f"{sensor_id} names no sensor",
                hint="Register the sensor first.",
            )
        with self._records.transaction():
            self._records.update_sensor_health(
                sensor_id,
                health=health,
                degradation_reason=reason,
                last_attempted_at=self._now(),
                last_success_at=sensor.last_success_at,
                last_event_at=sensor.last_event_at,
            )

    def get(self, sensor_id: str) -> Sensor | None:
        return self._records.get_sensor(sensor_id)

    def for_scope(self, *, system_id: str, environment_id: str | None = None) -> tuple[Sensor, ...]:
        return tuple(self._records.list_sensors(system_id=system_id, environment_id=environment_id))
