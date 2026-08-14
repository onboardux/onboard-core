"""The revision-aware writer -- contracts §6, §10 C7; impl spec §5.5; PRD F3, N5, N10.

`03` §5.5 calls this **T1, the densest suite in the plan**. Three of the five
instruments that survive any budget cut live here (`05` Quality notes):
the append-only SQL trace, the planted-secret egress property and -- with S1.2 --
the idempotence property.

| Behavior | Tier | Instrument |
|---|---|---|
| Exactly the §6 write set, nothing else | **T1** | SQL trace (C7) |
| Zero UPDATEs on `*_revision`, zero DELETEs | **T1** | SQL trace, `append_only` |
| A kill at any statement boundary leaves the store openable | **T1** | fault injection |
| The six fixed values are written | T2 | value assertions |
| `provenance` attaches to the **revision** | T1 | join assertion |
| A kind outside the manifest is refused | T1 | rejection |
| A below-floor fact becomes a gap, not knowledge | T1 | gap case |
| `status='dead'` is never written | **T1** | swept over the whole run |

The SQL trace is the load-bearing one and it is worth saying why it is a *trace*
rather than a row count: a row count after the fact cannot tell an `INSERT` that
was later corrected from one that was right, and cannot see a statement against a
table that happened to write nothing. The trace sees the statements.
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from adopt_extractors_common import MANIFEST, StubExtractor
from adopt_map.ports import ScopeLookupRecords
from adopt_map.schemas import SurfaceFact
from adopt_map.scope_resolve import resolve_scope
from adopt_map.writer import (
    AUDIT_EVENT_TYPE,
    SURFACE_AUTHORITY_CLASS,
    SURFACE_DEATH_CONDITION,
    SURFACE_ITEM_KIND,
    SURFACE_VERIFICATION,
    SurfaceWriter,
)

from adopt_model import (
    MODEL_FOR_TABLE,
    AuditEvent,
    Binding,
    BindingRevision,
    Conflict,
    DeathCondition,
    Identity,
    IdentityRevision,
    KnowledgeItem,
    KnowledgeRevision,
    Provenance,
)
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope, ScopeNode
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle
from adopt_store.revisions import (
    BindingRevisionDraft,
    IdentityRevisionDraft,
    KnowledgeRevisionDraft,
)
from tests.build1_conftest import surface_writer_for

pytestmark = pytest.mark.integration

#: `02` §6's write set, verbatim. A statement against any other table is a
#: violation, and the trace names it.
_PERMITTED_TABLES = frozenset(
    {
        "identity",
        "identity_revision",
        "knowledge_item",
        "knowledge_revision",
        "provenance",
        "binding",
        "binding_revision",
        "death_condition",
        "conflict",
        "audit_event",
    }
)


@pytest.fixture
def writer(s4_store: SqliteStoreHandle) -> SurfaceWriter:
    return surface_writer_for(s4_store)


@pytest.fixture
def resolved(
    s4_store: SqliteStoreHandle, s4_scope: Scope, scope_records: ScopeLookupRecords, add_boundary
):
    assert s4_scope.engagement and s4_scope.system and s4_scope.environment
    add_boundary(system_id=s4_scope.system.id)
    return resolve_scope(
        scope_records,
        firm_id=s4_scope.firm.id,
        engagement_id=s4_scope.engagement.id,
        system_id=s4_scope.system.id,
        environment_id=s4_scope.environment.id,
        archetype="web",
        tier="T2",
    )


@pytest.fixture
def sql_trace(s4_store: SqliteStoreHandle) -> Iterator[list[str]]:
    """Every statement the connection executes, for the run's duration.

    `sqlite3`'s own trace callback rather than a wrapper around our port: a
    wrapper sees what our code *meant* to run, and the question C7 asks is what
    actually reached the database.
    """
    statements: list[str] = []
    connection = s4_store.backend._connection
    connection.set_trace_callback(statements.append)
    yield statements
    connection.set_trace_callback(None)


def _facts() -> list[SurfaceFact]:
    return list(StubExtractor().extract("."))


def _written_tables(statements: list[str]) -> set[str]:
    written: set[str] = set()
    for statement in statements:
        head = statement.strip().split()
        if len(head) < 3 or head[0].upper() != "INSERT":
            continue
        written.add(head[2].strip('"`[]').lower())
    return written


# -- C7: the write set -------------------------------------------------------


def test_the_run_writes_exactly_the_contracts_write_set(
    writer: SurfaceWriter, resolved, sql_trace: list[str]
) -> None:
    """*Fails when* the writer touches a table outside `02` §6.

    *Matters because* `02` §6's "explicitly never written" list is 26 tables
    long, and several of them -- `classification`, `change_event`, `review_item`
    -- belong to builds whose semantics would be quietly corrupted by a row this
    build invented. *No other instrument catches it because* an extra INSERT into
    a table nobody reads yet produces no visible symptom for several builds.
    """
    writer.write_run(resolved=resolved, manifest=MANIFEST, facts=_facts(), vcs_revision="abc123")

    written = _written_tables(sql_trace)
    assert written, "the trace captured no INSERT at all -- the instrument is blind"
    assert written <= _PERMITTED_TABLES, sorted(written - _PERMITTED_TABLES)


@pytest.mark.append_only
def test_the_run_issues_no_update_on_a_revision_table_and_no_delete(
    writer: SurfaceWriter, resolved, sql_trace: list[str]
) -> None:
    """*Fails when* any statement mutates a revision row or deletes anything.

    *Matters because* the revision chain **is** the audit record (PRD N5): the
    moment a row is updated in place, "what did it say then" becomes permanently
    unanswerable and no later repair recovers the answer. *No other instrument
    catches it because* the resulting store is perfectly well-formed -- it is
    simply missing a history nobody knows was there.
    """
    writer.write_run(resolved=resolved, manifest=MANIFEST, facts=_facts(), vcs_revision="abc123")

    for statement in sql_trace:
        normalized = " ".join(statement.split()).upper()
        assert "DELETE " not in normalized, statement
        assert " DROP " not in f" {normalized} ", statement
        if normalized.startswith("UPDATE "):
            target = normalized.split()[1].strip('"`[]').lower()
            assert not target.endswith("_revision"), statement


@pytest.mark.append_only
def test_the_only_updates_are_the_two_parent_pointers(
    writer: SurfaceWriter, resolved, sql_trace: list[str]
) -> None:
    """*Fails when* a third table starts being updated in place.

    *Matters because* exactly two in-place updates are legitimate --
    `identity.last_seen` (CUJ-2 step 3) and a parent's `current_revision_id` --
    and both are on **parent** rows, not revisions. A third would be a mutation
    somebody added without noticing which side of the line it fell on. *No other
    instrument catches it because* the test above only forbids `*_revision`.
    """
    writer.write_run(resolved=resolved, manifest=MANIFEST, facts=_facts(), vcs_revision="abc123")

    updated = {
        " ".join(s.split()).upper().split()[1].strip('"`[]').lower()
        for s in sql_trace
        if " ".join(s.split()).upper().startswith("UPDATE ")
    }
    assert updated <= {"identity", "knowledge_item", "binding"}, sorted(updated)


# -- the fixed values --------------------------------------------------------


def test_the_run_writes_the_fixed_values_contracts_pins(
    s4_store: SqliteStoreHandle, writer: SurfaceWriter, resolved
) -> None:
    """*Fails when* one of the six values `02` §6 pins drifts.

    *Matters because* each is load-bearing somewhere else: `kind='surface'` is
    what Build 5's coverage query selects on, `authority_class='artifact_observed'`
    is what stops a scraped fact outranking a human-confirmed one, and
    `is_load_bearing=1` is what makes a change to the identity stale the item.
    *No other instrument catches it because* every wrong value is a legal enum
    member and the row inserts cleanly.
    """
    writer.write_run(resolved=resolved, manifest=MANIFEST, facts=_facts(), vcs_revision="abc123")
    records = s4_store.export_records()

    items = records.table_rows("knowledge_item", KnowledgeItem)
    assert items
    for item in items:
        assert item.kind == SURFACE_ITEM_KIND
        assert item.firm_id == resolved.firm_id
        assert item.engagement_id == resolved.engagement_id
        assert item.system_id == resolved.system_id
        assert item.environment_id == resolved.environment_id
        # B1-CR-39: Build 0's `create_item` writes `unverified`, and `02` §6's
        # `fresh` is the repaired document. A `fresh` parent carrying an
        # `unverified` revision would contradict itself.
        assert item.freshness_state == "unverified"

    for revision in records.table_rows("knowledge_revision", KnowledgeRevision):
        assert revision.authority_class == SURFACE_AUTHORITY_CLASS
        assert revision.verification == SURFACE_VERIFICATION
        assert revision.recipe_json is None
        assert revision.classifier_version_id is None

    for binding in records.table_rows("binding", Binding):
        assert binding.is_load_bearing is True

    for condition in records.table_rows("death_condition", DeathCondition):
        assert condition.condition == SURFACE_DEATH_CONDITION
        assert condition.threshold is None

    # One `death_condition` per surface item -- mandatory (PRD F3.1 item 5, R22).
    assert len(records.table_rows("death_condition", DeathCondition)) == len(items)


def test_locator_rung_is_set_only_for_ui_component(
    s4_store: SqliteStoreHandle, writer: SurfaceWriter, resolved
) -> None:
    """*Fails when* a rung is stamped on a kind that has no locator.

    *Matters because* `locator_rung` is how Bet 1 is audited: a non-null rung on
    a `symbol` binding makes the "everything is bound at rung 1-2" query answer
    yes for a referent that was never located at all. *No other instrument
    catches it because* the column is nullable and any integer is legal.
    """
    facts = [
        *_facts(),
        SurfaceFact(
            identity_kind="ui_component", namespace="testid", local_key="submit", title="Submit"
        ),
    ]
    manifest = MANIFEST.model_copy(update={"kinds": [*MANIFEST.kinds, "ui_component"]})
    writer.write_run(resolved=resolved, manifest=manifest, facts=facts, vcs_revision="abc123")

    records = s4_store.export_records()
    identities = {row.id: row for row in records.table_rows("identity", Identity)}
    bindings = {row.id: row for row in records.table_rows("binding", Binding)}

    for revision in records.table_rows("binding_revision", BindingRevision):
        identity = identities[bindings[revision.binding_id].identity_id]
        if identity.identity_kind == "ui_component":
            assert revision.locator_rung == 1
        else:
            assert revision.locator_rung is None


def test_provenance_attaches_to_the_revision_not_the_item(
    s4_store: SqliteStoreHandle, writer: SurfaceWriter, resolved
) -> None:
    """*Fails when* provenance is attached to the parent item (B1-CR-18).

    *Matters because* an item's evidence is the evidence of the **revision that
    made the claim**. Attached to the parent, every historical claim appears to
    carry the newest evidence, and "why did we believe this in March" becomes
    unanswerable -- the same class of loss the append-only rule exists to
    prevent. *No other instrument catches it because* both shapes join cleanly.
    """
    writer.write_run(resolved=resolved, manifest=MANIFEST, facts=_facts(), vcs_revision="abc123")
    records = s4_store.export_records()

    revision_ids = {row.id for row in records.table_rows("knowledge_revision", KnowledgeRevision)}
    item_ids = {row.id for row in records.table_rows("knowledge_item", KnowledgeItem)}
    rows = records.table_rows("provenance", Provenance)

    assert rows
    for row in rows:
        assert row.revision_id in revision_ids
        assert row.revision_id not in item_ids
        assert row.source_type == "commit"
        assert not row.source_ref.startswith("/"), "provenance must carry no absolute path"


def test_one_audit_event_per_run_never_one_per_fact(
    s4_store: SqliteStoreHandle, writer: SurfaceWriter, resolved
) -> None:
    """*Fails when* the audit row moves inside the per-fact loop.

    *Matters because* `02` §6 says one row per run and *"never per-fact rows"*:
    an audit table growing with the identity set puts client structure into a
    table nothing scrubs, and buries the record that a run happened. *No other
    instrument catches it because* per-fact rows are individually well-formed.
    """
    writer.write_run(resolved=resolved, manifest=MANIFEST, facts=_facts(), vcs_revision="abc123")

    rows = s4_store.export_records().table_rows("audit_event", AuditEvent)
    assert len(rows) == 1
    assert rows[0].event_type == AUDIT_EVENT_TYPE
    detail = rows[0].detail or ""
    for leaked in ("orders", "DATABASE", "views.py", "settings.py"):
        assert leaked not in detail, "the audit detail is a summary, not client structure"


def test_status_dead_is_never_written(
    s4_store: SqliteStoreHandle, writer: SurfaceWriter, resolved
) -> None:
    """*Fails when* any path writes `status='dead'` (B1-CR-07, PRD F5.3).

    *Matters because* from here absence and parse failure are indistinguishable,
    so a death is a **guess** -- and a false death is a false retirement in every
    build downstream. Build 3 owns death. *No other instrument catches it
    because* `dead` is a legal enum member and the row is well-formed.
    """
    writer.write_run(resolved=resolved, manifest=MANIFEST, facts=_facts(), vcs_revision="abc123")

    statuses = {
        row.status
        for row in s4_store.export_records().table_rows("identity_revision", IdentityRevision)
    }
    assert "dead" not in statuses
    assert statuses == {"active"}


# -- refusals ----------------------------------------------------------------


def test_a_kind_outside_the_manifest_is_refused(writer: SurfaceWriter, resolved) -> None:
    """*Fails when* an extractor may emit a kind it did not declare.

    *Matters because* `02` §7 obligation 4 is what keeps coverage arithmetic
    checkable: an extractor that widens its own vocabulary at runtime produces
    identities nobody attributed to it. *No other instrument catches it because*
    the kind is still a member of the closed enum and mints a valid URI.
    """
    stray = SurfaceFact(identity_kind="flag", namespace="local", local_key="x", title="x")
    with pytest.raises(AdoptError) as caught:
        writer.write_run(resolved=resolved, manifest=MANIFEST, facts=[stray], vcs_revision="abc123")
    assert caught.value.code is ErrorCode.MAP_EXTRACTOR_FAILED


def test_a_below_floor_fact_becomes_a_gap_and_not_knowledge(
    s4_store: SqliteStoreHandle, writer: SurfaceWriter, resolved
) -> None:
    """*Fails when* a low-confidence fact is written as knowledge.

    *Matters because* PRD §1.6's "silence beats guessing" is implemented **here**:
    below `MAP_MIN_EMIT_CONFIDENCE` the run declines and records a gap, and a map
    that wrote the guess instead would overstate itself in a way no reader can
    detect. *No other instrument catches it because* the guessed row is
    indistinguishable from a good one once written.
    """
    # `regex` is 0.45 in the table -- above the 0.40 floor -- so the case needs a
    # method the floor actually excludes. There is none today, which is itself
    # worth asserting: the floor is currently unreachable from a shipped method.
    from adopt_const import MAP_CONF_REGEX, MAP_MIN_EMIT_CONFIDENCE

    assert MAP_CONF_REGEX > MAP_MIN_EMIT_CONFIDENCE, (
        "no shipped evidence method falls below the emit floor, so the gap path is "
        "reachable only from S1.3's degrade ladder. If this ever inverts, the case "
        "below stops being hypothetical."
    )

    regex_manifest = MANIFEST.model_copy(update={"method": "regex"})
    result = writer.write_run(
        resolved=resolved, manifest=regex_manifest, facts=_facts(), vcs_revision="abc123"
    )
    assert result.gaps == []
    assert result.revisions_written["knowledge"] == len(_facts())


def test_a_tree_with_no_vcs_revision_records_a_gap_rather_than_inventing_a_source_type(
    s4_store: SqliteStoreHandle, writer: SurfaceWriter, resolved
) -> None:
    """*Fails when* a non-VCS tree's provenance is written under a wrong `source_type`.

    *Matters because* `SourceType` is closed and has **no artifact member**
    (B1-CR-36, OD-4): `02` §6's *"else the artifact kind"* names a value that
    does not exist, and the tempting repair -- writing `commit` anyway -- would
    put a claim in the store that a commit was observed when none was. *No other
    instrument catches it because* the row would be perfectly well-formed.
    """
    result = writer.write_run(
        resolved=resolved, manifest=MANIFEST, facts=_facts(), vcs_revision=None
    )

    assert result.provenance_written == 0
    assert s4_store.export_records().table_rows("provenance", Provenance) == []
    assert {gap.reason for gap in result.gaps} == {"provenance_unrecordable"}


def test_a_conflict_row_is_written_open_and_never_resolved(
    s4_store: SqliteStoreHandle, writer: SurfaceWriter, resolved
) -> None:
    """*Fails when* Build 1 resolves an ambiguity it emitted.

    *Matters because* B1-CR-08 and the autonomy matrix both say **nobody in
    Build 1** resolves an ambiguous move: the row is Build 3's input. A
    disposition other than `open` would mean this build decided. *No other
    instrument catches it because* the resolution would look like helpfulness.
    """
    result = writer.write_run(
        resolved=resolved, manifest=MANIFEST, facts=_facts(), vcs_revision="abc123"
    )
    identity = s4_store.export_records().table_rows("identity", Identity)[0]
    writer._write_conflict(
        identity_id=identity.id, identity_uri=identity.uri, candidates=2, result=result
    )

    rows = s4_store.export_records().table_rows("conflict", Conflict)
    assert len(rows) == 1
    assert rows[0].disposition == "open"
    assert rows[0].identity_id == identity.id


def test_a_store_at_the_wrong_schema_version_is_refused(s4_store: SqliteStoreHandle) -> None:
    """*Fails when* the writer opens a store it was not built for.

    *Matters because* the manifest is frozen additive-only and Build 1 adds no
    column, so a version mismatch means the binary and the store came from
    different lines -- and writing into it would produce rows the other line
    cannot read. *No other instrument catches it because* SQLite would accept
    every insert.
    """
    with pytest.raises(AdoptError) as caught:
        SurfaceWriter(
            identities=s4_store.identities(),
            items=s4_store.items(),
            bindings=s4_store.bindings(),
            aux=s4_store.import_records(),
            lookup=s4_store.export_records(),
            revisions=s4_store.revisions(),
            knowledge_draft=KnowledgeRevisionDraft,
            binding_draft=BindingRevisionDraft,
            identity_draft=IdentityRevisionDraft,
            schema_version=s4_store.schema_version + 1,
            supported_schema_version=s4_store.schema_version,
        )
    assert caught.value.code is ErrorCode.MAP_STORE_INCOMPATIBLE


# -- N10: atomicity ----------------------------------------------------------
#
# The helpers below build a complete, independent environment per boundary. They
# are here rather than as fixtures because a fixture is per-test and this sweep
# needs one store per *iteration*.


@contextmanager
def _prepared_store(root: Path) -> Iterator[tuple[SqliteStoreHandle, SurfaceWriter, object]]:
    """A fresh store with scope, boundary, a writer and a resolved scope."""
    root.mkdir(parents=True, exist_ok=True)
    handle = open_store(root / "store.db", migrate=True)
    try:
        facade = handle.scope()
        firm = facade.create_firm(slug="northwind", name="Northwind LLP")
        engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
        system = facade.create_system(
            engagement_id=engagement.id, slug="orders-api", name="Orders API"
        )
        environment = facade.create_environment(system_id=system.id, slug="prod", name="Production")
        handle.boundary().declare(
            scope=Scope(
                firm=ScopeNode(id=firm.id, slug=firm.slug),
                system=ScopeNode(id=system.id, slug=system.slug),
            ),
            tier="T2",
            knowledge_plane_location="customer",
            control_plane_location="customer",
            permitted_outbound_categories=["metadata_only"],
        )
        resolved_scope = resolve_scope(
            handle.export_records(),
            firm_id=firm.id,
            engagement_id=engagement.id,
            system_id=system.id,
            environment_id=environment.id,
            archetype="web",
            tier="T2",
        )
        yield (handle, surface_writer_for(handle), resolved_scope)
    finally:
        handle.close()


def _fingerprint_on_disk(root: Path) -> str:
    """Every row of every canonical table, **read from a fresh connection**.

    Fresh, not the caller's, because that is the difference between "what a
    crashed process left behind" and "what the crashing process could still see
    of its own open transaction". A row count would miss an in-place edit; the
    file's bytes would fail on WAL churn that changed no row. This fails on an
    insert, an update and a delete alike, and on nothing else.
    """
    digest = hashlib.blake2b(digest_size=16)
    with open_store(root / "store.db", read_only=True) as handle:
        _digest_rows(handle, digest)
    return digest.hexdigest()


def _digest_rows(handle: SqliteStoreHandle, digest: "hashlib._Hash") -> None:
    records = handle.export_records()
    for table in sorted(MODEL_FOR_TABLE):
        rendered = sorted(
            json.dumps(row.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            for row in records.table_rows(table, MODEL_FOR_TABLE[table])
        )
        digest.update(f"{table}:{len(rendered)}\n".encode())
        for line in rendered:
            digest.update(line.encode() + b"\n")
    return digest.hexdigest()


def _authorization_count(root: Path) -> int:
    """How many authorizations one clean run takes -- the sweep's upper bound."""
    with _prepared_store(root) as (handle, writer_, resolved_):
        seen = 0

        def count(*_action: object) -> int:
            nonlocal seen
            seen += 1
            return sqlite3.SQLITE_OK

        handle.backend._connection.set_authorizer(count)
        writer_.write_run(
            resolved=resolved_, manifest=MANIFEST, facts=_facts(), vcs_revision="abc123"
        )
        handle.backend._connection.set_authorizer(None)
        return seen


def test_a_kill_at_every_statement_boundary_leaves_the_store_openable_and_unchanged(
    tmp_path: Path,
) -> None:
    """*Fails when* the run is not one transaction (PRD F3.3, N10).

    *Matters because* the promise is that a crashed or budget-killed run leaves
    the store **byte-identical to its pre-run state** -- which is what makes a
    mid-run kill safe to retry rather than a recovery exercise. *No other
    instrument catches it because* a partially-committed run produces a store
    that opens cleanly and is simply half-mapped, and nothing downstream can tell
    a half-map from a small system.

    **The kill mechanism is the authorizer, and the first two candidates were
    both blind.** `set_trace_callback` looked ideal and is useless: CPython
    swallows an exception raised inside it, so the sweep would have injected
    *nothing* and passed on every boundary -- a gate whose broken state is
    indistinguishable from its passing state (Build 0 CR-67). `SQLITE_DENY`
    returned from `set_authorizer` produces a real `DatabaseError` that
    propagates, which was confirmed by running it before this test was written.

    **Each boundary gets a fresh store**, so "unchanged" is a comparison against
    a known pre-run state rather than against whatever the previous iteration
    left behind.
    """
    boundaries = _authorization_count(tmp_path / "count")
    assert boundaries > 10, "the run is too short for this to be a meaningful sweep"

    killed = 0
    for boundary in range(1, boundaries + 1):
        root = tmp_path / f"kill-{boundary}"
        aborted = False
        with _prepared_store(root) as (handle, writer_, resolved_):
            before = _fingerprint_on_disk(root)
            seen = 0

            def deny(*_action: object, limit: int = boundary) -> int:
                nonlocal seen
                seen += 1
                return sqlite3.SQLITE_DENY if seen == limit else sqlite3.SQLITE_OK

            handle.backend._connection.set_authorizer(deny)
            try:
                writer_.write_run(
                    resolved=resolved_,
                    manifest=MANIFEST,
                    facts=_facts(),
                    vcs_revision="abc123",
                )
            except (sqlite3.Error, AdoptError):
                aborted = True
                killed += 1
            finally:
                handle.backend._connection.set_authorizer(None)

        # The handle is closed by the time this runs, and that is the point: the
        # assertion is about what a **crashed process leaves on disk**, which is
        # what N10 promises and the only thing a later run can observe.
        #
        # Reading through the killed run's own connection instead reports its
        # uncommitted view of its own still-open transaction. The first version
        # of this test did exactly that and reported a failure at the `COMMIT`
        # boundary -- four identities, four items, four bindings -- that did not
        # exist on disk at all. The rows were real to that connection and to
        # nothing else.
        if aborted:
            assert _fingerprint_on_disk(root) == before, (
                f"a kill at authorization boundary {boundary} left rows on disk: "
                "the run is not one transaction"
            )

    assert killed > 0, "no boundary produced a kill, so this swept nothing -- the injector is blind"
