"""CUJ-2 -- a cosmetic change; the binding survives.

*Fails when* a change that alters only presentation produces a different URI, a
second `identity` row, or a new revision -- or when a genuine move rewrites the
old URI instead of aliasing it. *Matters because* this journey **is** Bet 1:
knowledge binds to identity, never to rendering. It is also the G2 gate's
precondition, so a substrate that fails it makes the whole silent-repair line
undeliverable. *No other instrument catches it because* the URI properties prove
the name is deterministic without touching the store, and the store tests prove
the chain is well formed without asking whether a rename should have moved
anything at all.

PRD §4 CUJ-2, four steps and one failure branch.
"""

import datetime as _dt
from collections.abc import Iterator

import pytest

from adopt_obs import ManualClock
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft, doctor, open_store
from adopt_store.api import SqliteStoreHandle

_START = _dt.datetime(2026, 8, 3, 9, 0, 0, tzinfo=_dt.UTC)
#: Stated, never waited for: sleeps in tests are banned (implementation spec §5).
_A_DAY_LATER = _dt.timedelta(days=1)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(_START)


@pytest.fixture
def store(tmp_path: object, clock: ManualClock) -> Iterator[SqliteStoreHandle]:
    handle = open_store(tmp_path / "store.db", migrate=True, clock=clock)  # type: ignore[operator]
    yield handle
    handle.close()


@pytest.fixture
def scope(store: SqliteStoreHandle) -> Scope:
    facade = store.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    return facade.resolve("northwind/acme-erp/orders-api/prod")


def _bound_item(store: SqliteStoreHandle, scope: Scope, identity_id: str) -> str:
    item_id, _ = store.items().create(
        scope=scope,
        kind="answer",
        title="How a refund is issued",
        revision=KnowledgeRevisionDraft(authority_class="human_confirmed", body_md="v1"),
    )
    store.bindings().create(
        item_id=item_id,
        identity_id=identity_id,
        is_load_bearing=True,
        revision=BindingRevisionDraft(status="active", locator_rung=1),
    )
    return item_id


@pytest.mark.e2e
def test_cuj2_a_cosmetic_change_leaves_the_binding_untouched(
    store: SqliteStoreHandle, scope: Scope, clock: ManualClock
) -> None:
    # Step 1 -- the referent is renamed in presentation only. The `name` of the
    # scope is free text and moves; the slugs and the key, which are what the URI
    # is built from, do not.
    identity = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )
    item_id = _bound_item(store, scope, identity.id)
    bindings_before = store.bindings().for_identity(identity.id)
    revisions_before = store.revision_records().revision_ids("identity_revision", identity.id)
    covered_cache_before = identity.covered_cache

    clock.advance(_A_DAY_LATER)

    # Step 2 -- the identical URI; the row is matched by `uri`, not re-created.
    again = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )
    assert again.uri == identity.uri
    assert again.id == identity.id

    # Step 3 -- `last_seen` advances; no new revision, because nothing about the
    # identity changed.
    assert again.last_seen == _START + _A_DAY_LATER
    assert again.first_seen == _START
    assert (
        store.revision_records().revision_ids("identity_revision", identity.id) == revisions_before
    )

    # Step 4 -- bindings resolve unchanged, and the coverage cache is untouched.
    bindings_after = store.bindings().for_identity(identity.id)
    assert [b.id for b in bindings_after] == [b.id for b in bindings_before]
    assert [b.current_revision_id for b in bindings_after] == [
        b.current_revision_id for b in bindings_before
    ]
    assert again.covered_cache == covered_cache_before
    assert store.items().get(item_id) is not None
    assert doctor(store) == []


@pytest.mark.e2e
def test_cuj2_failure_branch_a_genuine_move_aliases_and_never_rewrites(
    store: SqliteStoreHandle, scope: Scope, clock: ManualClock
) -> None:
    """A real move: a new URI, a new identity, and a `moved` revision on the old.

    **The old URI is never rewritten.** That is what makes a bundle a client
    exported last year still resolvable, and it is the single assertion this
    branch exists for -- everything else about a move is recoverable, and that
    is not.
    """
    source = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )
    original_uri = source.uri
    item_id = _bound_item(store, scope, source.id)
    clock.advance(_A_DAY_LATER)

    destination = store.identities().move(
        identity_id=source.id,
        scope=scope,
        kind="endpoint",
        namespace=None,
        key="POST /v2/orders",
    )

    assert destination.id != source.id
    assert destination.uri != original_uri

    reread = store.identities().get(source.id)
    assert reread is not None
    assert reread.uri == original_uri, "the old URI is never rewritten"

    rows = store.backend.query(
        "SELECT status, alias_of_identity_id FROM identity_revision "
        "WHERE identity_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (source.id,),
    )
    assert rows[0]["status"] == "moved"
    assert rows[0]["alias_of_identity_id"] == destination.id

    # The old binding is still readable and still points at the old identity:
    # nothing about a move is retroactive, and coverage provenance depends on
    # being able to read what was true before it.
    still_bound = store.bindings().for_identity(source.id)
    assert len(still_bound) == 1
    assert still_bound[0].item_id == item_id
    assert doctor(store) == []
