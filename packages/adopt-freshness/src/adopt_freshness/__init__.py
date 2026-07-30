"""`resolve_freshness`: multi-level, load-bearing propagation, sensor override.

Empty by design at S0. Implemented in S4.

Invariants carried forward: this package writes nothing, `is_load_bearing`
defaults to 1 so an unset writer errs toward staleness, and connector silence is
never read as stability.
"""
