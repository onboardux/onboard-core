"""The probe half of `adopt ci-sense`: run them here, decide nothing.

**Sensing executes where access exists** (v6.1 §6 Build 8 F4). A probe reaches
a client's own deployment, so the CI is where it can be run at all for every
system behind a network the plane cannot enter. This module runs the probes the
local replica declares, through `adopt_probe.runner` -- the one module in the
programme permitted to open a probe socket -- and renders what they observed
into the sense payload.

**Nothing is stored and nothing is compared.** `execute_probe` is called with
`records=None` and with a sink that carries the heartbeat instead of writing it,
so no `probe_run`, no `probe_observation` and no `sensor_heartbeat` lands in the
replica: `adopt ci-sense` writes nothing locally, because after activation the
plane is the sole writer of an operated system's canon (R9). The comparison is
the plane's too, against **its** baseline, for `_ci_sense_support`'s reason -- a
replica is only as fresh as its last `adopt pull`, and comparing against a stale
baseline would report drift that a current baseline explains.

**What travels is what a comparison needs and nothing more.** The step outputs
travel because Build 5's `compare_run` is a text comparison and cannot be done
on digests; they are the client's *own system's* responses, already canon in
their store, and already redacted of every declared secret by the runner before
this module can see them. No line of the repository travels here any more than
it does in the artifact half.

**The revision id travels with every result, and it is load-bearing.** A run of
revision B compared against a baseline of revision A is the *question* changing,
not the system -- so the plane reports `probe_changed` and produces no change
event at all. Without the revision id on the wire that distinction is
unreachable, and every probe edit would arrive as a client incident.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ProbeSection", "run_payload_probes"]


@dataclass(slots=True)
class ProbeSection:
    """What the probe half of one ci-sense run observed, ready to be rendered.

    **Every field that is not a result is a statement about what was not looked
    at**, which is `ProbeDelta`'s rule and its reason: a run that examined
    nothing must not read as a clean one. `reason` says why the half did not run
    at all; `skipped` says which probes had no revision to execute.
    """

    ran: bool = False
    reason: str | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    heartbeats: list[dict[str, Any]] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        """The `probes` object as the payload carries it.

        Sorted by probe name for `build_payload`'s reason: the framing fixture
        is compared byte for byte across two repositories, and a section that
        reordered per run could not be pinned.
        """
        return {
            "ran": self.ran,
            "reason": self.reason,
            "results": sorted(self.results, key=lambda row: str(row["probe"])),
            "skipped": sorted(self.skipped, key=lambda row: row["probe"]),
            "heartbeats": sorted(self.heartbeats, key=lambda row: str(row["probe"])),
        }

    @property
    def drifted_candidates(self) -> int:
        """How many probes completed and could therefore be compared.

        Named *candidates* rather than *drifted* deliberately: this side does not
        compare anything, so it cannot know what drifted. The count exists for
        the CI log line, where "ran 3, 3 comparable" and "ran 3, 0 comparable"
        are very different mornings.
        """
        return sum(1 for row in self.results if row["outcome"] in ("success", "diff"))


def run_payload_probes(handle: Any, scope: Any, *, allow_network: bool = False) -> ProbeSection:
    """Execute the probes this scope declares and render what they observed.

    Args:
        handle: The read-only store handle ci-sense already opened. Used for
            **reads only** -- the definitions and their active revisions.
        scope: The resolved four-level scope.
        allow_network: Permits the model adapter for a probe's `prompt` steps.
            An `http` step needs no such flag; the host allow-list is the
            invariant there and it is enforced inside the runner.

    Returns:
        A `ProbeSection`. `ran=False` with a reason when there is nothing to run,
        which is not the same answer as "ran and found nothing".
    """
    import os
    from contextlib import ExitStack

    from adopt_probe import execute_probe, parse_probe

    from adopt_cli.commands._probe_support import (
        active_revision,
        probes_in_scope,
    )

    definitions = probes_in_scope(handle, scope)
    if not definitions:
        return ProbeSection(ran=False, reason="no probes are defined in this scope")

    section = ProbeSection(ran=True)
    pairs: list[tuple[Any, Any, Any]] = []
    for probe in definitions:
        revision = active_revision(handle, probe)
        if revision is None:
            section.skipped.append({"probe": probe.name, "reason": "no revision"})
            continue
        if revision.status != "active":
            section.skipped.append(
                {"probe": probe.name, "reason": f"revision is {revision.status}"}
            )
            continue
        pairs.append((probe, revision, parse_probe(revision.capability_manifest)))

    if not pairs:
        section.ran = False
        section.reason = "no probe in this scope has an active revision"
        return section

    # The annex stays open for the whole set and closes with the stack, exactly
    # as `execute_in_scope` holds it -- a `prompt` step's model call is metered
    # through the one seam, and opening an annex per probe would make each run's
    # budget its own.
    with ExitStack() as stack:
        agent = _agent_for([spec for _, _, spec in pairs], stack, allow_network=allow_network)
        for probe, revision, spec in pairs:
            sink = _CarriedHeartbeat()
            report = execute_probe(
                spec,
                probe_definition_id=probe.id,
                probe_definition_revision_id=revision.id,
                # **`records=None`, and that is the verb's whole discipline.** A
                # recorded run here would be a canon write from a caller that is
                # not the writer (R9), so nothing lands in the replica: the run,
                # its observations and its heartbeat all travel instead.
                records=None,
                environ=os.environ,
                agent=agent,
                sensor=sink,
            )
            section.results.append(_result(probe, revision, spec, report))
            section.heartbeats.append(sink.payload(probe.name))

    return section


def _agent_for(specs: Sequence[Any], stack: Any, *, allow_network: bool) -> Any:
    """The model seam, opened only when a probe actually has a `prompt` step.

    `_probe_support._agent_for` unchanged and by import, rather than a second
    construction of the same runner: the adapter resolution, the annex lifetime
    and the offline refusal are all decisions that must be identical to the
    local loop's, and two spellings of them would differ the first time one was
    edited.
    """
    from adopt_cli.commands._probe_support import _agent_for as build_agent

    return build_agent(list(specs), stack, allow_network=allow_network, audience="probe")


def _result(probe: Any, revision: Any, spec: Any, report: Any) -> dict[str, Any]:
    """One probe's run, as the payload carries it.

    `exercises` travels because it is **the only probe -> identity link** (B5's
    `conflict.py` reading, which Build 6's refresh already inherits): the plane
    turns a drift into a change event about the referents the probe declares it
    exercises, and a payload without them would be a finding about nothing.

    `outputs` and `fingerprints` are parallel to `steps`, in step order, because
    that is the order `compare_run` pairs them against the baseline in. Ordered
    by the loop that ran them rather than sorted -- step 2 compared against step
    0's baseline is a comparison of two unrelated things that would report drift
    with complete confidence.
    """
    return {
        "probe": probe.name,
        "probe_definition_id": probe.id,
        "probe_definition_revision_id": revision.id,
        "outcome": report.outcome,
        "exercises": list(spec.exercises),
        "outputs": [step.output for step in report.steps],
        "fingerprints": [step.fingerprint for step in report.steps],
        # Already redacted of every declared secret by the runner -- `redact` is
        # applied to a refusal before it reaches the report, because a refusal
        # message is the one place a bad request's interpolated value would
        # otherwise appear.
        "refusal": report.refusal,
    }


class _CarriedHeartbeat:
    """A `SensorSink` that carries the fact instead of writing it.

    v6.1 §6 B5 requires probe runs to *"emit per-run sensor-health facts locally
    so Builds 6/8 treat probes as sensors without rework"*. In CI there is no
    local sensor worth writing to -- the replica's health is nobody's oracle,
    and a heartbeat there would leave the plane's sensor (the row
    `resolve_freshness` actually reads) hearing nothing while the scope looked
    healthy. So the fact travels.

    **A sink rather than a mapping of outcome to health**, which was the first
    spelling here. The runner already owns that mapping and applies it before
    calling this; re-deriving it on this side would be a second copy of a rule
    whose two halves would differ the first time either was edited -- and the
    symptom would be a scope whose sensor health disagreed with the run that
    produced it.
    """

    def __init__(self) -> None:
        self.outcome: str | None = None
        self.detail: str | None = None

    def heartbeat(self, *, outcome: str, detail: str | None = None) -> None:
        self.outcome = outcome
        self.detail = detail

    def payload(self, probe_name: str) -> dict[str, Any]:
        return {"probe": probe_name, "outcome": self.outcome, "detail": self.detail}
