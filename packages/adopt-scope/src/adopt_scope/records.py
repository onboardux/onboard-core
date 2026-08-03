"""The storage port the scope facade is written against.

Its own module so that `hierarchy` and `lifecycle` can both depend on it without
depending on each other. The alternative — a function-local import to break the
cycle — hides a real dependency from every tool that reads imports, including the
twelve contracts in `importlinter.ini`.

Implemented twice and only twice: over SQLite in `adopt_store.facades.scope`, and
over Postgres in `plane_store.facades.scope`. That is what lets the tenant-escape
suite drive the *same* facade the local CLI drives, rather than a parallel copy
whose behaviour is asserted nowhere.
"""

import datetime as _dt
from contextlib import AbstractContextManager
from typing import Protocol

from adopt_model import Engagement, Environment, Firm, System, SystemLifecycleEvent
from adopt_model._enums import LifecycleState

__all__ = ["ScopeRecords"]


class ScopeRecords(Protocol):
    """Row in, row out. No SQL, connection or cursor crosses this boundary.

    Every `find_*` deliberately ignores `lifecycle_state`: a slug belonging to an
    `ARCHIVED` or `DISCONNECTED` scope must still be found, or it would be
    reissued and every URI ever emitted for the earlier scope would silently
    re-point (implementation spec §4.5 behaviour 2).
    """

    def transaction(self) -> AbstractContextManager[None]:
        """A unit of work. Nested use joins the outermost transaction."""
        ...

    def insert_firm(self, row: Firm) -> None: ...
    def insert_engagement(self, row: Engagement) -> None: ...
    def insert_system(self, row: System) -> None: ...
    def insert_environment(self, row: Environment) -> None: ...
    def insert_lifecycle_event(self, row: SystemLifecycleEvent) -> None: ...

    def find_firm(self, slug: str) -> Firm | None: ...
    def find_engagement(self, firm_id: str, slug: str) -> Engagement | None: ...
    def find_system(self, engagement_id: str, slug: str) -> System | None: ...
    def find_environment(self, system_id: str, slug: str) -> Environment | None: ...

    def get_system(self, system_id: str) -> System | None: ...

    def set_system_state(
        self, system_id: str, to_state: LifecycleState, updated_at: _dt.datetime
    ) -> None:
        """Advance a system's stored state.

        Called from `adopt_scope.lifecycle.transition` and nowhere else, because
        that is the only place the paired `system_lifecycle_event` is written.
        """
        ...
