"""The append-only revision model: retirement, forks, dangling heads, and the
absent update method.

*Fails when* a revision chain can acquire two heads, when a head pointer can be
advanced without its revision (or the reverse), when retirement deletes rather
than appends, or when a facade grows a way to mutate a `*_revision` row.
*Matters because* the chain **is** the audit record: the question a client asks
in a dispute is "what did this say in March", and every one of those failures
makes it permanently unanswerable -- no later repair recovers the answer.
*No other instrument catches it because* `no-revision-update` reads source text
and so cannot see a chain forked at runtime by two well-formed writers, and the
grep gate cannot see a head pointer advanced without its revision at all.

The four families are tested together, one parametrised row each, because they
differ in exactly three places -- terminal status, head pointer, parent column --
and a test file per family would assert the same invariant four times while
hiding which of the three differences was actually exercised.
"""

import datetime as _dt
from collections.abc import Iterator
from typing import Any

import pytest

from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_scope import Scope
from adopt_store import (
    BindingRevisionDraft,
    IdentityRevisionDraft,
    KnowledgeRevisionDraft,
    ProbeDefinitionRevisionDraft,
    UnknownFamilyError,
    doctor,
    open_store,
)
from adopt_store.api import SqliteStoreHandle
from adopt_store.revisions import FAMILIES, RETIRED_ITEM_FRESHNESS

_START = _dt.datetime(2026, 8, 3, 9, 0, 0, tzinfo=_dt.UTC)


@pytest.fixture
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SqliteStoreHandle]:
    path = tmp_path_factory.mktemp("revisions") / "store.db"
    handle = open_store(path, migrate=True, clock=ManualClock(_START))
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


def _make_item(store: SqliteStoreHandle, scope: Scope, title: str = "How refunds work") -> str:
    item_id, _ = store.items().create(
        scope=scope,
        kind="answer",
        title=title,
        revision=KnowledgeRevisionDraft(authority_class="human_confirmed", body_md="v1"),
    )
    return item_id


def _make_identity(store: SqliteStoreHandle, scope: Scope, key: str = "POST /v1/orders") -> str:
    return store.identities().observe(scope=scope, kind="endpoint", namespace=None, key=key).id


def _make_binding(store: SqliteStoreHandle, scope: Scope) -> str:
    binding_id, _ = store.bindings().create(
        item_id=_make_item(store, scope),
        identity_id=_make_identity(store, scope),
        is_load_bearing=True,
    )
    return binding_id


def _make_probe(store: SqliteStoreHandle, scope: Scope) -> str:
    probe_id, _ = store.probes().create(
        scope=scope,
        name="orders round trip",
        revision=ProbeDefinitionRevisionDraft(
            interaction="POST /v1/orders",
            safe_path="sandbox",
            diff_method="exact",
            capability_manifest="{}",
        ),
    )
    return probe_id


_PARENT_BUILDERS = {
    "identity": _make_identity,
    "knowledge": _make_item,
    "binding": _make_binding,
    "probe_definition": _make_probe,
}

_NEXT_DRAFTS: dict[str, Any] = {
    "identity": IdentityRevisionDraft(status="active", extractor="probe"),
    "knowledge": KnowledgeRevisionDraft(authority_class="artifact_observed", body_md="v2"),
    "binding": BindingRevisionDraft(status="active", locator_rung=2),
    "probe_definition": ProbeDefinitionRevisionDraft(
        interaction="POST /v1/orders",
        safe_path="shadow",
        diff_method="contract_delta",
        capability_manifest="{}",
    ),
}

_FAMILY_NAMES = ("identity", "knowledge", "binding", "probe_definition")


# --------------------------------------------------------------------------
# Retirement, one row per family.
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("family_name", _FAMILY_NAMES)
def test_retire_appends_a_terminal_revision_and_never_deletes(
    store: SqliteStoreHandle, scope: Scope, family_name: str
) -> None:
    """PRD F6.7: `retired` and `dead` are statuses, never deletions.

    A retired binding must stay readable, because coverage provenance depends on
    it -- "why was this covered in March" has no answer in a store that deletes.
    """
    parent_id = _PARENT_BUILDERS[family_name](store, scope)
    family = FAMILIES[parent_id.split("_", 1)[0]]
    records = store.revision_records()
    before = list(records.revision_ids(family.revision_table, parent_id))

    terminal_id = store.revisions().retire(parent_id=parent_id, reason="referent removed")

    after = list(records.revision_ids(family.revision_table, parent_id))
    assert set(before) < set(after), "retirement appends; it never removes"
    assert terminal_id in after
    assert store.revisions().current_head(parent_id) == terminal_id


@pytest.mark.unit
@pytest.mark.parametrize(
    ("family_name", "expected_status"),
    [
        pytest.param("identity", "dead", id="identity-dead"),
        pytest.param("binding", "retired", id="binding-retired"),
        pytest.param("probe_definition", "retired", id="probe-retired-CR-33"),
    ],
)
def test_the_terminal_revision_carries_the_family_status(
    store: SqliteStoreHandle, scope: Scope, family_name: str, expected_status: str
) -> None:
    """Contracts §5 obligation 4, for the three families that carry a status."""
    parent_id = _PARENT_BUILDERS[family_name](store, scope)
    family = FAMILIES[parent_id.split("_", 1)[0]]

    terminal_id = store.revisions().retire(parent_id=parent_id, reason="gone")

    rows = store.backend.query(
        f"SELECT status FROM {family.revision_table} WHERE id = ?",  # noqa: S608
        (terminal_id,),
    )
    assert rows[0]["status"] == expected_status


@pytest.mark.unit
def test_a_retired_knowledge_item_carries_its_terminal_state_on_the_parent(
    store: SqliteStoreHandle, scope: Scope
) -> None:
    """`knowledge_revision` has no status column; the parent's freshness is the
    terminal state, which contracts §5 obligation 6 permits."""
    item_id = _make_item(store, scope)

    store.revisions().retire(parent_id=item_id, reason="procedure withdrawn")

    item = store.items().get(item_id)
    assert item is not None
    assert item.freshness_state == RETIRED_ITEM_FRESHNESS


# --------------------------------------------------------------------------
# Optimistic concurrency.
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("family_name", _FAMILY_NAMES)
def test_two_writers_at_one_head_leave_exactly_one_winner(
    store: SqliteStoreHandle, scope: Scope, family_name: str
) -> None:
    """Contracts §5 obligation 2, driven as the race it describes.

    Both writers read the same head -- which is exactly what two processes do --
    and then both append. The second must be refused, or the chain has two heads
    and the store can no longer say what the current revision is.

    Deliberately not threaded: the defect is a *lost update*, and reading the head
    twice before writing either reproduces it exactly. A thread would add
    scheduling noise to a test whose whole point is determinism.
    """
    parent_id = _PARENT_BUILDERS[family_name](store, scope)
    writer = store.revisions()
    head_seen_by_both = writer.current_head(parent_id)

    winner = writer.append_revision(
        parent_id=parent_id, draft=_NEXT_DRAFTS[family_name], expected_head_id=head_seen_by_both
    )

    with pytest.raises(AdoptError) as raised:
        writer.append_revision(
            parent_id=parent_id,
            draft=_NEXT_DRAFTS[family_name],
            expected_head_id=head_seen_by_both,
        )

    assert raised.value.code is ErrorCode.REVISION_CHAIN_FORK
    assert writer.current_head(parent_id) == winner
    assert doctor(store) == [], "the refused write must leave no trace"


@pytest.mark.unit
def test_the_fork_message_names_both_heads(store: SqliteStoreHandle, scope: Scope) -> None:
    """A caller has to retry, and cannot without being told the current head."""
    item_id = _make_item(store, scope)
    writer = store.revisions()
    stale = writer.current_head(item_id)
    writer.append_revision(
        parent_id=item_id, draft=_NEXT_DRAFTS["knowledge"], expected_head_id=stale
    )
    current = writer.current_head(item_id)

    with pytest.raises(AdoptError) as raised:
        writer.append_revision(
            parent_id=item_id, draft=_NEXT_DRAFTS["knowledge"], expected_head_id=stale
        )

    assert str(stale) in raised.value.message
    assert str(current) in raised.value.message


@pytest.mark.unit
def test_a_refused_append_writes_nothing(store: SqliteStoreHandle, scope: Scope) -> None:
    """ "Nothing is written" is the half of CUJ-1's failure branch that is easy to lose."""
    item_id = _make_item(store, scope)
    records = store.revision_records()
    before = list(records.revision_ids("knowledge_revision", item_id))

    with pytest.raises(AdoptError):
        store.revisions().append_revision(
            parent_id=item_id, draft=_NEXT_DRAFTS["knowledge"], expected_head_id="krev_nonexistent"
        )

    assert list(records.revision_ids("knowledge_revision", item_id)) == before


@pytest.mark.unit
def test_an_id_from_another_table_has_no_chain_to_append_to(
    store: SqliteStoreHandle, scope: Scope
) -> None:
    with pytest.raises(UnknownFamilyError):
        store.revisions().retire(parent_id=scope.firm.id, reason="wrong table")


# --------------------------------------------------------------------------
# The absent update method (PRD N9, contracts §10.3).
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("facade_name", ["identities", "items", "bindings", "probes", "revisions"])
def test_no_facade_exposes_an_update_or_delete_on_a_revision(
    store: SqliteStoreHandle, facade_name: str
) -> None:
    """N9 names a *facade surface* test beside the grep gate, and this is it.

    The gate reads source text, so it cannot see a method named `edit` that
    happens to issue an UPDATE through a helper. This reads the surface a caller
    actually has: if there is no method, there is nothing to call.
    """
    facade = getattr(store, facade_name)()
    surface = {name for name in dir(facade) if not name.startswith("_")}

    forbidden = {name for name in surface if name.startswith(("update", "delete", "edit", "set_"))}
    assert forbidden == set(), (
        f"{facade_name}() exposes {sorted(forbidden)}; a revision family is append-only "
        "and retirement is a terminal-status revision"
    )


@pytest.mark.unit
def test_the_revision_port_offers_no_update_or_delete(store: SqliteStoreHandle) -> None:
    """The port beneath the facades, for the same reason and one layer down."""
    surface = {name for name in dir(store.revision_records()) if not name.startswith("_")}

    assert not any(name.startswith(("update", "delete")) for name in surface), sorted(surface)


# --------------------------------------------------------------------------
# `store doctor` -- seeded defects, one row per finding.
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_doctor_reports_nothing_on_a_healthy_store(store: SqliteStoreHandle, scope: Scope) -> None:
    """CUJ-1 step 5. Without this row, the two findings below could both be
    firing on every store and the tests would still pass."""
    _make_binding(store, scope)
    _make_probe(store, scope)

    assert doctor(store) == []


@pytest.mark.unit
def test_doctor_reports_a_dangling_head_pointer(store: SqliteStoreHandle, scope: Scope) -> None:
    """CR-07 removed the foreign key; this is the integrity it traded away.

    Seeded by writing the pointer directly, which is what a writer bypassing
    `advance_head` would do.
    """
    item_id = _make_item(store, scope)
    with store.backend.transaction():
        store.backend.execute(
            "UPDATE knowledge_item SET current_revision_id = ? WHERE id = ?",
            ("krev_01JZZZZZZZZZZZZZZZZZZZZZZZ", item_id),
        )

    findings = doctor(store)

    assert [f.code for f in findings] == [ErrorCode.REVISION_HEAD_DANGLING]
    assert findings[0].subject_id == item_id
    assert "names no row" in findings[0].detail


@pytest.mark.unit
@pytest.mark.parametrize("family_name", _FAMILY_NAMES)
def test_doctor_reports_a_forked_chain(
    store: SqliteStoreHandle, scope: Scope, family_name: str
) -> None:
    """A fork in the wild means a writer bypassed `append_revision`.

    Seeded by inserting a second revision that supersedes nothing -- the exact
    row a writer that ignored `expected_head_id` would produce. Checked for
    `identity` too, whose head is derived: a derived head resolving to two
    revisions is as forked as a stored one and harder to notice, because there is
    no pointer to look at.
    """
    parent_id = _PARENT_BUILDERS[family_name](store, scope)
    family = FAMILIES[parent_id.split("_", 1)[0]]
    _insert_orphan_revision(store, family.revision_table, family.parent_column, parent_id)

    findings = [f for f in doctor(store) if f.code is ErrorCode.REVISION_CHAIN_FORK]

    assert [f.subject_id for f in findings] == [parent_id]
    assert "exactly one head" in findings[0].detail


def _insert_orphan_revision(
    store: SqliteStoreHandle, table: str, parent_column: str, parent_id: str
) -> None:
    """Copy the head row under a new id, superseding nothing -- a second head."""
    rows = store.backend.query(
        f"SELECT * FROM {table} WHERE {parent_column} = ? LIMIT 1",  # noqa: S608
        (parent_id,),
    )
    values = dict(rows[0])
    values["id"] = values["id"][:-4] + "ZZZZ"
    values["supersedes_revision_id"] = None
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    with store.backend.transaction():
        store.backend.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",  # noqa: S608
            tuple(values[column] for column in columns),
        )
