"""A command's own coverage change refreshes the cache; somebody else's never does.

*Fails when* a write the product made on purpose leaves `covered_cache`
disagreeing -- or when the refresh erases a disagreement it did not cause.
*Matters because* the first made `COVERAGE_CACHE_DISAGREEMENT` fire after every
`ingest`, `bind`, `answer`, review resolution and `refresh` (validation
transcripts, 2026-10-03 and 2026-10-05), training operators to ignore the one
alarm whose job is to page; the second would destroy the evidence of a real
defect, which `store.py`'s incident rule forbids. *No other instrument catches
it because* `recompute_coverage` is correct either way: only the cache's state
after a wrapped write distinguishes the two failures.
"""

from collections.abc import Callable

import pytest

from adopt_cli.commands._coverage_support import coverage_cache_kept_current
from adopt_coverage import recompute_coverage
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft
from adopt_store.api import SqliteStoreHandle


def _bind_confirmed_item(store: SqliteStoreHandle, scope: Scope, identity_id: str) -> None:
    item_id, _ = store.items().create(
        scope=scope,
        kind="answer",
        title="How a refund is issued",
        revision=KnowledgeRevisionDraft(
            authority_class="human_confirmed", body_md="v1", verification="verified"
        ),
    )
    with store.backend.transaction():
        store.backend.execute(
            "INSERT INTO audience_tag (item_id, audience) VALUES (?, ?)", (item_id, "engineering")
        )
    store.bindings().create(
        item_id=item_id,
        identity_id=identity_id,
        is_load_bearing=True,
        revision=BindingRevisionDraft(status="active", locator_rung=1),
    )


@pytest.mark.unit
def test_a_wrapped_write_leaves_an_agreeing_cache_agreeing(
    s4_store: SqliteStoreHandle, s4_scope: Scope, add_boundary: Callable[..., str]
) -> None:
    assert s4_scope.system is not None
    system_id = s4_scope.system.id
    add_boundary(system_id=system_id)
    identity = s4_store.identities().observe(
        scope=s4_scope, kind="endpoint", namespace=None, key="POST /v1/refunds"
    )

    with coverage_cache_kept_current(s4_store, system_id):
        _bind_confirmed_item(s4_store, s4_scope, identity.id)

    result = recompute_coverage(s4_store.coverage_records(), system_id)
    assert result.covered == 1
    assert result.disagreements == ()


@pytest.mark.unit
def test_a_disagreement_that_predates_the_write_survives_it(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    add_boundary: Callable[..., str],
    inject_cache: Callable[..., None],
) -> None:
    assert s4_scope.system is not None
    system_id = s4_scope.system.id
    add_boundary(system_id=system_id)
    tampered = s4_store.identities().observe(
        scope=s4_scope, kind="endpoint", namespace=None, key="GET /v1/orders"
    )
    inject_cache(identity_id=tampered.id, covered=True)
    identity = s4_store.identities().observe(
        scope=s4_scope, kind="endpoint", namespace=None, key="POST /v1/refunds"
    )

    with coverage_cache_kept_current(s4_store, system_id):
        _bind_confirmed_item(s4_store, s4_scope, identity.id)

    result = recompute_coverage(s4_store.coverage_records(), system_id)
    assert tampered.id in {entry.identity_id for entry in result.disagreements}
