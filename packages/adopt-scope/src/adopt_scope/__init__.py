"""The firm -> engagement -> system -> environment hierarchy.

Implementation spec §4.5. Three invariants hold across this package, and they are
why it is a package rather than a few helpers inside the store:

1. **No state transition without an event.** `lifecycle.transition` is the only
   caller of `ScopeRecords.set_system_state`, and it writes the
   `system_lifecycle_event` in the same transaction.
2. **No slug mutation, ever** -- and no reissue after `ARCHIVED` or
   `DISCONNECTED`, because reuse would silently re-point every historical URI.
3. **`is_billable` and `data_residency_region` are recorded and never
   interpreted** in Build 0 (owner decisions 14 and 17).

`resolve()` returns ids **and** slugs: the URI builder needs slugs, the store
needs ids, and returning only one pushes a lookup into whatever loop the caller
happens to have written.
"""

from adopt_scope.hierarchy import ID_PREFIXES, INITIAL_LIFECYCLE_STATE, ScopeFacade
from adopt_scope.lifecycle import LIFECYCLE_EVENT_PREFIX, RETIRED_STATES, transition
from adopt_scope.records import ScopeRecords
from adopt_scope.resolve import SCOPE_LEVELS, Scope, ScopeLevel, ScopeNode, ScopePath
from adopt_scope.slug import (
    ensure_slug_available,
    ensure_slug_unchanged,
    is_valid_slug,
    validate_slug,
)

__all__ = [
    "ID_PREFIXES",
    "INITIAL_LIFECYCLE_STATE",
    "LIFECYCLE_EVENT_PREFIX",
    "RETIRED_STATES",
    "SCOPE_LEVELS",
    "Scope",
    "ScopeFacade",
    "ScopeLevel",
    "ScopeNode",
    "ScopePath",
    "ScopeRecords",
    "ensure_slug_available",
    "ensure_slug_unchanged",
    "is_valid_slug",
    "transition",
    "validate_slug",
]
