"""`store doctor` -- reports, and never repairs.

Implementation spec §4.7 behaviour 6 lists eight findings; each sprint adds the
findings for the state it introduces. S3 delivered the two the revision model
makes possible; S4 adds coverage-cache disagreement and the sensor whose cadence
is NULL. Malformed URIs arrive with the code that writes them.

**Doctor mutates nothing, and that is the whole design.** The incident card in
implementation spec §8 says it in the sharpest form: *"Do not repair the chain by
hand -- the chain is the audit record, and hand-editing it is the one action that
makes 'what did it say then' permanently unanswerable."* A doctor that offered
`--fix` would be a doctor whose findings disappear before anyone reads them, and
the writer that caused them would never be found.

**That is why the coverage finding calls `recompute_coverage` and not
`rebuild_cache`.** The two are separate calls precisely so that looking is not
also correcting: a doctor that rebuilt the cache first would report a clean store
and destroy the only evidence of the writer that drifted it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from adopt_coverage import recompute_coverage
from adopt_coverage.records import CoverageRecords
from adopt_model import Sensor
from adopt_obs import ErrorCode
from adopt_store.facades.records import RevisionRecords, SensorRecords
from adopt_store.revisions import FAMILIES, Family

__all__ = [
    "Finding",
    "doctor",
    "find_coverage_cache_disagreements",
    "find_dangling_heads",
    "find_forked_chains",
    "find_sensors_without_cadence",
]


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing wrong with the store.

    Carries the registry code so a caller can branch on the finding without
    parsing prose, and the ids so an operator can go and look rather than
    reproduce.
    """

    code: ErrorCode
    table: str
    subject_id: str
    detail: str

    def render(self) -> str:
        return f"{self.code}: {self.table} {self.subject_id} -- {self.detail}"


class _Store(Protocol):
    """The half of a store handle `doctor` needs."""

    def revision_records(self) -> RevisionRecords: ...
    def coverage_records(self) -> CoverageRecords: ...
    def sensor_records(self) -> SensorRecords: ...


#: Checked in a fixed order so two runs over one store produce one report.
_FAMILY_ORDER: Final[tuple[Family, ...]] = tuple(
    FAMILIES[prefix] for prefix in ("idn", "ki", "bnd", "pd")
)


def find_dangling_heads(records: RevisionRecords) -> list[Finding]:
    """Parents whose `current_revision_id` points at no revision.

    `current_revision_id` is a pointer with no foreign key (CR-07) -- the cyclic
    constraint it would otherwise need is what this rebuild refuses to carry. The
    integrity the database is not enforcing is therefore checked here, which is
    the trade CR-07 made explicit rather than assumed.
    """
    findings: list[Finding] = []
    for family in _FAMILY_ORDER:
        if not family.has_head_pointer:
            continue
        for parent_id, head_id in records.head_pointers(family.parent_table):
            if head_id is None:
                continue
            if not records.revision_exists(family.revision_table, head_id):
                findings.append(
                    Finding(
                        code=ErrorCode.REVISION_HEAD_DANGLING,
                        table=family.parent_table,
                        subject_id=parent_id,
                        detail=f"current_revision_id {head_id} names no row in "
                        f"{family.revision_table}",
                    )
                )
    return findings


def find_forked_chains(records: RevisionRecords) -> list[Finding]:
    """Parents with more than one revision that nothing supersedes.

    `expected_head_id` should make this unreachable, so a fork in the wild means
    a writer bypassed `append_revision` -- which is what the finding says, because
    the remedy is to find that writer and not to tidy the chain.

    Checked for all four families, including `identity`, whose head is derived:
    a derived head that resolves to two revisions is exactly as forked as a
    stored one, and is harder to notice precisely because there is no pointer to
    look at.
    """
    findings: list[Finding] = []
    for family in _FAMILY_ORDER:
        for parent_id in records.parent_ids(family.parent_table):
            revisions = set(records.revision_ids(family.revision_table, parent_id))
            superseded = set(records.superseded_ids(family.revision_table, parent_id))
            heads = sorted(revisions - superseded)
            if len(heads) > 1:
                findings.append(
                    Finding(
                        code=ErrorCode.REVISION_CHAIN_FORK,
                        table=family.revision_table,
                        subject_id=parent_id,
                        detail=f"{len(heads)} revisions are superseded by nothing: "
                        f"{', '.join(heads)}. A chain has exactly one head",
                    )
                )
    return findings


def find_coverage_cache_disagreements(records: CoverageRecords) -> list[Finding]:
    """Identities whose `covered_cache` differs from a fresh recompute.

    **Alarm-grade, and never repaired here.** `recompute_coverage` is called and
    `rebuild_cache` is not: the disagreement is the evidence, and a doctor that
    corrected it would report a healthy store while the writer that drifted it
    kept drifting. PRD F7.3 says it plainly -- a disagreement is a defect signal,
    not a value to reconcile.

    The sweep covers every system with identities, and every environment of each,
    because a caller that had to name a scope would eventually name the wrong one
    and read the silence as health.
    """
    findings: list[Finding] = []
    for system_id in records.systems_with_identities():
        result = recompute_coverage(records, system_id)
        findings.extend(
            Finding(
                code=ErrorCode.COVERAGE_CACHE_DISAGREEMENT,
                table="identity",
                subject_id=entry.identity_id,
                detail=f"cache says covered={entry.cached} and the recompute says "
                f"{entry.recomputed}. The recompute is the authority; find the writer "
                "that set the cache before rebuilding it",
            )
            for entry in result.disagreements
        )
    return findings


def find_sensors_without_cadence(records: SensorRecords) -> list[Finding]:
    """Sensors with a NULL `expected_cadence_seconds`.

    Reported because the failure mode is silent: with no cadence there is no
    deadline, so the missed-heartbeat check never fires and a channel that
    stopped reporting a month ago still reads `HEALTHY`. Every other way this
    store goes wrong announces itself; this one announces nothing, which is
    exactly why it needs an instrument.
    """
    return [
        Finding(
            code=ErrorCode.FRESHNESS_SENSOR_DEGRADED,
            table="sensor",
            subject_id=sensor.id,
            detail="expected_cadence_seconds is NULL, so the missed-heartbeat check "
            "cannot run and silence from this sensor will never resolve STALE",
        )
        for sensor in _sorted_sensors(records.sensors_without_cadence())
    ]


def _sorted_sensors(sensors: Sequence[Sensor]) -> Sequence[Sensor]:
    """A fixed order, so two runs over one store produce one report."""
    return sorted(sensors, key=lambda row: row.id)


def doctor(store: _Store) -> list[Finding]:
    """Every finding this sprint's checks can produce, in a stable order.

    Reports without mutating. A caller that wants the store repaired has to
    decide what repair means and do it deliberately.
    """
    records = store.revision_records()
    return [
        *find_dangling_heads(records),
        *find_forked_chains(records),
        *find_coverage_cache_disagreements(store.coverage_records()),
        *find_sensors_without_cadence(store.sensor_records()),
    ]
