"""Manifest loader, four emitters, the additive-only linter, and migrations.

Empty by design at S0. Implemented in S1, where `schema/canonical.yaml` becomes
the single source of truth for all 37 tables at ``schema_version = 3``.

Invariant carried forward from the implementation spec: the manifest is the only
schema authority, no emitter reads a database, and there is no version 1 or 2 in
this schema line.
"""
