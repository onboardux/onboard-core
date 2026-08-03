"""The contracts §10.3 facades — and the rule that keeps them portable.

**A facade never depends on a realization.** `no-raw-sqlite` names this package
as a source module, so nothing here may reach `sqlite3` even transitively — and
import-linter follows the chain, so importing `adopt_store.sqlite` is caught as
readily as importing the driver. That is the contract working as intended rather
than an obstacle: a facade that knows it is talking to SQLite is a facade the
Postgres realization has to reimplement, and two implementations of one set of
rules is what the tenant-escape suite would then be unable to cover.

The consequence is the shape of every facade in the programme: the facade holds
the rules and takes a **port**; each realization implements the port; the
assembly of the two happens in `adopt_store.api` (SQLite) and in
`plane_store` (Postgres). `ScopeFacade` is the first of them and lives in
`adopt_scope`, beside the slug and lifecycle rules it enforces.

Facades arrive with the tables they front: scope in S2, identities, items,
bindings and probes here in S3, coverage in S4, and so on.
"""

from adopt_scope import ScopeFacade
from adopt_store.facades.identity import IdentityFacade
from adopt_store.facades.knowledge import BindingFacade, KnowledgeFacade, ProbeFacade
from adopt_store.facades.records import (
    BindingRecords,
    IdentityRecords,
    KnowledgeRecords,
    ProbeRecords,
    RevisionRecords,
)

__all__ = [
    "BindingFacade",
    "BindingRecords",
    "IdentityFacade",
    "IdentityRecords",
    "KnowledgeFacade",
    "KnowledgeRecords",
    "ProbeFacade",
    "ProbeRecords",
    "RevisionRecords",
    "ScopeFacade",
]
