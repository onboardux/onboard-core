"""`resolve_freshness`: multi-level, load-bearing propagation, sensor override.

Implemented in S4. Contracts §6, implementation spec §4.9.

**The invariants this package carries.** It writes nothing -- the port it runs on
has no write method, so that is structural rather than a rule. `is_load_bearing`
defaults to `1`, so an unset writer errs toward staleness. And connector silence
is never read as stability: a sensor past its cadence, or one that has never
reported at all, is `STALE`, not healthy.
"""

from adopt_freshness.records import FreshnessRecords
from adopt_freshness.resolve import (
    FRESHNESS_LEVELS,
    RULE_BINDING_RETIRED,
    RULE_BINDING_STALE,
    RULE_ITEM_RETIRED,
    RULE_ITEM_STATE,
    RULE_SENSOR_MISSED_CADENCE,
    RULE_SENSOR_UNHEALTHY,
    RULE_SOURCE_IDENTITY_DEAD,
    RULE_SOURCE_IDENTITY_MOVED,
    FreshnessLevel,
    FreshnessResolution,
    resolve_freshness,
    sensor_effective_health,
)

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
    "FreshnessRecords",
    "FreshnessResolution",
    "resolve_freshness",
    "sensor_effective_health",
]
