"""Store wiring `adopt probe` needs: add a probe, find them, run them.

Separate from `probe.py` so the command body stays the lazy-import boundary and
these can be tested against a real store without going through typer -- the
`_map_support` split, for the same reason.

**`add`'s idempotence lives here, and it is the verb's job rather than a
constraint's.** `probe_definition` has no UNIQUE index on (system, environment,
name), so this module keys on that triple itself: an identical re-add is a no-op,
a changed one appends a revision through the existing family machinery, and a
first add creates the definition. That is `IdentityFacade.observe()`'s shape
applied to probes -- the writer owns idempotence, because the alternative is a
schema change and v6.1 §8 budgets none for this build.

**Reads go through `export_records().table_rows`**, Build 4's pattern: it adds no
query path to any realized port, so the plane's escape-coverage denominator is
untouched by this build. The one exception is `list_probe_definitions`, which is
on `ProbeRunRecords` because the runner genuinely needs a scoped list and the new
port is excluded in the plane rather than realized.
"""

from collections.abc import Sequence
from typing import Any, Protocol

from adopt_probe import ProbeSpec
from adopt_probe.ports import ProbeWriter

from adopt_model import ProbeDefinition, ProbeDefinitionRevision, Sensor
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope

__all__ = [
    "AddOutcome",
    "SensorAdapter",
    "active_revision",
    "add_probe",
    "probes_in_scope",
    "resolve_probe",
    "run_targets",
]

#: The sensor kind a probe run reports under. Build 0's `sensor_kind` vocabulary
#: already carries it; Build 5 is the first writer.
PROBE_SENSOR_KIND: str = "probe"


class AddOutcome:
    """What `add` did, so the CLI can say so rather than always printing 'ok'."""

    CREATED: str = "created"
    UNCHANGED: str = "unchanged"
    REVISED: str = "revised"


def _rows[TModel](handle: Any, table: str, model_type: type[TModel]) -> Sequence[TModel]:
    rows: Sequence[TModel] = handle.export_records().table_rows(table, model_type)
    return rows


def probes_in_scope(handle: Any, scope: Scope) -> list[ProbeDefinition]:
    """Every probe defined for the scope's system and environment."""
    if scope.system is None or scope.environment is None:
        raise AdoptError(
            ErrorCode.SCOPE_VIOLATION,
            message="a probe needs a system and an environment",
            hint="Resolve the scope to `firm/engagement/system/environment`. A probe "
            "that did not say which environment it runs against is how a sandbox "
            "probe ends up pointed at production.",
        )
    return [
        row
        for row in _rows(handle, "probe_definition", ProbeDefinition)
        if row.system_id == scope.system.id and row.environment_id == scope.environment.id
    ]


def active_revision(handle: Any, probe: ProbeDefinition) -> ProbeDefinitionRevision | None:
    """The probe's current revision, by its head pointer.

    Falls back to the newest revision when the pointer is unset, which is the
    honest reading of a store written by something that did not advance it: the
    chain is still there, and refusing to run would be refusing over bookkeeping.
    """
    revisions = [
        row
        for row in _rows(handle, "probe_definition_revision", ProbeDefinitionRevision)
        if row.probe_definition_id == probe.id
    ]
    if not revisions:
        return None
    if probe.current_revision_id is not None:
        for row in revisions:
            if row.id == probe.current_revision_id:
                return row
    return sorted(revisions, key=lambda row: (row.created_at, row.id))[-1]


def resolve_probe(handle: Any, scope: Scope, name: str) -> ProbeDefinition:
    """The probe of that name in the scope.

    Raises:
        AdoptError: ``MANIFEST_INVALID`` when no probe of that name exists. Usage
            rather than a bespoke code: v6.1 §6 B5 authorizes three new probe
            codes and none of them is "you named a probe that is not there".
    """
    for probe in probes_in_scope(handle, scope):
        if probe.name == name:
            return probe
    known = sorted(row.name for row in probes_in_scope(handle, scope))
    raise AdoptError(
        ErrorCode.MANIFEST_INVALID,
        message=f"no probe named {name!r} in this scope",
        hint=f"Known probes: {known or '(none)'}. Add one with `adopt probe add FILE`.",
    )


def add_probe(
    handle: Any, scope: Scope, spec: ProbeSpec, *, actor_id: str | None = None
) -> tuple[str, str, str]:
    """Create, revise, or no-op a probe definition. Returns (outcome, id, revision).

    The three-way answer is the point: an operator re-running `add` after editing
    one line needs to know a revision was appended, and one re-running it
    unchanged needs to know nothing happened. A verb that printed "ok" for both
    would make the supersede chain invisible until `diff` reported "the probe
    changed" for a reason nobody remembered causing.
    """
    writer: ProbeWriter = handle.probes()

    existing = None
    for probe in probes_in_scope(handle, scope):
        if probe.name == spec.name:
            existing = probe
            break

    if existing is None:
        probe_id, revision_id = writer.record(
            scope=scope,
            name=spec.name,
            interaction=spec.interaction,
            safe_path=spec.safe_path,
            diff_method=spec.diff_method,
            capability_manifest=spec.capability_manifest,
            actor_id=actor_id,
        )
        return AddOutcome.CREATED, probe_id, revision_id

    current = active_revision(handle, existing)
    if (
        current is not None
        and current.interaction == spec.interaction
        and current.capability_manifest == spec.capability_manifest
        and current.safe_path == spec.safe_path
        and current.diff_method == spec.diff_method
        and current.status == "active"
    ):
        return AddOutcome.UNCHANGED, existing.id, current.id

    revision_id = writer.append(
        probe_definition_id=existing.id,
        expected_head_id=None if current is None else current.id,
        interaction=spec.interaction,
        safe_path=spec.safe_path,
        diff_method=spec.diff_method,
        capability_manifest=spec.capability_manifest,
        actor_id=actor_id,
    )
    return AddOutcome.REVISED, existing.id, revision_id


class _SensorFacadeLike(Protocol):
    def register(self, *, scope: Any, kind: Any, **kwargs: Any) -> Sensor: ...
    def heartbeat(self, *, sensor_id: str, outcome: Any, detail: str | None = ...) -> Any: ...


class SensorAdapter:
    """Binds one probe sensor so the runner can report without knowing the store.

    Satisfies `adopt_probe.SensorSink` structurally. The sensor is registered
    once per (system, environment) and reused: a row per run would turn "is this
    channel healthy" into "which of these four hundred rows did you mean", and
    `sensor` is the table freshness reads silence from.
    """

    def __init__(self, handle: Any, scope: Scope) -> None:
        self._facade: _SensorFacadeLike = handle.sensors()
        self._sensor_id = self._existing_or_new(handle, scope)

    def _existing_or_new(self, handle: Any, scope: Scope) -> str:
        # `probes_in_scope` already refused a scope without both levels before
        # any caller reaches here, so this is narrowing rather than validation --
        # and it raises the same registered code rather than asserting, because
        # an assertion is removed by `-O` and this one guards a sensor row that
        # freshness reads.
        system, environment = scope.system, scope.environment
        if system is None or environment is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message="a probe sensor needs a system and an environment",
                hint="Resolve the scope to `firm/engagement/system/environment`.",
            )
        for row in _rows(handle, "sensor", Sensor):
            if (
                row.system_id == system.id
                and row.environment_id == environment.id
                and row.kind == PROBE_SENSOR_KIND
            ):
                return row.id
        return self._facade.register(scope=scope, kind=PROBE_SENSOR_KIND).id

    @property
    def sensor_id(self) -> str:
        return self._sensor_id

    def heartbeat(self, *, outcome: str, detail: str | None = None) -> None:
        self._facade.heartbeat(sensor_id=self._sensor_id, outcome=outcome, detail=detail)


def _agent_for(specs: Sequence[ProbeSpec], *, allow_network: bool, audience: str) -> Any:
    """A `Runner`, but only if some probe actually has a prompt step.

    Built once for the whole invocation rather than per probe, and **not at all**
    when every step is `http`: R3 makes the no-model mode complete, so a probe set
    that needs no adapter must not fail because none is configured -- nor open the
    runtime annex to find that out.
    """
    if not any(step.kind == "prompt" for spec in specs for step in spec.steps):
        return None

    from adopt_agent import Runner
    from adopt_cli.commands.agent import adapter_settings, prompts_root

    offline, adapter_id, model, endpoint = adapter_settings(allow_network=allow_network)
    if not adapter_id:
        return None
    from adopt_cli.store_option import configured_annex

    with configured_annex() as annex:
        return Runner(
            annex=annex,
            scope_ref=audience,
            skills_root=prompts_root(),
            offline=offline,
            adapter_id=adapter_id,
            model=model,
            endpoint=endpoint,
        )


def run_targets(
    *,
    target: str | None,
    run_all: bool,
    scope_text: str | None,
    store_override: Any,
    allow_network: bool,
    read_file: Any,
) -> dict[str, Any]:
    """Resolve what to run, run it, and return the `--json` payload.

    Three shapes, and the difference between them is what gets persisted:

    * `--all` -- every probe in scope, each at its active revision, recorded.
    * a **name** -- that one stored probe, recorded.
    * a **path** -- parsed and executed, **nothing stored** (D-10). This is the
      authoring loop and the rogue negative control; a probe nobody added must
      not leave a definition, a revision or a run behind.
    """
    import os
    from pathlib import Path

    from adopt_probe import execute_probe, parse_probe, summarize

    from adopt_cli.commands._map_support import resolve_scope
    from adopt_cli.store_option import open_configured_store

    unstored_path = None
    if target is not None and not run_all:
        candidate = Path(target)
        if candidate.exists() and candidate.is_file():
            unstored_path = candidate

    if unstored_path is not None:
        # Parsed before anything is opened, so a rogue probe is refused without
        # a store, a socket or a row. `parse_probe` raises PROBE_HOST_UNDECLARED.
        spec = parse_probe(read_file(unstored_path))
        report = execute_probe(
            spec,
            probe_definition_id="(unstored)",
            probe_definition_revision_id="(unstored)",
            records=None,
            environ=os.environ,
            agent=_agent_for([spec], allow_network=allow_network, audience="probe"),
            sensor=None,
        )
        payload = summarize([report])
        payload["stored"] = False
        return payload

    handle = open_configured_store(store_override, read_only=False)
    try:
        scope = resolve_scope(handle, scope_text)
        definitions = probes_in_scope(handle, scope)
        if not run_all and target is not None:
            definitions = [resolve_probe(handle, scope, target)]

        pairs: list[tuple[ProbeDefinition, ProbeDefinitionRevision, ProbeSpec]] = []
        skipped: list[dict[str, str]] = []
        for probe in definitions:
            revision = active_revision(handle, probe)
            if revision is None:
                skipped.append({"probe": probe.name, "reason": "no revision"})
                continue
            if revision.status != "active":
                skipped.append({"probe": probe.name, "reason": f"revision is {revision.status}"})
                continue
            pairs.append((probe, revision, parse_probe(revision.capability_manifest)))

        agent = _agent_for(
            [spec for _, _, spec in pairs], allow_network=allow_network, audience="probe"
        )
        sensor = SensorAdapter(handle, scope) if pairs else None
        records = handle.probe_run_records()

        reports = [
            execute_probe(
                spec,
                probe_definition_id=probe.id,
                probe_definition_revision_id=revision.id,
                records=records,
                environ=os.environ,
                agent=agent,
                sensor=sensor,
            )
            for probe, revision, spec in pairs
        ]
    finally:
        handle.close()

    payload = summarize(reports)
    payload["stored"] = True
    if skipped:
        payload["skipped"] = skipped
    return payload
