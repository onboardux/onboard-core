"""`recompute_coverage` -- the authority, and the cache-disagreement alarm.

**Breaking change 3 of 3, second half.** In the withdrawn `0.1.x` line
`identity_registry.covered` *was* truth, recomputed by whichever writer happened
to touch it. Here the function is truth and `identity.covered_cache` is a cache,
and the difference is the whole point: a cache that disagrees is a defect signal,
never a value to be quietly corrected.

**This module computes and never writes.** The write lives in
`adopt_coverage.cache`, one call away, so that `store doctor` can ask for the
comparison without the act of looking changing what is there. Implementation spec
§8's incident card is explicit -- rebuilding the cache first destroys the
evidence, and the writer that caused the drift is then unfindable.

**The six inputs are evaluated here, not in SQL.** The port hands back rows; each
predicate below is one input from contracts §6, named, so the property test that
compares this function against an independent reference implementation is
comparing two derivations rather than two callers of one clever query.
"""

import datetime as _dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from adopt_const import COVERAGE_ALARM_SAMPLE_MAX
from adopt_coverage.records import CoverageRecords
from adopt_model import Binding, Identity, KnowledgeItem, ObservabilityBoundary
from adopt_obs import Clock, ErrorCode, SystemClock, get_logger, truncate_to_millisecond

__all__ = [
    "COVERAGE_REASONS",
    "REASON_AUDIENCE_OR_ENVIRONMENT",
    "REASON_IDENTITY_NOT_ACTIVE",
    "REASON_NO_ACTIVE_KNOWLEDGE_REVISION",
    "REASON_NO_LIVE_BINDING",
    "REASON_NO_OBSERVABILITY_BOUNDARY",
    "REASON_VERIFICATION_CONFLICTED",
    "CoverageResult",
    "Disagreement",
    "IdentityCoverage",
    "recompute_coverage",
]

_LOGGER: Final = get_logger("adopt.coverage")

# --------------------------------------------------------------------------
# The six inputs of contracts §6, one reason each.
#
# A reason names why an identity is **not** covered. They are stable strings
# because they reach the CLI envelope and a `store doctor` finding, and an
# operator branching on "which of the six is missing" is the whole reason the
# result is not a bare boolean.
# --------------------------------------------------------------------------

#: Input 1 -- "an active `identity_revision`".
REASON_IDENTITY_NOT_ACTIVE: Final[str] = "identity_revision_not_active"

#: Input 2 -- "at least one non-retired `binding`".
REASON_NO_LIVE_BINDING: Final[str] = "no_live_binding"

#: Input 3 -- "an active `knowledge_revision` on the bound item".
REASON_NO_ACTIVE_KNOWLEDGE_REVISION: Final[str] = "no_active_knowledge_revision"

#: Input 4 -- "applicable audience and environment".
REASON_AUDIENCE_OR_ENVIRONMENT: Final[str] = "audience_or_environment_inapplicable"

#: Input 5 -- "the `observability_boundary` for the scope".
REASON_NO_OBSERVABILITY_BOUNDARY: Final[str] = "no_observability_boundary"

#: Input 6 -- "verification requirements". A `conflicted` verification is Bet 4
#: working as designed: intent and reality disagree, the disagreement is
#: representable, and the identity is **not** reported as covered while it
#: stands.
REASON_VERIFICATION_CONFLICTED: Final[str] = "verification_conflicted"

#: Input 6, second half -- **only `verified` knowledge counts** (v6.1 §6 Build 2,
#: F6; plan decision D5).
#:
#: This tightens what Build 0 shipped, and the reason the original rule was
#: written the other way is worth keeping: until Build 2 nothing could *make* an
#: item verified, so requiring it would have made coverage unreachable by
#: construction. Build 2 supplies both doors -- `adopt ingest` writes a
#: human-authored document as `verified`, and confirming in `adopt review`
#: promotes a mined candidate -- so the objection no longer holds, and the rule
#: v6.1 actually requires can be enforced.
#:
#: What it buys is the honesty invariant: an unverified harvest candidate bound
#: to an identity must not make `adopt gaps` stop asking for that identity's
#: knowledge. A machine's unreviewed guess is not coverage, and counting it as
#: coverage is how a gap report becomes a report about itself.
REASON_NOT_VERIFIED: Final[str] = "knowledge_not_verified"

#: Every reason, in evaluation order. Exported so a caller can enumerate them
#: without re-deriving the list and getting one fewer.
COVERAGE_REASONS: Final[tuple[str, ...]] = (
    REASON_IDENTITY_NOT_ACTIVE,
    REASON_NO_LIVE_BINDING,
    REASON_NO_ACTIVE_KNOWLEDGE_REVISION,
    REASON_AUDIENCE_OR_ENVIRONMENT,
    REASON_NO_OBSERVABILITY_BOUNDARY,
    REASON_VERIFICATION_CONFLICTED,
    REASON_NOT_VERIFIED,
)

#: The `identity_status` that counts as live. `moved` and `dead` do not: a moved
#: identity's coverage belongs to the identity it aliases, and a dead one covers
#: nothing.
_ACTIVE_IDENTITY_STATUS: Final[str] = "active"

#: The terminal `binding_status`. `active` and `moved` are both live -- a moved
#: binding still ties the item to the referent, which is what CUJ-2 turns on.
_RETIRED_BINDING_STATUS: Final[str] = "retired"

#: The terminal `freshness_state` on a knowledge item. Knowledge carries its
#: terminal state on the parent rather than on the revision (contracts §5
#: obligation 4), so this is where "the revision is not active" is read.
_RETIRED_ITEM_FRESHNESS: Final[str] = "retired"

#: The `verification` that blocks coverage as a contradiction.
_CONFLICTED_VERIFICATION: Final[str] = "conflicted"

#: The only `verification` that carries coverage. A `NULL` verification blocks
#: exactly as `unverified` does: a revision that never stated its verification
#: has not been verified, and treating the absence as permission would let any
#: writer that omitted the field manufacture coverage.
_VERIFIED_VERIFICATION: Final[str] = "verified"


@dataclass(frozen=True, slots=True)
class IdentityCoverage:
    """One identity's verdict, and why."""

    identity_id: str
    uri: str
    covered: bool
    #: Empty when covered. Sorted and deduplicated, so two runs over one store
    #: produce one answer.
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Disagreement:
    """The cache said one thing and the recompute says another.

    Alarm-grade on its own. Carries both values because "the cache is wrong" is
    not actionable and "the cache says covered, the recompute says not" is.
    """

    identity_id: str
    uri: str
    cached: bool
    recomputed: bool


@dataclass(frozen=True, slots=True)
class CoverageResult:
    """What `recompute_coverage` returns.

    Nothing here is a cache and nothing here has been written anywhere. The
    caller decides whether to rebuild the cache from it (`adopt_coverage.cache`)
    or merely to look (`store doctor`).
    """

    system_id: str
    environment_id: str | None
    identities: tuple[IdentityCoverage, ...]
    disagreements: tuple[Disagreement, ...]
    computed_at: _dt.datetime

    @property
    def covered(self) -> int:
        return sum(1 for entry in self.identities if entry.covered)

    @property
    def uncovered(self) -> int:
        return sum(1 for entry in self.identities if not entry.covered)

    def verdict(self, identity_id: str) -> bool | None:
        """The verdict for one identity, or `None` when it is out of scope."""
        for entry in self.identities:
            if entry.identity_id == identity_id:
                return entry.covered
        return None


def _boundary_applies(boundary: ObservabilityBoundary, environment_id: str) -> bool:
    """Whether a boundary row governs an identity in `environment_id`.

    A boundary with no environment is the system-wide declaration and governs
    every environment; one naming an environment governs only that one.
    """
    return boundary.environment_id is None or boundary.environment_id == environment_id


def _environment_applies(item: KnowledgeItem, environment_id: str) -> bool:
    """Whether an item's environment is applicable to an identity's.

    `knowledge_item.environment_id` is nullable *because an item may span
    environments* -- so null is "applies everywhere", not "applies nowhere".
    Reading it the other way would make every cross-environment item silently
    stop covering anything.
    """
    return item.environment_id is None or item.environment_id == environment_id


def _binding_blockers(
    binding: Binding,
    *,
    binding_status: str | None,
    item: KnowledgeItem | None,
    verification: str | None,
    has_verification_row: bool,
    audience_count: int,
    environment_id: str,
) -> frozenset[str]:
    """Inputs 2, 3, 4 and 6, for one candidate binding.

    Returns the reasons this binding fails to carry coverage. Empty means it
    carries it, and one such binding is enough -- contracts §6 asks for "at least
    one non-retired binding", not for all of them.
    """
    blockers: set[str] = set()

    # Input 2 -- a binding whose head revision is retired is not live. A binding
    # with no head revision at all is also not live: nothing has ever asserted
    # the relationship.
    if binding_status is None or binding_status == _RETIRED_BINDING_STATUS:
        blockers.add(REASON_NO_LIVE_BINDING)

    # Input 3 -- an active knowledge revision on the bound item.
    if (
        item is None
        or item.current_revision_id is None
        or item.freshness_state == _RETIRED_ITEM_FRESHNESS
    ):
        blockers.add(REASON_NO_ACTIVE_KNOWLEDGE_REVISION)
        # Inputs 4 and 6 are statements about that item. With no item there is
        # nothing to say about them, and inventing a second reason would report
        # one defect as three.
        return frozenset(blockers)

    # Input 4 -- applicable audience and environment.
    if audience_count == 0 or not _environment_applies(item, environment_id):
        blockers.add(REASON_AUDIENCE_OR_ENVIRONMENT)

    # Input 6 -- verification requirements. Two distinct failures, reported
    # separately because they send an operator to different places: a conflict
    # needs adjudicating, an unverified item needs reviewing.
    if has_verification_row and verification == _CONFLICTED_VERIFICATION:
        blockers.add(REASON_VERIFICATION_CONFLICTED)
    elif verification != _VERIFIED_VERIFICATION:
        blockers.add(REASON_NOT_VERIFIED)

    return frozenset(blockers)


def _evaluate(
    identity: Identity,
    *,
    identity_status: str | None,
    bindings: Sequence[Binding],
    binding_statuses: Mapping[str, str],
    items: Mapping[str, KnowledgeItem],
    verifications: Mapping[str, str | None],
    audience_counts: Mapping[str, int],
    boundaries: Sequence[ObservabilityBoundary],
) -> IdentityCoverage:
    """All six inputs for one identity."""
    blockers: set[str] = set()

    # Input 1 -- an active identity revision. An identity with no revision has
    # never been asserted to exist by anything.
    if identity_status != _ACTIVE_IDENTITY_STATUS:
        blockers.add(REASON_IDENTITY_NOT_ACTIVE)

    # Input 5 -- the observability boundary for the scope. Without one, nothing
    # has declared what may be observed here, and coverage would be a claim
    # about a system nobody agreed to look at.
    if not any(_boundary_applies(row, identity.environment_id) for row in boundaries):
        blockers.add(REASON_NO_OBSERVABILITY_BOUNDARY)

    # Inputs 2, 3, 4 and 6, per candidate binding.
    if not bindings:
        blockers.add(REASON_NO_LIVE_BINDING)
    else:
        per_binding = [
            _binding_blockers(
                binding,
                binding_status=binding_statuses.get(binding.id),
                item=items.get(binding.item_id),
                verification=verifications.get(binding.item_id),
                has_verification_row=binding.item_id in verifications,
                audience_count=audience_counts.get(binding.item_id, 0),
                environment_id=identity.environment_id,
            )
            for binding in bindings
        ]
        if all(reasons for reasons in per_binding):
            # Every candidate failed. Report every distinct reason rather than
            # the first: an operator fixing one binding's audience should not
            # then discover the next binding was retired all along.
            blockers.update(*per_binding)

    return IdentityCoverage(
        identity_id=identity.id,
        uri=identity.uri,
        covered=not blockers,
        reasons=tuple(sorted(blockers)),
    )


def recompute_coverage(
    records: CoverageRecords,
    system_id: str,
    environment_id: str | None = None,
    *,
    clock: Clock | None = None,
) -> CoverageResult:
    """Evaluate coverage for every identity in scope. **The authority.**

    Args:
        records: The read port. Supplied rather than reached for, because a
            module-level store would make this function untestable against the
            random graphs its correctness property needs.
        system_id: The system whose identities are evaluated.
        environment_id: One environment, or `None` for every environment of the
            system.
        clock: Injected clock; tests pass `ManualClock`.

    Returns:
        Per-identity coverage plus a `disagreements` list against
        `covered_cache`. **Nothing is written.**

    Emits:
        `coverage_cache_disagreement` at `LogLevel.ALARM` when the disagreement
        list is non-empty -- a defect signal that must page, not merely be
        recorded (PRD F7.3). The **count is always complete**; the ids are a
        sample bounded by `COVERAGE_ALARM_SAMPLE_MAX`, because a cold cache over
        a 50k-identity store disagrees on every row and an uncapped field would
        put a megabyte of ULIDs on one line. `store doctor` enumerates every
        affected identity, so the alarm says *how bad* and the doctor says
        *which*. Identity **ids** travel, never URIs: an id is minted by us and
        carries no client-derived text.
    """
    now = truncate_to_millisecond((clock if clock is not None else SystemClock()).now())

    identities = records.identities_in_scope(system_id=system_id, environment_id=environment_id)
    identity_statuses = records.head_identity_statuses(
        system_id=system_id, environment_id=environment_id
    )
    binding_statuses = records.head_binding_statuses(
        system_id=system_id, environment_id=environment_id
    )
    items = {row.id: row for row in records.items_in_scope(system_id=system_id)}
    verifications = records.head_item_verifications(system_id=system_id)
    audience_counts = records.audience_counts(system_id=system_id)
    boundaries = records.boundaries_for_system(system_id=system_id)

    bindings_by_identity: dict[str, list[Binding]] = {}
    for binding in records.bindings_in_scope(system_id=system_id, environment_id=environment_id):
        bindings_by_identity.setdefault(binding.identity_id, []).append(binding)

    verdicts = tuple(
        _evaluate(
            identity,
            identity_status=identity_statuses.get(identity.id),
            bindings=bindings_by_identity.get(identity.id, []),
            binding_statuses=binding_statuses,
            items=items,
            verifications=verifications,
            audience_counts=audience_counts,
            boundaries=boundaries,
        )
        for identity in identities
    )

    cached = {row.id: row.covered_cache for row in identities}
    disagreements = tuple(
        Disagreement(
            identity_id=verdict.identity_id,
            uri=verdict.uri,
            cached=cached[verdict.identity_id],
            recomputed=verdict.covered,
        )
        for verdict in verdicts
        if cached[verdict.identity_id] != verdict.covered
    )

    if disagreements:
        _LOGGER.alarm(
            "coverage_cache_disagreement",
            code=str(ErrorCode.COVERAGE_CACHE_DISAGREEMENT),
            system_id=system_id,
            environment_id=environment_id,
            disagreement_count=len(disagreements),
            identity_ids=[entry.identity_id for entry in disagreements[:COVERAGE_ALARM_SAMPLE_MAX]],
            identity_ids_truncated=len(disagreements) > COVERAGE_ALARM_SAMPLE_MAX,
        )

    return CoverageResult(
        system_id=system_id,
        environment_id=environment_id,
        identities=verdicts,
        disagreements=disagreements,
        computed_at=now,
    )
