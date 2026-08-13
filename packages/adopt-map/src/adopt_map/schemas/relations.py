"""The relation predicate vocabulary -- contracts §5.2, closed.

Nineteen predicates and no twentieth. Closed for the same reason `IdentityKind`
is: a predicate an extractor invents is a predicate no consumer knows how to
read, and the edge it describes then exists in the store as a string nobody
joins on.

**The framework does not auto-create inverses** (§5.2). `handled_by` and
`handles` are both here, and an extractor emits *the direction it observed*.
Auto-inverting would manufacture an edge nobody saw: a route declaring
`handled_by` a symbol is an observation about the route, and asserting the
symbol `handles` the route is a second claim that happens to be true here and
would not be in a dispatcher that resolves handlers at runtime.
"""

from typing import Final, Literal, get_args

__all__ = ["RELATION_PREDICATES", "RelationPredicate"]

RelationPredicate = Literal[
    "handled_by",
    "handles",
    "reads",
    "writes",
    "calls",
    "called_by",
    "scheduled_by",
    "schedules",
    "configured_by",
    "configures",
    "emits",
    "consumes",
    "secured_by",
    "depends_on",
    "declared_in",
    "retries_to",
    "fails_to",
    "indexes",
    "derives_from",
]

#: The vocabulary as a set, read from the type rather than restated beside it.
RELATION_PREDICATES: Final[frozenset[str]] = frozenset(get_args(RelationPredicate))
