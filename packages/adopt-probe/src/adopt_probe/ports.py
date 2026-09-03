"""What the runner is handed, declared here and realized in `adopt-store`.

**`ProbeRunRecords` is a new port rather than three methods on `ProbeRecords`,
and that is a decision with a gate behind it.** `adopt-plane` already realizes
`ProbeRecords` as `PostgresProbeRecords`; its `escape_coverage` gate refuses to
*exclude* any port a `Postgres*Records` class realizes even partially
(`EXCLUDED-BUT-REALIZED`, and a partial realization is called out as the harder
case). Adding execution methods to `ProbeRecords` would therefore have forced
either Postgres realizations plus an escape case per new query path -- Build 8's
work, pulled forward -- or a red plane gate. A separate port takes the Build 3
(`EscalationRecords`) and Build 4 (`CoverageGapRecords`) exclusion precedent
cleanly.

**Declared here, in the consumer, rather than in `adopt_store.facades`**
(CR-34, CR-37, and `adopt_export.ExportRecords`'s precedent). `adopt_probe` is a
source module of `no-raw-sqlite`, and importing `adopt_store` would pull
`sqlite3` into this package's graph through `adopt_store/__init__` -> `api` ->
`sqlite`. The realization is `SqliteProbeRunRecords`; this package never learns
which engine answered.

**The method set is a ceiling, not a starting point.** Reporting reads go
through the export port's `table_rows` (Build 4's pattern), which adds no query
path to any realized port and therefore leaves the plane's escape denominator
untouched. `insert_baseline_version`, `insert_conflict`, `latest_baseline`,
`latest_run` and `open_conflicts` are declared now because the port is one
shape; their callers arrive with baselines, diff and conflicts.
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from adopt_model import BaselineVersion, Conflict, ProbeDefinition, ProbeObservation, ProbeRun

__all__ = ["ProbeRunRecords", "ProbeWriter", "SensorSink"]


class ProbeWriter(Protocol):
    """The slice of `ProbeFacade` that `adopt probe add` writes through.

    **Fields, never a draft object**, which is `KnowledgeWriter`'s shape and for
    its reason: the revision-draft dataclass belongs to `adopt-store`, and a
    caller constructing one would have to import that package. CR-36 makes
    `adopt_cli.store_option` the only CLI module permitted to do so, so the
    facade takes the fields and builds the draft on its own side of the boundary.
    """

    def record(
        self,
        *,
        scope: Any,
        name: str,
        interaction: str,
        safe_path: Any,
        diff_method: Any,
        capability_manifest: str,
        schedule_cron: str | None = ...,
        actor_id: str | None = ...,
    ) -> tuple[str, str]: ...

    def append(
        self,
        *,
        probe_definition_id: str,
        expected_head_id: str | None,
        interaction: str,
        safe_path: Any,
        diff_method: Any,
        capability_manifest: str,
        actor_id: str | None = ...,
    ) -> str: ...


class ProbeRunRecords(Protocol):
    """`probe_run`, `probe_observation`, `baseline_version`, `conflict`.

    Build 5's execution data. There is **no update and no delete** here, for the
    same reason `RevisionRecords` has none: a run is a record of what happened,
    and a record of what happened that can be edited is a record of nothing.
    `probe_run.outcome` is decided once, when the run finishes.
    """

    def transaction(self) -> AbstractContextManager[None]: ...

    def insert_probe_run(self, row: ProbeRun) -> None: ...
    def insert_probe_observation(self, row: ProbeObservation) -> None: ...
    def insert_baseline_version(self, row: BaselineVersion) -> None: ...
    def insert_conflict(self, row: Conflict) -> None: ...

    def list_probe_definitions(
        self, *, system_id: str, environment_id: str
    ) -> Sequence[ProbeDefinition]:
        """Every probe defined for one environment of one system.

        Scoped rather than global: `--all` means "every probe of the store's
        scope", and a probe belonging to another environment is a probe pointed
        at a system nobody asked about.
        """
        ...

    def latest_baseline(self, *, probe_definition_revision_id: str) -> BaselineVersion | None:
        """The newest approved baseline for one probe revision, or `None`."""
        ...

    def latest_run(
        self, *, probe_definition_id: str
    ) -> tuple[ProbeRun, Sequence[ProbeObservation]] | None:
        """The newest run of any revision of one probe, with its observations."""
        ...

    def open_conflicts(self, *, identity_id: str, intent_revision_id: str) -> Sequence[Conflict]:
        """Open conflicts already recorded for one (identity, intent) pair.

        The dedup read: an open conflict is never written twice, because a
        reviewer seeing the same disagreement five times stops reading the list.
        """
        ...


class SensorSink(Protocol):
    """Where a run's sensor-health fact goes.

    v6.1 §6 B5 requires probe runs to "emit per-run sensor-health facts locally
    so Builds 6/8 treat probes as sensors without rework". The machinery is Build
    0's `SensorFacade`; this is the seam that keeps `adopt_store` out of this
    package's import graph, and it is deliberately one method wide -- the runner
    reports what happened and decides nothing about health.
    """

    def heartbeat(self, *, outcome: str, detail: str | None = None) -> None: ...
