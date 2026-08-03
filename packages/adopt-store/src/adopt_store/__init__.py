"""SQLite realization, facades, transactions, the VectorIndex seam.

Implementation spec §4.7. Invariants enforced here and by CI rather than by
review:

* **`sqlite3` is imported in `adopt_store.sqlite` and nowhere else**
  (`no-raw-sqlite`), so no caller above the store can hold a connection.
* **No facade returns a connection, cursor or raw SQL** (contracts §10.3).
* **No destructive statement exists anywhere in the package** -- no `DROP`, no
  `ALTER ... DROP`, no unpredicated `DELETE` -- checked by
  `scripts/no_destructive_sql.py`.
* **Ids are generated inside the facade and scope is injected by it**; neither
  is accepted from a caller.
* **`append_revision` is the only mutation on a revision family**, and no update
  or delete method exists on any facade for a `*_revision` table
  (`no-revision-update`, in both repositories).

The coverage facade and the remaining contracts §10.3 accessors arrive with the
tables they front, in S4 and later.
"""

from adopt_store.api import OpenRestriction, Store, open_store, scope_facade, writer_identity
from adopt_store.doctor import Finding, doctor
from adopt_store.facades import (
    BindingFacade,
    IdentityFacade,
    KnowledgeFacade,
    ProbeFacade,
)
from adopt_store.revisions import (
    BindingRevisionDraft,
    IdentityRevisionDraft,
    KnowledgeRevisionDraft,
    ProbeDefinitionRevisionDraft,
    RevisionWriter,
    UnknownFamilyError,
)
from adopt_store.vector.api import VECTOR_FEATURE_FLAG, VectorIndex

__all__ = [
    "VECTOR_FEATURE_FLAG",
    "BindingFacade",
    "BindingRevisionDraft",
    "Finding",
    "IdentityFacade",
    "IdentityRevisionDraft",
    "KnowledgeFacade",
    "KnowledgeRevisionDraft",
    "OpenRestriction",
    "ProbeDefinitionRevisionDraft",
    "ProbeFacade",
    "RevisionWriter",
    "Store",
    "UnknownFamilyError",
    "VectorIndex",
    "doctor",
    "open_store",
    "scope_facade",
    "writer_identity",
]
