"""The firm -> engagement -> system -> environment hierarchy.

Empty by design at S0. Implemented in S2.

Invariants carried forward: a slug is set once and never changes, a slug is
never reissued after ARCHIVED or DISCONNECTED, and no code path changes
`lifecycle_state` without writing a `system_lifecycle_event` in the same
transaction.
"""
