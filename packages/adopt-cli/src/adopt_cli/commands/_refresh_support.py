"""`adopt refresh`'s write path: what the cascade concluded, recorded once.

The decisions are all elsewhere and on purpose. `adopt_map.diff.compute` decides
what changed, `adopt_knowledge.review.coalesce_changes` decides what one
reviewer sees, `ChangeFacade` decides nothing at all and records. This module is
the sequence that joins them, and the sequence is the whole of its content:
everything it does is either "read before the run" or "write inside one
transaction", and both are load-bearing.

**Read before, write after.** Stored identities and the file snapshot are read
before `run_map` executes, because afterwards every new identity is in the store
and every changed file has already been re-read: nothing would have appeared,
nothing would have changed, and a refresh would report a clean run forever.

**One transaction for the whole conclusion.** Retirements, digest re-records,
the change event, its classifications, propagation and the review batch commit
together or not at all. A partial commit is the worst outcome available here:
knowledge staled with no queue entry explaining why is rot the product created
itself, and a queue entry pointing at a retirement that rolled back is a
reviewer being asked about something that never happened.

**The map's own writes are not in that transaction, and cannot be.** `run_map`
opens one transaction per pack and records moves as it goes -- that is Build 1's
design, and refresh calls it as built rather than reaching inside it. So a
crash between the map and the write leaves observed identities and recorded
moves with no change event. That is recoverable and self-correcting: the next
refresh reads the new state as its baseline and reports nothing, which is
"we already know about this", not a lie. The alternative -- refresh
reimplementing the walk to hold one transaction over both -- is a second
extractor path, and two extraction paths eventually disagree about what is
there.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from adopt_knowledge.review import SOURCE_REFRESH, ChangeCause, ChangedItem, coalesce_changes
from adopt_map import ChangeEntry, DiffOutcome, FileDelta, MapReport, SourceTree
from adopt_map.diff import (
    CLASS_DEAD,
    CLASS_MOVED,
    CLASS_NEW,
    CLASS_SEMANTICS,
    STEP_SEMANTICS,
)
from adopt_map.diff import compute as compute_diff
from adopt_map.filestate import FileState, changed_paths, hash_file

from adopt_model import Identity
from adopt_obs import get_logger, new_id
from adopt_scope import Scope

__all__ = [
    "ProbeDelta",
    "RefreshOutcome",
    "current_file_state",
    "diff_for",
    "probe_delta",
    "record_refresh",
    "refresh_batch_key",
]

_log = get_logger("adopt_cli")

#: The classes whose subject is a referent a human may have written about, and
#: therefore the ones that can put an item in the queue. RENDER-ONLY is excluded
#: by construction rather than by filter: it names a *file*, has no identity,
#: and asking a reviewer to act on it is what "low-priority informational"
#: exists to avoid.
_ACTIONABLE = (CLASS_DEAD, CLASS_MOVED, CLASS_SEMANTICS)


@dataclass(slots=True)
class RefreshOutcome:
    """What one refresh run did, as the command needs to render and exit on it."""

    diff: DiffOutcome
    batch_key: str
    change_event_id: str | None = None
    classification_ids: tuple[str, ...] = ()
    review_batch_id: str | None = None
    review_item_ids: tuple[str, ...] = ()
    retired: tuple[str, ...] = ()
    redigested: tuple[str, ...] = ()
    staled_bindings: tuple[str, ...] = ()
    queued_items: tuple[ChangedItem, ...] = ()
    #: Classified changes whose identity nothing is bound to. Reported so the
    #: run's counts add up: a reviewer seeing "3 semantic changes" and two queue
    #: entries needs to know the third covers knowledge nobody has written.
    unbound: tuple[ChangeEntry, ...] = ()
    snapshot_paths: int = 0
    files_hashed: int = 0
    probes: "ProbeDelta | None" = None
    probe_event_id: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def wrote_anything(self) -> bool:
        return self.change_event_id is not None or self.probe_event_id is not None

    @property
    def found_actionable(self) -> bool:
        """Whether anything needs a human -- what exit 4 means.

        **RENDER-ONLY does not count**, and that is the difference between a
        useful exit code and one an FDE learns to ignore. A comment edit is
        recorded, rendered and reviewable; making it set the same exit code as a
        deleted endpoint would fire the signal on every commit, which is the
        flood v6.1 coalescing exists to prevent arriving through the exit status
        instead of the queue.
        """
        return bool(self.diff.actionable) or bool(self.probes is not None and self.probes.entries)


def refresh_batch_key() -> str:
    """`refresh:<ulid>` -- one key per run, shared by every event it produces.

    A run id rather than a timestamp: two refreshes in one millisecond would
    share a timestamped key and their batches would merge into a session nobody
    ran. `run` is the registered correlation prefix and this is exactly what it
    is for.
    """
    return f"{SOURCE_REFRESH}:{new_id('run')}"


def current_file_state(tree: SourceTree) -> tuple[list[FileState], int]:
    """Hash every walked file. Returns the states and how many were readable.

    Unreadable files are skipped rather than recorded with a sentinel: a file
    that could not be hashed has an unknown state, and the honest treatment of
    an unknown is to leave it out of a snapshot whose only claim is "these bytes
    were these bytes".
    """
    states: list[FileState] = []
    for entry in tree.files:
        digest = hash_file(entry.absolute)
        if digest is not None:
            states.append(FileState(path=entry.path, sha256=digest))
    return states, len(states)


def diff_for(
    *,
    run: MapReport,
    before: Sequence[Any],
    previous_state: Mapping[str, str] | None,
    current_state: Sequence[FileState],
) -> DiffOutcome:
    """Run the cascade over one map run's result. Pure but for its arguments."""
    delta = (
        FileDelta(available=False)
        if previous_state is None
        else FileDelta(
            changed_paths=changed_paths(
                previous_state, {state.path: state.sha256 for state in current_state}
            ),
            available=True,
        )
    )
    return compute_diff(
        stored=before,
        observed=run.observed,
        moves=run.moves,
        file_delta=delta,
        failed_extractors=[
            outcome.extractor for outcome in run.outcomes if outcome.status == "failed"
        ],
        ran_extractors=[outcome.extractor for outcome in run.outcomes],
        oversized_paths=run.files_oversized,
    )


@dataclass(slots=True)
class ProbeDelta:
    """What the probe half of one refresh observed, ready to be recorded.

    **Every field that is not a finding is a statement about what was not
    looked at**, for the reason the artifact report gives about exemptions: a
    run that examined nothing must not read as a clean one. `reason` says why
    the half did not run at all; `skipped` says which probes had nothing to
    compare against; `failed` says which could not reach their system; and
    `unresolved` says which declared referents no longer exist.
    """

    ran: bool = False
    reason: str | None = None
    entries: tuple[ChangeEntry, ...] = ()
    drifted: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    skipped: tuple[dict[str, str], ...] = ()
    unresolved: tuple[str, ...] = ()
    conflicts: tuple[dict[str, str], ...] = ()
    probes_run: int = 0


def probe_delta(
    handle: Any,
    scope: Scope,
    *,
    dead_uris: frozenset[str] = frozenset(),
    allow_network: bool = False,
) -> ProbeDelta:
    """Re-run the probes and turn drift into classified change entries.

    **Build 5's machinery invoked as built, not a second copy of it.**
    `execute_in_scope` is the same function `adopt probe run` calls -- sensors,
    budgets, host allow-list, conflict rows and all -- and `compare_in_scope` is
    the same comparison `adopt probe diff` reports. Refresh adds exactly one
    thing neither had: what a drift *means* for the knowledge bound to what the
    probe exercises.

    Three rules, each inherited rather than restated:

    * **`probe_changed` is never drift.** A run compared against a baseline of
      another revision means we edited the probe, and reporting that as a change
      in the client's system is the mistake that teaches an FDE to ignore the
      command. `compare_in_scope` decides it; this function only reads DRIFT.
    * **The manifest's `exercises` is the only probe -> identity link** (B5's
      `conflict.py` reading). A probe declaring nothing produces nothing here,
      silently and correctly: it never claimed to be about a referent.
    * **A failure is not a finding.** A probe that could not reach its system
      says so through its sensor heartbeat -- which is what makes the item
      `observation_stale` rather than `stale` -- and refresh reports it and
      carries on. Treating an unreachable system as a changed one is the
      "connector silence is stability" error inverted.

    Returns:
        A `ProbeDelta`. `ran=False` with a reason when there is nothing to run.
    """
    from adopt_probe import DRIFT, NO_BASELINE, ProbeOutcome

    from adopt_cli.commands._probe_support import (
        compare_in_scope,
        execute_in_scope,
        probes_in_scope,
    )

    definitions = probes_in_scope(handle, scope)
    if not definitions:
        return ProbeDelta(ran=False, reason="no probes are defined in this scope")

    execution = execute_in_scope(handle, scope, definitions, allow_network=allow_network)
    comparisons, _ = compare_in_scope(handle, scope)
    specs = {spec.name: spec for _, _, spec in execution.pairs}

    skipped = list(execution.skipped)
    failed = tuple(
        sorted(
            report.probe_name
            for report in execution.reports
            if report.outcome in (ProbeOutcome.FAILURE, ProbeOutcome.BLOCKED)
        )
    )

    drifted: list[str] = []
    unresolved: list[str] = []
    # `identity_id -> (uri, [evidence fragment])`. Grouped rather than appended
    # one entry per (probe, referent) because `classification` is UNIQUE on
    # `(change_event_id, identity_id)`: two probes exercising one endpoint is
    # one thing that happened to it, described twice.
    grouped: dict[str, tuple[str, list[str]]] = {}

    for comparison in comparisons:
        if comparison.verdict == NO_BASELINE:
            skipped.append(
                {
                    "probe": comparison.probe,
                    "reason": "no baseline yet -- run `adopt probe baseline --set`",
                }
            )
            continue
        if comparison.verdict != DRIFT:
            continue
        spec = specs.get(comparison.probe)
        if spec is None:  # pragma: no cover -- a comparison for a probe this run skipped
            continue
        drifted.append(comparison.probe)
        for uri in spec.exercises:
            identity = handle.identities().find_by_uri(uri)
            if identity is None or uri in dead_uris:
                unresolved.append(uri)
                continue
            _, fragments = grouped.setdefault(identity.id, (uri, []))
            fragments.append(_probe_evidence(comparison))

    entries = tuple(
        ChangeEntry(
            uri=uri,
            impact_class=CLASS_SEMANTICS,
            decided_by=STEP_SEMANTICS,
            evidence="; ".join(sorted(fragments)),
            identity_id=identity_id,
        )
        for identity_id, (uri, fragments) in sorted(grouped.items())
    )

    return ProbeDelta(
        ran=True,
        entries=entries,
        drifted=tuple(sorted(drifted)),
        failed=failed,
        skipped=tuple(skipped),
        unresolved=tuple(sorted(set(unresolved))),
        conflicts=execution.conflicts,
        probes_run=len(execution.reports),
    )


def _probe_evidence(comparison: Any) -> str:
    """`evidence` for a probe-sourced classification: which probe, and how far off.

    The **lowest** similarity across the drifted steps, because that is the step
    a reviewer should look at first, and a mean would let one wildly changed
    response hide behind four identical ones. Never the output itself: this
    column is exported.
    """
    similarities = [
        step.similarity
        for step in comparison.steps
        if step.verdict == "drift" and step.similarity is not None
    ]
    worst = f", lowest similarity {min(similarities):.2f}" if similarities else ""
    return f"probe {comparison.probe} drifted from baseline {comparison.baseline_id}{worst}"


def record_refresh(
    handle: Any,
    *,
    scope: Scope,
    diff: DiffOutcome,
    batch_key: str,
    probes: ProbeDelta | None = None,
    actor_id: str | None = None,
) -> RefreshOutcome:
    """Write everything one refresh concluded, in one transaction.

    Order inside the transaction is deliberate:

    1. **retire deaths** -- so `resolve_freshness`' source rule sees them;
    2. **re-record digests** -- semantics changes and instrument re-baselines
       alike, so the *next* run compares against this observation;
    3. **the change event and its classifications** -- the record of what this
       run concluded and why, one event per **source**: `artifact` for the map
       diff and `provider` for the probe delta, both under this run's one
       `batch_key`, because a reviewer works one session and not one per sensor;
    4. **propagation** -- the one binding write, semantics-changed only;
    5. **the review batch** -- last, because it references the items the steps
       above may have staled, and a queue entry that predates the state it
       describes would show a reviewer a stale reason for a stale item.

    Returns:
        A `RefreshOutcome`. When the diff found nothing actionable, no event is
        written and `change_event_id` is `None` -- the clean-run promise.
    """
    outcome = RefreshOutcome(diff=diff, batch_key=batch_key, probes=probes)
    probe_entries = () if probes is None else probes.entries

    if scope.system is None or scope.environment is None:
        raise ValueError(
            "refresh needs a scope resolved to an environment: a change event carries "
            "both, and a run that could not name where it looked is not reportable."
        )

    identities = handle.identities()
    changes = handle.changes()
    actionable = [entry for entry in diff.changes if entry.impact_class in _ACTIONABLE]

    with handle.revision_records().transaction():
        for entry in actionable:
            if entry.impact_class == CLASS_DEAD and entry.identity_id is not None:
                identities.retire(
                    identity_id=entry.identity_id,
                    reason="not observed by adopt refresh; no successor shares its digest",
                    actor_id=actor_id,
                )
                outcome.retired += (entry.identity_id,)

        for entry in actionable:
            if entry.impact_class == CLASS_SEMANTICS and entry.identity_id is not None:
                _record_digest(identities, entry, actor_id=actor_id)
                outcome.redigested += (entry.identity_id,)
        for rebaseline in diff.rebaselines:
            identities.record_digest(
                identity_id=rebaseline.identity_id,
                source_version=rebaseline.digest,
                extractor=rebaseline.extractor,
                extractor_version=rebaseline.to_version,
                source_ref=rebaseline.source_path,
                actor_id=actor_id,
            )
            outcome.redigested += (rebaseline.identity_id,)

        # New identities were created by the map run itself; their ids are read
        # back here because `classification.identity_id` is NOT NULL and the
        # diff -- which writes nothing -- could not have known them.
        resolved = _resolve_new(identities, diff.changes)
        # **Every class is recorded, RENDER-ONLY included** (v6.1 §6: "nothing is
        # silent in v1"). What separates it from the rest is priority, not
        # existence: it never queues an item and never sets an exit code, but it
        # is in the store and in the review surface, because a cosmetic change a
        # reviewer cannot see is indistinguishable from one we failed to notice.
        #
        # The entries are handed to the facade **as the diff produced them**,
        # with only a missing id filled in. `ChangeEntry` already carries the
        # four fields a classification needs, so a second value type would be
        # one more place for a class or a cascade step to be transcribed wrong
        # -- and the facade takes the shape structurally, exactly as
        # `adopt_probe` hands a writer to a runner.
        classified = [
            entry
            if entry.identity_id is not None
            else replace(entry, identity_id=resolved[entry.uri])
            for entry in diff.changes
            if entry.identity_id is not None or entry.uri in resolved
        ]

        if classified:
            outcome.change_event_id, outcome.classification_ids = changes.record_run(
                system_id=str(scope.system.id),
                environment_id=str(scope.environment.id),
                source="artifact",
                batch_key=batch_key,
                changes=classified,
                referent=scope.path(),
                raw=_run_summary(diff),
            )

        if probe_entries:
            # A **second** event, not more rows on the first. `change_event.source`
            # is the column that says where a finding came from, and one event
            # carrying both would make "what did the probes see" unanswerable
            # from the store -- which is exactly the question Build 8 operates on.
            outcome.probe_event_id, probe_classification_ids = changes.record_run(
                system_id=str(scope.system.id),
                environment_id=str(scope.environment.id),
                source="provider",
                batch_key=batch_key,
                changes=list(probe_entries),
                referent=scope.path(),
                raw=_probe_summary(probes),
            )
            outcome.classification_ids += probe_classification_ids

        # Propagation and the queue read **both** halves: a note staled by a
        # drifted probe is staled by the same mechanism and reviewed in the same
        # batch as one staled by an edited file. One freshness mechanism
        # everywhere (v6.1 §6) is a statement about sources too, not only levels.
        actionable = [*actionable, *probe_entries]
        semantic_ids = [
            entry.identity_id
            for entry in actionable
            if entry.impact_class == CLASS_SEMANTICS and entry.identity_id is not None
        ]
        if semantic_ids:
            bindings = [
                binding
                for identity_id in semantic_ids
                for binding in handle.bindings().for_identity(identity_id)
            ]
            outcome.staled_bindings = changes.stale_load_bearing_bindings(
                bindings, identity_ids=semantic_ids
            )

        queued, unbound = _queue_entries(handle, actionable)
        outcome.queued_items = queued
        outcome.unbound = unbound
        if queued:
            outcome.review_batch_id, outcome.review_item_ids = handle.governance().open_batch(
                system_id=str(scope.system.id),
                batch_key=batch_key,
                items=[(item.item_id, None) for item in queued],
                owner_actor_id=actor_id,
            )

    _log.info(
        "refresh.recorded",
        batch_key=batch_key,
        classifications=len(outcome.classification_ids),
        probe_classifications=len(probe_entries),
        retired=len(outcome.retired),
        staled=len(outcome.staled_bindings),
        queued=len(outcome.review_item_ids),
    )
    return outcome


def _record_digest(identities: Any, entry: ChangeEntry, *, actor_id: str | None) -> None:
    """Append the observed digest, so the next run compares against this one."""
    if entry.digest is None:  # pragma: no cover -- a semantics entry always has one
        return
    identities.record_digest(
        identity_id=entry.identity_id,
        source_version=entry.digest,
        extractor=entry.extractor,
        extractor_version=entry.extractor_version,
        source_ref=entry.source_path,
        actor_id=actor_id,
    )


def _resolve_new(identities: Any, entries: Sequence[ChangeEntry]) -> dict[str, str]:
    """`uri -> identity_id` for entries the diff could not id.

    Only `UNBOUND_NEW` reaches here, and only because the identity it names was
    created by the map run *after* the stored state was read. A URI that still
    resolves to nothing is skipped rather than raised: an extractor that emitted
    an observation the facade refused is a defect in that extractor, and losing
    the whole refresh over it would hide every real change beside it.
    """
    resolved: dict[str, str] = {}
    for entry in entries:
        if entry.identity_id is not None or entry.impact_class != CLASS_NEW:
            continue
        found: Identity | None = identities.find_by_uri(entry.uri)
        if found is not None:
            resolved[entry.uri] = found.id
    return resolved


def _queue_entries(
    handle: Any, actionable: Sequence[ChangeEntry]
) -> tuple[tuple[ChangedItem, ...], tuple[ChangeEntry, ...]]:
    """Which knowledge items this run puts in front of a human, and what is left over.

    Only **load-bearing** bindings queue their item. The rule is the one
    `resolve_freshness` applies (PRD F8.3) and applying a different one here
    would be the queue disagreeing with the freshness state it exists to explain:
    an item shown as needing review while `adopt ask` still serves it as fresh.
    """
    pairs: list[tuple[str, ChangeCause]] = []
    unbound: list[ChangeEntry] = []

    for entry in actionable:
        if entry.identity_id is None:  # pragma: no cover -- actionable always has one
            continue
        bindings = [
            binding
            for binding in handle.bindings().for_identity(entry.identity_id)
            if binding.is_load_bearing
        ]
        if not bindings:
            unbound.append(entry)
            continue
        cause = ChangeCause(
            identity_id=entry.identity_id,
            identity_uri=entry.uri,
            impact_class=entry.impact_class,
            evidence=entry.evidence,
        )
        for binding in bindings:
            pairs.append((binding.item_id, cause))

    return coalesce_changes(pairs), tuple(unbound)


def _probe_summary(probes: ProbeDelta | None) -> str:
    """`change_event.raw` for the provider half: counts and probe names only.

    Names are ours -- an FDE wrote the probe file -- and counts are ours. What a
    probe *saw* is client content and lives in `probe_observation` under the
    redaction policy, never here: this column is exported.
    """
    if probes is None:  # pragma: no cover -- only called when entries exist
        return ""
    return ";".join(
        (
            f"probes_run={probes.probes_run}",
            f"drifted={','.join(probes.drifted)}",
            f"referents={len(probes.entries)}",
        )
    )


def _run_summary(diff: DiffOutcome) -> str:
    """`change_event.raw`: counts only, never client content.

    The column is **exportable**, so it is the most tempting place in the build
    to put a diff hunk and the one place that must never hold one. Counts by
    class say what the run concluded and travel through any boundary tier.
    """
    counts = diff.by_class()
    return ";".join(f"{name}={count}" for name, count in sorted(counts.items()))
