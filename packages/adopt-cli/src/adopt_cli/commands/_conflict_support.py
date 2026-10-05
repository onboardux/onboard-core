"""The two reads every surface needs to agree about a probe contradiction.

`adopt gaps`, `adopt pack`, `adopt handover` and `adopt ask` all report on Bet 4's
`conflict` rows, and until 2026-10-05 they disagreed: the pack listed a runbook
as "Contradicted by observation" while `ask` served it KNOWN and fresh
(independence transcript T3a). Each now applies the same rule
(`adopt_knowledge.rank_conflicts` / `contradicted_revisions`), fed by these two
reads.

Both are `table_rows` reads -- Build 4's report pattern -- so no query path is
added to any realized port and the plane's escape-coverage denominator does not
move.
"""

from typing import Any

__all__ = ["conflict_rows", "contradicted_now", "superseded_revisions"]


def conflict_rows(handle: Any) -> tuple[Any, ...]:
    """Every `conflict` row in the store."""
    from adopt_model import Conflict

    return tuple(handle.export_records().table_rows("conflict", Conflict))


def superseded_revisions(handle: Any) -> frozenset[str]:
    """Knowledge revision ids that are no longer their item's head.

    A conflict against one of these contradicts a claim nobody is making any
    more: the item was re-confirmed or rewritten after the probe drifted.
    """
    from adopt_model import KnowledgeItem, KnowledgeRevision

    records = handle.export_records()
    heads = {
        item.current_revision_id
        for item in records.table_rows("knowledge_item", KnowledgeItem)
        if item.current_revision_id is not None
    }
    return frozenset(
        revision.id
        for revision in records.table_rows("knowledge_revision", KnowledgeRevision)
        if revision.id not in heads
    )


def contradicted_now(handle: Any) -> frozenset[str]:
    """Head revisions a drifted probe currently contradicts."""
    from adopt_knowledge import contradicted_revisions

    return contradicted_revisions(conflict_rows(handle), superseded=superseded_revisions(handle))
