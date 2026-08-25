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
from contextlib import ExitStack
from typing import Any, Protocol

from adopt_probe import Baseline, ProbeSpec
from adopt_probe.ports import ProbeWriter

from adopt_model import (
    Binding,
    BindingRevision,
    Identity,
    KnowledgeItem,
    KnowledgeRevision,
    ProbeDefinition,
    ProbeDefinitionRevision,
    ProbeObservation,
    ProbeRun,
    Sensor,
)
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, truncate_to_millisecond
from adopt_scope import Scope

__all__ = [
    "AddOutcome",
    "SensorAdapter",
    "active_revision",
    "add_probe",
    "diff_probes",
    "probes_in_scope",
    "resolve_probe",
    "run_targets",
    "set_baselines",
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


def _clock(handle: Any) -> Clock:
    clock: Clock = handle.clock if handle.clock is not None else SystemClock()
    return clock


def _adapter_id() -> str | None:
    """The configured adapter, for a baseline's `model_provider_version`.

    Read through the same resolver every other key goes through, so `adopt
    doctor` can explain the value a baseline recorded.
    """
    from adopt_cli.commands.agent import adapter_settings

    _, adapter_id, _, _ = adapter_settings()
    return adapter_id


def _baseline_for(handle: Any, revision_id: str) -> Baseline | None:
    """The current baseline of **this** revision, or `None`.

    Keyed on the revision deliberately: a baseline recorded against a different
    revision is not something this run may be compared against, and returning it
    would make the runner call an edit to our own probe file a change in the
    client's system. `adopt probe diff` is where that case is reported, by name.
    """
    from adopt_probe import baseline_from_row

    row = handle.probe_run_records().latest_baseline(probe_definition_revision_id=revision_id)
    return None if row is None else baseline_from_row(row)


def _probe_baselines(handle: Any, revision_ids: frozenset[str]) -> Any:
    """The newest `baseline_version` across a probe's revisions, or `None`.

    A report read (`table_rows`), not a port query: `diff` has to find a
    baseline set against an **older** revision -- that is the entire
    "probe changed" case -- and the port's `latest_baseline` takes one revision
    id by design. Reading the table adds no query path to any realized port,
    which is what keeps the plane's escape denominator untouched by this build.
    """
    from adopt_probe import baseline_from_row

    from adopt_model import BaselineVersion

    rows = [
        row
        for row in _rows(handle, "baseline_version", BaselineVersion)
        if row.probe_definition_revision_id in revision_ids
    ]
    if not rows:
        return None
    newest = sorted(rows, key=lambda row: (row.created_at, row.id))[-1]
    return baseline_from_row(newest)


def _revision_ids(handle: Any, probe: ProbeDefinition) -> frozenset[str]:
    return frozenset(
        row.id
        for row in _rows(handle, "probe_definition_revision", ProbeDefinitionRevision)
        if row.probe_definition_id == probe.id
    )


def _agent_for(
    specs: Sequence[ProbeSpec], stack: ExitStack, *, allow_network: bool, audience: str
) -> Any:
    """A `Runner`, but only if some probe actually has a prompt step.

    Built once for the whole invocation rather than per probe, and **not at all**
    when every step is `http`: R3 makes the no-model mode complete, so a probe set
    that needs no adapter must not fail because none is configured -- nor open the
    runtime annex to find that out.

    **The annex is entered on the caller's `ExitStack`, not on a `with` inside
    this function, and that is a repaired defect rather than a style choice.**
    S5.1 returned the `Runner` out of a `with configured_annex()` block, so the
    annex database was closed before the first prompt step ever reached it and
    every `prompt` step through the CLI died with `Cannot operate on a closed
    database`. Nothing caught it: the runner's unit tests are handed an agent
    directly, and S5.1's hand-run demo probe had only `http` steps -- the seam was
    exercised everywhere except through the one path an operator uses. The
    journey e2e is what found it, which is exactly the case v6.1 §4 R1 makes for
    ending every build in a verb somebody runs.
    """
    if not any(step.kind == "prompt" for spec in specs for step in spec.steps):
        return None

    from adopt_agent import Runner
    from adopt_cli.commands.agent import adapter_settings, prompts_root
    from adopt_cli.store_option import configured_annex

    offline, adapter_id, model, endpoint = adapter_settings(allow_network=allow_network)
    if not adapter_id:
        return None

    annex = stack.enter_context(configured_annex())
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
        with ExitStack() as stack:
            report = execute_probe(
                spec,
                probe_definition_id="(unstored)",
                probe_definition_revision_id="(unstored)",
                records=None,
                environ=os.environ,
                agent=_agent_for([spec], stack, allow_network=allow_network, audience="probe"),
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

        sensor = SensorAdapter(handle, scope) if pairs else None
        records = handle.probe_run_records()

        # The annex stays open for the whole set of runs and closes with the
        # stack -- see `_agent_for`.
        with ExitStack() as stack:
            agent = _agent_for(
                [spec for _, _, spec in pairs],
                stack,
                allow_network=allow_network,
                audience="probe",
            )
            reports = [
                execute_probe(
                    spec,
                    probe_definition_id=probe.id,
                    probe_definition_revision_id=revision.id,
                    records=records,
                    environ=os.environ,
                    agent=agent,
                    sensor=sensor,
                    clock=handle.clock,
                    baseline=_baseline_for(handle, revision.id),
                )
                for probe, revision, spec in pairs
            ]
        # The conflict pass runs **after** every probe, not inside the loop: a
        # probe that drifted late must still be able to conflict, and a pass per
        # probe would re-read the whole knowledge join once per probe to answer
        # the same question.
        conflicts = _record_conflicts(handle, records, pairs, reports)
    finally:
        handle.close()

    payload = summarize(reports)
    payload["stored"] = True
    if conflicts:
        payload["conflicts"] = conflicts
    if skipped:
        payload["skipped"] = skipped
    return payload


def _record_conflicts(
    handle: Any,
    records: Any,
    pairs: Sequence[tuple[ProbeDefinition, ProbeDefinitionRevision, ProbeSpec]],
    reports: Sequence[Any],
) -> list[dict[str, str]]:
    """Bet 4's write: a drifted probe contradicting confirmed knowledge.

    Deduplicated against what is already open, per `(identity, intent revision)`,
    because a reviewer who sees the same disagreement on every run stops reading
    the list -- which is the failure mode that makes a conflict queue worthless.
    """
    from adopt_probe import ProbeOutcome, conflicting_intents

    from adopt_model import Conflict
    from adopt_obs import new_id

    drifted = [
        (spec, report)
        for (_, _, spec), report in zip(pairs, reports, strict=True)
        if report.outcome == ProbeOutcome.DIFF and spec.exercises
    ]
    if not drifted:
        return []

    # Read once for the whole pass. Every one of these is `table_rows`, so the
    # conflict join adds no query path to any realized port (Build 4's pattern).
    identities = _rows(handle, "identity", Identity)
    bindings = _rows(handle, "binding", Binding)
    binding_revisions = _rows(handle, "binding_revision", BindingRevision)
    items = _rows(handle, "knowledge_item", KnowledgeItem)
    knowledge_revisions = _rows(handle, "knowledge_revision", KnowledgeRevision)

    written: list[dict[str, str]] = []
    for spec, report in drifted:
        intents = conflicting_intents(
            exercises=spec.exercises,
            identities=identities,
            bindings=bindings,
            binding_revisions=binding_revisions,
            items=items,
            knowledge_revisions=knowledge_revisions,
        )
        detected_at = report.finished_at or _clock(handle).now()
        for intent in intents:
            if records.open_conflicts(
                identity_id=intent.identity_id, intent_revision_id=intent.intent_revision_id
            ):
                continue
            with records.transaction():
                records.insert_conflict(
                    Conflict(
                        id=new_id("cf"),
                        identity_id=intent.identity_id,
                        intent_revision_id=intent.intent_revision_id,
                        # v1 writes no knowledge from probe output (D-8). The
                        # intent side cites the revision a human confirmed; what
                        # the probe saw is in `probe_observation`, reachable from
                        # the run, and fabricating a revision to point at here
                        # would be inventing canon from an unreviewed measurement.
                        actual_revision_id=None,
                        detected_at=truncate_to_millisecond(detected_at),
                        disposition="open",
                    )
                )
            written.append(
                {
                    "probe": spec.name,
                    "identity": intent.identity_uri,
                    "intent_revision": intent.intent_revision_id,
                }
            )
    return written


def _runs_of(handle: Any, revision_id: str) -> list[ProbeRun]:
    """Every recorded run of one revision, oldest first.

    A report read rather than a port query, per Build 4's pattern: the port's
    `latest_run` answers "the newest run of this probe" and this needs "the
    newest *eligible* run of this revision", which is a filter over history.
    """
    return sorted(
        (
            row
            for row in _rows(handle, "probe_run", ProbeRun)
            if row.probe_definition_revision_id == revision_id
        ),
        key=lambda row: (row.started_at, row.id),
    )


def _observations_of(handle: Any, run_id: str) -> list[ProbeObservation]:
    """One run's observations in step order.

    Ordered by id, which **is** step order: ids are ULIDs minted in the loop
    that executed the steps, so they are monotonic within the run. Ordering by
    anything else would silently pair step 2 against step 0's baseline.
    """
    return sorted(
        (
            row
            for row in _rows(handle, "probe_observation", ProbeObservation)
            if row.probe_run_id == run_id
        ),
        key=lambda row: row.id,
    )


def set_baselines(handle: Any, scope: Scope) -> dict[str, Any]:
    """`adopt probe baseline --set` -- version "this is how it behaves today".

    One `baseline_version` row per active probe revision that has a run worth
    versioning. **Which run it took is in the report**, and so is that run's
    outcome: re-baselining after drift is a human accepting a change, and a verb
    that printed "baseline set" for both cases would make accepting a regression
    indistinguishable from confirming a steady state.

    Raises:
        AdoptError: ``PROBE_BASELINE_MISSING`` when nothing in scope has a run
            that could become a baseline. Usage, exit 2 -- nothing is broken and
            one command fixes it.
    """
    from adopt_probe import BASELINE_ELIGIBLE_OUTCOMES, build_baseline, parse_probe

    if scope.environment is None:  # pragma: no cover -- `probes_in_scope` refuses first
        raise AdoptError(
            ErrorCode.SCOPE_VIOLATION,
            message="a baseline is recorded against one environment",
            hint="Resolve the scope to `firm/engagement/system/environment`.",
        )

    records = handle.probe_run_records()
    now = truncate_to_millisecond(_clock(handle).now())
    adapter_id = _adapter_id()

    versioned: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for probe in probes_in_scope(handle, scope):
        revision = active_revision(handle, probe)
        if revision is None:
            skipped.append({"probe": probe.name, "reason": "no revision"})
            continue
        eligible = [
            row
            for row in _runs_of(handle, revision.id)
            if str(row.outcome) in BASELINE_ELIGIBLE_OUTCOMES
        ]
        if not eligible:
            # Reported, not raised: one probe with nothing to version must not
            # stop the others being versioned.
            skipped.append({"probe": probe.name, "reason": "no run to version"})
            continue

        run = eligible[-1]
        observations = _observations_of(handle, run.id)
        spec = parse_probe(revision.capability_manifest)
        row = build_baseline(
            run,
            observations,
            environment_id=str(scope.environment.id),
            now=now,
            redaction_policy=spec.redaction_policy,
            # Only when a model actually answered. A probe of pure `http` steps
            # ran against no model, and naming one would put an environment fact
            # into an exportable row that was never true of this recording.
            model_provider_version=(
                adapter_id if any(step.kind == "prompt" for step in spec.steps) else None
            ),
        )
        with records.transaction():
            records.insert_baseline_version(row)
        versioned.append(
            {
                "probe": probe.name,
                "baseline": row.id,
                "revision": revision.id,
                "from_run": run.id,
                # The honest half of `--set`: a baseline taken from a `diff` run
                # is a human accepting drift, and the report says which it was.
                "from_outcome": str(run.outcome),
                "steps": len(observations),
                "fingerprint": row.fingerprint,
            }
        )

    if not versioned:
        raise AdoptError(
            ErrorCode.PROBE_BASELINE_MISSING,
            message="no probe in this scope has a run that could become a baseline",
            hint="Run `adopt probe run --all` first. A baseline is a recording of "
            "observed behaviour, so there has to be an observation -- and a run "
            "that failed or was refused is not one.",
        )

    payload: dict[str, Any] = {"baselines": len(versioned), "set": versioned}
    if skipped:
        payload["skipped"] = skipped
    return payload


def diff_probes(handle: Any, scope: Scope) -> dict[str, Any]:
    """`adopt probe diff` -- the latest run against the named baseline.

    Re-derives the comparison the run already recorded and **writes nothing**.
    That is not a limitation: `probe_run.outcome` and
    `probe_observation.similarity` were decided when the run finished, and a
    command that could re-judge a recorded run would make the record a running
    opinion rather than an account of what happened.

    Raises:
        AdoptError: ``PROBE_BASELINE_MISSING`` when **no** probe in scope has a
            baseline. A single probe without one is listed and does not fail a
            command whose other probes compared cleanly.
    """
    from adopt_probe import DRIFT, NO_BASELINE, compare_run, parse_probe

    records = handle.probe_run_records()
    comparisons: list[dict[str, Any]] = []
    any_baseline = False

    for probe in probes_in_scope(handle, scope):
        revision = active_revision(handle, probe)
        latest = records.latest_run(probe_definition_id=probe.id)
        if revision is None or latest is None:
            # No run means no baseline: a baseline is made *from* a run. Reported
            # as the same "nothing to compare against yet" answer rather than as
            # a third vocabulary item saying the same thing.
            comparisons.append(
                {
                    "probe": probe.name,
                    "verdict": NO_BASELINE,
                    "baseline": None,
                    "baseline_revision": None,
                    "run_revision": None,
                    "run": None,
                    "steps": [],
                }
            )
            continue

        baseline = _probe_baselines(handle, _revision_ids(handle, probe))
        any_baseline = any_baseline or baseline is not None
        run, observations = latest
        comparisons.append(
            _comparison_payload(
                compare_run(
                    probe=probe.name,
                    baseline=baseline,
                    run_revision_id=run.probe_definition_revision_id,
                    run_id=run.id,
                    outputs=[row.output or "" for row in observations],
                    steps=parse_probe(revision.capability_manifest).steps,
                )
            )
        )

    if not any_baseline:
        raise AdoptError(
            ErrorCode.PROBE_BASELINE_MISSING,
            message="no probe in this scope has a baseline to compare against",
            hint="Run `adopt probe run --all` and then `adopt probe baseline --set`. "
            "A diff without a baseline is not a failure -- there is simply nothing "
            "yet that says how the system behaved before.",
        )

    return {
        "probes": len(comparisons),
        "comparisons": comparisons,
        "drifted": sum(1 for row in comparisons if row["verdict"] == DRIFT),
    }


def _comparison_payload(comparison: Any) -> dict[str, Any]:
    """One `ProbeComparison` as `--json` carries it.

    Both revision ids travel on a `probe_changed` verdict, because that verdict
    is a claim about **our** file rather than about the client's system, and a
    reader has to be able to check it.
    """
    return {
        "probe": comparison.probe,
        "verdict": comparison.verdict,
        "baseline": comparison.baseline_id,
        "baseline_revision": comparison.baseline_revision_id,
        "run_revision": comparison.run_revision_id,
        "run": comparison.run_id,
        "steps": [
            {
                "step": step.index,
                "kind": step.kind,
                "verdict": step.verdict,
                "similarity": step.similarity,
                "detail": step.detail,
            }
            for step in comparison.steps
        ],
    }
