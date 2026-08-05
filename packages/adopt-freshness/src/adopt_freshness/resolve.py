"""`resolve_freshness` -- multi-level, load-bearing propagation, sensor override.

**This module writes nothing.** Contracts §6 says so and implementation spec §4.9
repeats it, and the port beneath it offers no write method at all, so the
guarantee is structural rather than remembered.

Three rules decide, and each exists because its absence was a specific failure:

**Load-bearing propagation.** One item may bind several identities. A change to
one of them does *not* stale the item unless that binding is load-bearing (PRD
F8.3). Blanket propagation was the previous implicit behaviour, and one shared
utility touching 200 items would have staled all 200 -- mass false staleness,
which trains people to ignore staleness. `is_load_bearing` defaults to `1`, so a
writer that forgets errs toward staleness and never toward false confidence.

**The sensor-health override.** A sensor outside `HEALTHY` blocks `fresh` and the
system reads `observation_stale` (PRD F8.4). `observation_stale` is a distinct
`freshness_state` from `stale` because the causes and the remedies differ: one
says the knowledge is out of date, the other says *we cannot currently tell*.

**Connector silence is never stability.** A sensor that has stopped reporting
past `SENSOR_MISSED_CADENCE_MULTIPLIER x expected_cadence_seconds` is treated as
sensor health `STALE`, and so is one that has never reported at all. The
alternative -- no heartbeat, no problem -- is the reading that makes a dead
connector look like a quiet system.

**Precedence, and why the override is not simply first.** A retired item is
terminal and no sensor changes that. A genuinely stale item stays `stale`, which
is stronger and more actionable than `observation_stale`. The override therefore
bites exactly where contracts §6 says it does: on an item that would otherwise
resolve `fresh`.
"""

import datetime as _dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from adopt_const import SENSOR_MISSED_CADENCE_MULTIPLIER
from adopt_freshness.records import FreshnessRecords
from adopt_model import Binding, KnowledgeItem, Sensor
from adopt_model._enums import FreshnessState
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock

__all__ = [
    "FRESHNESS_LEVELS",
    "RULE_BINDING_RETIRED",
    "RULE_BINDING_STALE",
    "RULE_ITEM_RETIRED",
    "RULE_ITEM_STATE",
    "RULE_SENSOR_MISSED_CADENCE",
    "RULE_SENSOR_UNHEALTHY",
    "RULE_SOURCE_IDENTITY_DEAD",
    "RULE_SOURCE_IDENTITY_MOVED",
    "FreshnessLevel",
    "FreshnessResolution",
    "resolve_freshness",
    "sensor_effective_health",
]

#: The four levels Build 0 resolves. PRD F8.1 names five; fleet-summary is a
#: query *over* these and is item 12's, not a level this function returns.
FreshnessLevel = Literal["source", "binding", "knowledge_revision", "system"]

FRESHNESS_LEVELS: Final[tuple[FreshnessLevel, ...]] = (
    "source",
    "binding",
    "knowledge_revision",
    "system",
)

#: Deciding rules. Stable strings: they reach `adopt freshness resolve --json`,
#: and a state without the reason that produced it is not actionable.
RULE_ITEM_RETIRED: Final[str] = "item_retired"
RULE_SOURCE_IDENTITY_DEAD: Final[str] = "load_bearing_identity_dead"
RULE_SOURCE_IDENTITY_MOVED: Final[str] = "load_bearing_identity_moved"
RULE_BINDING_RETIRED: Final[str] = "load_bearing_binding_retired"
RULE_BINDING_STALE: Final[str] = "load_bearing_binding_stale"
RULE_SENSOR_UNHEALTHY: Final[str] = "sensor_health_override"
RULE_SENSOR_MISSED_CADENCE: Final[str] = "sensor_missed_cadence"
RULE_ITEM_STATE: Final[str] = "item_state"

_HEALTHY: Final[str] = "HEALTHY"
_SENSOR_STALE: Final[str] = "STALE"
_IDENTITY_DEAD: Final[str] = "dead"
_IDENTITY_MOVED: Final[str] = "moved"
_BINDING_RETIRED: Final[str] = "retired"

_FRESH: Final[FreshnessState] = "fresh"
_STALE: Final[FreshnessState] = "stale"
_RETIRED: Final[FreshnessState] = "retired"
_OBSERVATION_STALE: Final[FreshnessState] = "observation_stale"


@dataclass(frozen=True, slots=True)
class FreshnessResolution:
    """What `resolve_freshness` returns: the state, the level, and the rule.

    The rule is not decoration. CUJ-5 requires an operator to be told *why*
    knowledge stopped being fresh, and `adopt freshness resolve --json` reports
    it (contracts §14). A state with no reason cannot be acted on and cannot be
    argued with.
    """

    item_id: str
    state: FreshnessState
    level: FreshnessLevel
    deciding_rule: str
    #: Set when the sensor override fired, so a caller can name the channel.
    sensor_id: str | None = None


def sensor_effective_health(
    sensor: Sensor, *, now: _dt.datetime, last_heartbeat_at: _dt.datetime | None
) -> str:
    """A sensor's health once silence is taken into account.

    A stored health other than `HEALTHY` is returned unchanged -- the channel has
    already said something is wrong. A `HEALTHY` sensor is then checked against
    its cadence:

    * no `expected_cadence_seconds` -> the check is **disabled**, and the sensor
      is reported as it stands. This is why `store doctor` raises a finding for a
      NULL cadence: the silent failure mode is the check quietly not running.
    * a cadence, and no heartbeat ever -> `STALE`. A channel that has never
      reported is not a channel that is working.
    * a cadence, and the last heartbeat older than
      `SENSOR_MISSED_CADENCE_MULTIPLIER x` it -> `STALE`.
    """
    if sensor.health != _HEALTHY:
        return sensor.health
    if sensor.expected_cadence_seconds is None:
        return sensor.health
    deadline = _dt.timedelta(
        seconds=sensor.expected_cadence_seconds * SENSOR_MISSED_CADENCE_MULTIPLIER
    )
    if last_heartbeat_at is None:
        return _SENSOR_STALE
    return _SENSOR_STALE if now - last_heartbeat_at > deadline else _HEALTHY


def _first_unhealthy(
    sensors: Sequence[Sensor],
    *,
    now: _dt.datetime,
    heartbeats: Mapping[str, _dt.datetime],
) -> tuple[Sensor, str] | None:
    """The first sensor in scope that is not healthy, and why.

    Ordered by sensor id so two runs over one store agree, and returning the
    *first* rather than all of them because the override is binary: one degraded
    channel is enough, and listing the rest would suggest the answer depends on
    how many.
    """
    for sensor in sorted(sensors, key=lambda row: row.id):
        effective = sensor_effective_health(
            sensor, now=now, last_heartbeat_at=heartbeats.get(sensor.id)
        )
        if effective != _HEALTHY:
            return sensor, effective
    return None


def _load_bearing_blocker(
    bindings: Sequence[Binding],
    *,
    binding_statuses: Mapping[str, str],
    identity_statuses: Mapping[str, str],
) -> tuple[FreshnessLevel, str] | None:
    """The source- or binding-level rule that stales the item, if any.

    **Only load-bearing bindings are consulted** (PRD F8.3). Bindings are read in
    id order so the reported rule is deterministic when several would fire.

    Source level is checked before binding level for each binding, because a dead
    or moved referent is a fact about the world and a retired binding is a fact
    about our record of it -- and telling an operator "the endpoint is gone"
    beats telling them "we stopped tracking it".
    """
    for binding in sorted(bindings, key=lambda row: row.id):
        if not binding.is_load_bearing:
            continue

        identity_status = identity_statuses.get(binding.identity_id)
        if identity_status == _IDENTITY_DEAD:
            return "source", RULE_SOURCE_IDENTITY_DEAD
        if identity_status == _IDENTITY_MOVED:
            return "source", RULE_SOURCE_IDENTITY_MOVED

        if binding_statuses.get(binding.id) == _BINDING_RETIRED:
            return "binding", RULE_BINDING_RETIRED
        if binding.freshness_state in (_STALE, _OBSERVATION_STALE):
            return "binding", RULE_BINDING_STALE

    return None


def resolve_freshness(
    records: FreshnessRecords,
    item_id: str,
    *,
    clock: Clock | None = None,
) -> FreshnessResolution:
    """Resolve one item's freshness across four levels. **Pure read.**

    Args:
        records: The read port. Supplied rather than reached for, so the
            propagation property can drive generated fixtures.
        item_id: The knowledge item to resolve.
        clock: Injected clock; tests pass `ManualClock`. Never `sleep`.

    Returns:
        The resolved state, the level that decided it and the deciding rule.

    Raises:
        AdoptError: ``SCOPE_VIOLATION`` when `item_id` names no item. A missing
            item is not "fresh by default": returning a state for something that
            does not exist is how an absent record becomes a confident answer.
    """
    now = (clock if clock is not None else SystemClock()).now()

    item: KnowledgeItem | None = records.get_item(item_id)
    if item is None:
        raise AdoptError(
            ErrorCode.SCOPE_VIOLATION,
            message=f"{item_id} names no knowledge item",
            hint="Resolve an item that exists. Freshness is a statement about a record, "
            "and there is no honest state to report for one that is absent.",
        )

    # Knowledge-revision level, terminal. A retired item stays retired: no sensor
    # and no binding change makes a withdrawn answer relevant again.
    if item.freshness_state == _RETIRED:
        return FreshnessResolution(
            item_id=item_id,
            state=_RETIRED,
            level="knowledge_revision",
            deciding_rule=RULE_ITEM_RETIRED,
        )

    bindings = records.bindings_for_item(item_id)
    blocker = _load_bearing_blocker(
        bindings,
        binding_statuses=records.head_binding_statuses([row.id for row in bindings]),
        identity_statuses=records.head_identity_statuses([row.identity_id for row in bindings]),
    )
    if blocker is not None:
        level, rule = blocker
        return FreshnessResolution(item_id=item_id, state=_STALE, level=level, deciding_rule=rule)

    # System level. The override blocks `fresh` and nothing else, which is
    # exactly what contracts §6 says: "affected knowledge may not resolve
    # `fresh`". An `unverified` item is already not claiming to be current, so
    # overriding it would replace one honest state with another and lose the
    # distinction the vocabulary exists to carry.
    if item.freshness_state == _FRESH:
        sensors = records.sensors_in_scope(
            system_id=item.system_id, environment_id=item.environment_id
        )
        unhealthy = _first_unhealthy(
            sensors,
            now=now,
            heartbeats=records.latest_heartbeat_at([row.id for row in sensors]),
        )
        if unhealthy is not None:
            sensor, effective = unhealthy
            rule = (
                RULE_SENSOR_MISSED_CADENCE
                if effective == _SENSOR_STALE and sensor.health == _HEALTHY
                else RULE_SENSOR_UNHEALTHY
            )
            return FreshnessResolution(
                item_id=item_id,
                state=_OBSERVATION_STALE,
                level="system",
                deciding_rule=rule,
                sensor_id=sensor.id,
            )

    return FreshnessResolution(
        item_id=item_id,
        state=item.freshness_state,
        level="knowledge_revision",
        deciding_rule=RULE_ITEM_STATE,
    )
