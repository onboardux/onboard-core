"""SQLite realization, facades, the revision writer, doctor, the VectorIndex seam.

Empty by design at S0. Implemented in S2 and S3.

Invariants carried forward: `sqlite3` is imported in `adopt_store.sqlite` and
nowhere else, no facade returns a connection or raw SQL, no destructive
statement exists anywhere in the package, and `append_revision` is the only
mutation path on a revision family.
"""
