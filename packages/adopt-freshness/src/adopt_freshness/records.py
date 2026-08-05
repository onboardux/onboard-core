"""The storage port `resolve_freshness` reads through.

Declared here rather than imported from `adopt_store`, for the reason given in
`adopt_coverage.records`: `no-raw-sqlite` names `adopt_freshness` as a source
module, so a dependency on `adopt_store` would reach `sqlite3` transitively.

**Every method is a read, and there is no write on this port at all.**
`resolve_freshness` writes nothing (contracts §6, implementation spec §4.9), and
the absent methods are how that is guaranteed rather than remembered: a function
cannot write through a port that offers no way to.
"""

import datetime as _dt
from collections.abc import Mapping, Sequence
from typing import Protocol

from adopt_model import Binding, KnowledgeItem, Sensor

__all__ = ["FreshnessRecords"]


class FreshnessRecords(Protocol):
    """Row in, resolution out. No SQL, connection or cursor crosses this boundary."""

    def get_item(self, item_id: str) -> KnowledgeItem | None: ...

    def bindings_for_item(self, item_id: str) -> Sequence[Binding]:
        """Every binding on the item, load-bearing or not.

        Both are returned and the *caller* applies the load-bearing rule
        (PRD F8.3). Filtering here would move the rule into the realization,
        where the property test asserting "exactly the K load-bearing items
        stale" could not see it.
        """
        ...

    def head_binding_statuses(self, binding_ids: Sequence[str]) -> Mapping[str, str]:
        """`binding_id` -> the status of its head revision."""
        ...

    def head_identity_statuses(self, identity_ids: Sequence[str]) -> Mapping[str, str]:
        """`identity_id` -> the status of its derived head revision."""
        ...

    def sensors_in_scope(self, *, system_id: str, environment_id: str | None) -> Sequence[Sensor]:
        """Every sensor gating the item's scope.

        `environment_id` of `None` means every environment of the system, which
        is the right reading for an item that spans environments: a sensor
        degrading anywhere it draws on is a sensor that degrades it.
        """
        ...

    def latest_heartbeat_at(self, sensor_ids: Sequence[str]) -> Mapping[str, _dt.datetime]:
        """`sensor_id` -> the most recent `observed_at`, as an aware datetime.

        A sensor that has never reported is **absent from the mapping**, not
        present with a null. Silence is the condition the missed-cadence rule
        exists to catch, and representing it as a value invites a caller to
        compare it and conclude nothing is wrong.
        """
        ...
