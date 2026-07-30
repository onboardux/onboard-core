"""`recompute_coverage` and the cache-disagreement alarm.

Empty by design at S0. Implemented in S4.

Invariants carried forward: this package is the only writer of `covered_cache`
and `covered_cache_at`, the recompute result is the authority, and a
disagreement alarms rather than silently reconciling.
"""
