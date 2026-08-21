"""`recompute_coverage` and the cache-disagreement alarm.

Implemented in S4. Contracts §6, implementation spec §4.8.

**The invariants this package carries.** It is the only writer of
`covered_cache` and `covered_cache_at` -- enforced by the `no-covered-cache-write`
import contract, not by convention. The recompute result is the authority and the
cache is rebuilt from it, never the reverse. A disagreement alarms and is never
silently reconciled, because a quietly self-healing cache reintroduces exactly
the invisible coverage decay the rebuild exists to delete.

**Computing and writing are two calls on purpose.** `recompute_coverage` reads
and decides; `rebuild_cache` writes. `store doctor` calls the first and not the
second, which is what lets it report a disagreement without destroying the
evidence of who caused it.
"""

from adopt_coverage.cache import CacheWriter, rebuild_cache
from adopt_coverage.recompute import (
    COVERAGE_REASONS,
    REASON_AUDIENCE_OR_ENVIRONMENT,
    REASON_IDENTITY_NOT_ACTIVE,
    REASON_NO_ACTIVE_KNOWLEDGE_REVISION,
    REASON_NO_LIVE_BINDING,
    REASON_NO_OBSERVABILITY_BOUNDARY,
    REASON_NOT_VERIFIED,
    REASON_VERIFICATION_CONFLICTED,
    CoverageResult,
    Disagreement,
    IdentityCoverage,
    recompute_coverage,
)
from adopt_coverage.records import CoverageRecords

__all__ = [
    "COVERAGE_REASONS",
    "REASON_AUDIENCE_OR_ENVIRONMENT",
    "REASON_IDENTITY_NOT_ACTIVE",
    "REASON_NOT_VERIFIED",
    "REASON_NO_ACTIVE_KNOWLEDGE_REVISION",
    "REASON_NO_LIVE_BINDING",
    "REASON_NO_OBSERVABILITY_BOUNDARY",
    "REASON_VERIFICATION_CONFLICTED",
    "CacheWriter",
    "CoverageRecords",
    "CoverageResult",
    "Disagreement",
    "IdentityCoverage",
    "rebuild_cache",
    "recompute_coverage",
]
