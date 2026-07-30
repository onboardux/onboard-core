"""The Workflow facade, the in-process test backend, the purity checker.

Empty by design at S0. Implemented in S8.

Invariants carried forward: no DBOS symbol appears in this package, workflow
bodies are pure (no clock, randomness, network, model call or I/O), and no
Build 0 OSS-side command uses durable workflows -- so the OSS CLI never requires
Postgres.
"""
