"""Bet 4: when what the system does contradicts what the store says it does.

v6.1's fourth bet is that *a conflict between intent and behaviour is a
deliverable*. This module is the deterministic join that produces one: a probe
drifted, the probe declares which identities it exercises, and those identities
have confirmed knowledge bound to them. That knowledge is now contradicted by an
observation, and saying so is the product.

**Only confirmed knowledge can be contradicted**, and that is Build 2's honesty
invariant carried forward rather than restated. An unverified draft is not a
claim anybody made -- nobody has agreed it is true -- so a probe disagreeing with
it is two unconfirmed opinions, not a conflict. Filing that as a conflict would
fill the reviewer's queue with disagreements between the tool and itself, and a
queue full of noise is a queue nobody opens. The same reasoning excludes a
retired binding: a relationship somebody withdrew is not one the store is still
asserting.

**No knowledge is written from a probe observation.** The conflict row cites the
confirmed revision on the intent side and leaves `actual_revision_id` NULL
(sprint plan D-8). What the probe saw is in `probe_observation`, reachable from
the run, and turning it into a knowledge revision would be fabricating canon from
an unreviewed measurement -- which is the one thing every build in this line
refuses to do. The disagreement is recorded; adjudicating it is a human act.

**Nothing here resolves anything either.** There is no update path and no delete
path for a conflict in this build. `disposition` already carries
`defect_filed` and `drift_accepted`, and the tooling that writes them arrives
with the review surface that owns them. v1 surfaces; it does not close.

The join is pure: rows in, pairs out, no store and no clock. The caller reads the
rows (through the export port's `table_rows`, adding no query path to any
realized port) and writes what this returns.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from adopt_model import (
    Binding,
    BindingRevision,
    Identity,
    KnowledgeItem,
    KnowledgeRevision,
)

__all__ = ["ConflictIntent", "conflicting_intents"]

#: A binding whose head revision says this is not live. Mirrors
#: `adopt_coverage.recompute`'s reading exactly, because "the store asserts this
#: relationship" must mean one thing in both places -- a probe conflicting with
#: knowledge that coverage does not count as covering the identity would be two
#: modules disagreeing about the same store.
_RETIRED_BINDING: Final[str] = "retired"
#: An item nobody is maintaining any more. Same reasoning.
_RETIRED_ITEM: Final[str] = "retired"
#: The one verification value that means a human agreed with it.
_VERIFIED: Final[str] = "verified"


@dataclass(frozen=True, slots=True)
class ConflictIntent:
    """One (identity, confirmed revision) pair a drifted probe contradicts."""

    identity_id: str
    identity_uri: str
    item_id: str
    intent_revision_id: str


def _head_binding_status(
    binding: Binding, revisions_by_binding: dict[str, list[BindingRevision]]
) -> str | None:
    """The status of a binding's current revision, or `None` if it has none.

    Falls back to the newest revision when the head pointer is unset, which is
    the same reading `_probe_support.active_revision` takes of a probe: the
    chain exists, and treating a missing pointer as a missing relationship would
    silently stop a real binding from ever conflicting.
    """
    revisions = revisions_by_binding.get(binding.id, [])
    if not revisions:
        return None
    if binding.current_revision_id is not None:
        for revision in revisions:
            if revision.id == binding.current_revision_id:
                return str(revision.status)
    return str(sorted(revisions, key=lambda row: (row.created_at, row.id))[-1].status)


def conflicting_intents(
    *,
    exercises: Sequence[str],
    identities: Sequence[Identity],
    bindings: Sequence[Binding],
    binding_revisions: Sequence[BindingRevision],
    items: Sequence[KnowledgeItem],
    knowledge_revisions: Sequence[KnowledgeRevision],
) -> tuple[ConflictIntent, ...]:
    """Every confirmed claim a drifted probe's exercised identities contradict.

    Args:
        exercises: The identity URIs the probe declares it exercises. A probe
            that declares none produces nothing -- it observed a change nobody
            told us was about any particular thing, and inventing a linkage
            would be worse than the silence.
        identities: Identities in scope, for URI resolution. A URI naming
            nothing is skipped rather than raised: a probe outliving the
            identity it named is a stale probe, not a broken command.
        bindings: Candidate bindings, any scope; filtered by identity here.
        binding_revisions: Their revisions, for the head status.
        items: The knowledge items bindings point at.
        knowledge_revisions: Every revision, for the verification of each item's
            current one.

    Returns:
        Unique `(identity, intent revision)` pairs, ordered deterministically so
        two runs over one store write the same rows in the same order.
    """
    if not exercises:
        return ()

    identity_by_uri = {row.uri: row for row in identities}
    revisions_by_binding: dict[str, list[BindingRevision]] = {}
    for binding_revision in binding_revisions:
        revisions_by_binding.setdefault(binding_revision.binding_id, []).append(binding_revision)
    items_by_id = {row.id: row for row in items}
    revisions_by_id = {row.id: row for row in knowledge_revisions}

    found: dict[tuple[str, str], ConflictIntent] = {}
    for uri in exercises:
        identity = identity_by_uri.get(uri)
        if identity is None:
            continue
        for binding in bindings:
            if binding.identity_id != identity.id:
                continue
            status = _head_binding_status(binding, revisions_by_binding)
            if status is None or status == _RETIRED_BINDING:
                continue
            item = items_by_id.get(binding.item_id)
            if (
                item is None
                or item.current_revision_id is None
                or str(item.freshness_state) == _RETIRED_ITEM
            ):
                continue
            revision = revisions_by_id.get(item.current_revision_id)
            if revision is None or str(revision.verification or "") != _VERIFIED:
                continue
            found[(identity.id, revision.id)] = ConflictIntent(
                identity_id=identity.id,
                identity_uri=identity.uri,
                item_id=item.id,
                intent_revision_id=revision.id,
            )

    return tuple(sorted(found.values(), key=lambda row: (row.identity_uri, row.intent_revision_id)))
