"""Manifest loader, four emitters, the additive-only linter, and migrations.

`schema/canonical.yaml` is the single source of truth for all 37 tables at
``schema_version = 3``. Every realization is generated from it and none is
hand-edited; ``adopt-schema generate --check`` fails on drift.

Invariant carried forward from the implementation spec: the manifest is the only
schema authority, no emitter reads a database, and there is no version 1 or 2 in
this schema line.
"""

from adopt_schema.manifest import (
    Column,
    EnumDecl,
    Index,
    Manifest,
    ScopeRef,
    Table,
    load_manifest,
)

__all__ = [
    "Column",
    "EnumDecl",
    "Index",
    "Manifest",
    "ScopeRef",
    "Table",
    "load_manifest",
]
