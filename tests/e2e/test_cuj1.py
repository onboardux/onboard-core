"""CUJ-1 -- a downstream item writes knowledge with bindings.

*Fails when* the sequence a downstream build item actually performs -- resolve
scope, build URIs, create an item with its first revision, bind it to each
identity, run `doctor` -- does not work end to end. *Matters because* this is the
journey items 1, 2 and 3 re-point onto, and each of them needs a full
re-derivation of identities against it; a substrate that works one facade at a
time and not in sequence would be discovered by the first team to try, after they
had committed to the migration. *No other instrument catches it because* every
unit test in this sprint drives one facade with the others stubbed out or unused,
and the defects that survive that are the ones at the seams -- a scope resolved to
the wrong depth, a head pointer set before its revision, an id minted in the
wrong place.

PRD §4 CUJ-1, five steps and one failure branch.
"""

import datetime as _dt
from collections.abc import Iterator

import pytest

from adopt_identity import build_uri, parse_uri
from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_scope import Scope
from adopt_store import (
    BindingRevisionDraft,
    KnowledgeRevisionDraft,
    doctor,
    open_store,
)
from adopt_store.api import SqliteStoreHandle

_START = _dt.datetime(2026, 8, 3, 9, 0, 0, tzinfo=_dt.UTC)

#: The referents the item binds to. Two, because the interesting half of CUJ-1
#: is step 4 -- *per identity* -- and one binding cannot show a per-identity
#: decision being made.
_REFERENTS = (
    ("endpoint", None, "POST /v1/orders"),
    ("db_field", "public", ("orders", "total_cents")),
)


@pytest.fixture
def store(tmp_path: object) -> Iterator[SqliteStoreHandle]:
    handle = open_store(
        tmp_path / "store.db",  # type: ignore[operator]
        migrate=True,
        clock=ManualClock(_START),
    )
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


@pytest.mark.e2e
def test_cuj1_a_downstream_item_writes_knowledge_with_bindings(
    store: SqliteStoreHandle, scope: Scope
) -> None:
    # Step 1 -- scope resolves to four ids and four slugs. Both halves, because
    # the URI builder needs the slugs and the store needs the ids, and a resolve
    # that returned one would push a lookup into whatever loop the caller wrote.
    assert scope.depth == 4
    assert scope.slugs() == ("northwind", "acme-erp", "orders-api", "prod")
    for level in (scope.firm, scope.engagement, scope.system, scope.environment):
        assert level is not None
        assert level.id != level.slug, "ids and slugs are different things"

    # Step 2 -- a URI per referent, and each validates.
    identities = []
    for kind, namespace, key in _REFERENTS:
        uri = build_uri(scope, kind, namespace, key)
        assert parse_uri(uri).render() == uri
        identity = store.identities().observe(
            scope=scope, kind=kind, namespace=namespace, key=key, extractor="test-extractor"
        )
        assert identity.uri == uri
        identities.append(identity)

    # Step 3 -- item and first revision, head pointer set in the same transaction.
    item_id, revision_id = store.items().create(
        scope=scope,
        kind="procedure",
        title="How a refund is issued",
        revision=KnowledgeRevisionDraft(
            authority_class="human_confirmed",
            body_md="Call POST /v1/orders/{id}/refund.",
            verification="verified",
        ),
        actor_id="act_fde",
    )
    item = store.items().get(item_id)
    assert item is not None
    assert item.current_revision_id == revision_id
    assert item.system_id == scope.system.id if scope.system else False

    # Step 4 -- a binding per identity, `is_load_bearing` set explicitly.
    load_bearing = {identities[0].id: True, identities[1].id: False}
    for identity in identities:
        binding_id, binding_revision_id = store.bindings().create(
            item_id=item_id,
            identity_id=identity.id,
            is_load_bearing=load_bearing[identity.id],
            revision=BindingRevisionDraft(status="active", locator_rung=1, extractor="test"),
            actor_id="act_fde",
        )
        binding = store.bindings().get(binding_id)
        assert binding is not None
        assert binding.current_revision_id == binding_revision_id
        assert binding.is_load_bearing is load_bearing[identity.id], (
            "the writer's choice is recorded, not the schema default"
        )

    # Step 5 -- zero findings.
    findings = doctor(store)
    assert findings == [], [finding.render() for finding in findings]


@pytest.mark.e2e
def test_cuj1_failure_branch_a_second_revision_without_the_head_is_refused(
    store: SqliteStoreHandle, scope: Scope
) -> None:
    """The item appends without `supersedes_revision_id`.

    The helper refuses with `REVISION_CHAIN_FORK`, **names the current head** so
    the caller can retry, and writes nothing. All three matter: a refusal that
    did not name the head would leave the caller unable to proceed, and one that
    half-wrote would leave the chain in the state the refusal exists to prevent.
    """
    item_id, first_revision_id = store.items().create(
        scope=scope,
        kind="answer",
        title="Refund window",
        revision=KnowledgeRevisionDraft(authority_class="artifact_observed", body_md="30 days"),
    )
    records = store.revision_records()
    before = list(records.revision_ids("knowledge_revision", item_id))

    with pytest.raises(AdoptError) as raised:
        store.revisions().append_revision(
            parent_id=item_id,
            draft=KnowledgeRevisionDraft(authority_class="artifact_observed", body_md="60 days"),
            expected_head_id=None,
        )

    assert raised.value.code is ErrorCode.REVISION_CHAIN_FORK
    assert first_revision_id in raised.value.message, "the caller cannot retry without the head"
    assert list(records.revision_ids("knowledge_revision", item_id)) == before, "nothing written"

    item = store.items().get(item_id)
    assert item is not None
    assert item.current_revision_id == first_revision_id
    assert doctor(store) == []
