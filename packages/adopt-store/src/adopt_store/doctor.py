"""`store doctor` -- reports, and never repairs.

Implementation spec §4.7 behaviour 6 lists eight findings; this sprint delivers
the two that the revision model makes possible, and each later sprint adds the
findings for the state it introduces (coverage-cache disagreement at S4, malformed
URIs and sensor cadence with the code that writes them).

**Doctor mutates nothing, and that is the whole design.** The incident card in
implementation spec §8 says it in the sharpest form: *"Do not repair the chain by
hand -- the chain is the audit record, and hand-editing it is the one action that
makes 'what did it say then' permanently unanswerable."* A doctor that offered
`--fix` would be a doctor whose findings disappear before anyone reads them, and
the writer that caused them would never be found.
"""

from dataclasses import dataclass
from typing import Final, Protocol

from adopt_obs import ErrorCode
from adopt_store.facades.records import RevisionRecords
from adopt_store.revisions import FAMILIES, Family

__all__ = ["Finding", "doctor", "find_dangling_heads", "find_forked_chains"]


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


def doctor(store: _Store) -> list[Finding]:
    """Every finding this sprint's checks can produce, in a stable order.

    Reports without mutating. A caller that wants the store repaired has to
    decide what repair means and do it deliberately.
    """
    records = store.revision_records()
    return [*find_dangling_heads(records), *find_forked_chains(records)]
