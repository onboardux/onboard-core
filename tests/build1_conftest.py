"""Build 1 fixtures, shared by the unit, property and integration suites.

Imported by `tests/conftest.py` rather than declared as a second `conftest.py`,
so there is one fixture namespace and `pytest --fixtures` shows everything in one
place.

**`store_fingerprint` is the instrument behind "zero writes".** `02` §10 C8 says
every scope abort must write nothing, and the honest way to assert that is to
photograph the store before and after and require the photographs to match. A
row count would miss an in-place edit; the file's bytes would miss nothing but
would also fail on WAL churn that changes no row. So the fingerprint is every
row of every table the manifest declares, ordered, digested -- which fails on an
insert, an update and a delete alike, and on nothing else.
"""

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from adopt_map.context import Budget, ExtractorContext
from adopt_map.fileindex import build_index
from adopt_map.ports import ScopeLookupRecords, SurfaceAuxRecords
from adopt_map.scope_resolve import ResolvedScope, resolve_scope
from adopt_map.writer import SurfaceWriter

from adopt_model import MODEL_FOR_TABLE
from adopt_scope import Scope, ScopeNode
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle
from adopt_store.revisions import (
    BindingRevisionDraft,
    IdentityRevisionDraft,
    KnowledgeRevisionDraft,
)

#: How long a test context's budget runs. Far enough that no extraction suite
#: is accidentally a budget suite; finite so a hung test still ends.
_TEST_BUDGET_S: float = 3_600.0


def surface_writer_for(handle: SqliteStoreHandle) -> SurfaceWriter:
    """Compose a `SurfaceWriter` over a real store.

    One helper rather than the same eight lines in four test modules: S1.2 widened
    the constructor with the read port, the revision appender and the identity
    draft, and four copies of a composition root is four places to forget one.
    The composition itself mirrors `adopt_cli.commands.map_command`, which is the
    only production caller.
    """
    return SurfaceWriter(
        identities=handle.identities(),
        items=handle.items(),
        bindings=handle.bindings(),
        aux=handle.import_records(),
        lookup=handle.export_records(),
        revisions=handle.revisions(),
        knowledge_draft=KnowledgeRevisionDraft,
        binding_draft=BindingRevisionDraft,
        identity_draft=IdentityRevisionDraft,
        schema_version=handle.schema_version,
        supported_schema_version=handle.schema_version,
    )


def build_scoped_store(
    root: Path, *, environments: Sequence[str] = ("prod",)
) -> tuple[SqliteStoreHandle, dict[str, ResolvedScope]]:
    """A fresh store with one system and `environments` under it, all resolved.

    A plain function rather than a fixture because the property suites need one
    store **per Hypothesis example** rather than one per test, and a
    function-scoped fixture reused across examples is a store carrying the
    previous example's rows -- which for an idempotence property would make
    every run after the first look correct for the wrong reason.
    """
    root.mkdir(parents=True, exist_ok=True)
    handle = open_store(root / "store.db", migrate=True)
    facade = handle.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
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

    resolved: dict[str, ResolvedScope] = {}
    for slug in environments:
        environment = facade.create_environment(system_id=system.id, slug=slug, name=slug.title())
        resolved[slug] = resolve_scope(
            handle.export_records(),
            firm_id=firm.id,
            engagement_id=engagement.id,
            system_id=system.id,
            # Named explicitly: with two environments present, omitting it is
            # `MAP_ENVIRONMENT_AMBIGUOUS` by design (`02` §2 rule 3).
            environment_id=environment.id,
            archetype="web",
            tier="T2",
        )
    return handle, resolved


@pytest.fixture
def scope_records(s4_store: SqliteStoreHandle) -> ScopeLookupRecords:
    """The read port, satisfied structurally by Build 0's export records (OD-2)."""
    return s4_store.export_records()


@pytest.fixture
def aux_records(s4_store: SqliteStoreHandle) -> SurfaceAuxRecords:
    """The write port for the four tables with no facade (OD-1)."""
    return s4_store.import_records()


@pytest.fixture
def surface_writer(s4_store: SqliteStoreHandle) -> SurfaceWriter:
    """The writer, composed over the shared store fixture."""
    return surface_writer_for(s4_store)


@pytest.fixture
def resolved_scope(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    scope_records: ScopeLookupRecords,
    add_boundary: Callable[..., str],
) -> ResolvedScope:
    """The production scope, with a boundary declared so resolution does not abort."""
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
def staging_scope(
    s4_store: SqliteStoreHandle,
    s4_scope_staging: Scope,
    scope_records: ScopeLookupRecords,
    add_boundary: Callable[..., str],
) -> ResolvedScope:
    """The **staging** scope of the same system -- PRD F6, CUJ-6.

    A second environment on one system is what makes environment isolation
    testable rather than asserted: without it the isolation gate would pass
    vacuously, which is why `05`'s prerequisite 9 asked for the fixture.
    """
    assert s4_scope_staging.engagement and s4_scope_staging.system
    assert s4_scope_staging.environment
    add_boundary(system_id=s4_scope_staging.system.id)
    return resolve_scope(
        scope_records,
        firm_id=s4_scope_staging.firm.id,
        engagement_id=s4_scope_staging.engagement.id,
        system_id=s4_scope_staging.system.id,
        environment_id=s4_scope_staging.environment.id,
        archetype="web",
        tier="T2",
    )


@pytest.fixture
def store_fingerprint(s4_store: SqliteStoreHandle) -> Callable[[], str]:
    """A digest over every row of every canonical table.

    Sensitive to an insert, an update and a delete; insensitive to WAL churn,
    page reordering and connection state -- which is the difference between an
    instrument and a file hash.
    """

    def _fingerprint() -> str:
        digest = hashlib.blake2b(digest_size=16)
        records = s4_store.export_records()
        for table in sorted(MODEL_FOR_TABLE):
            rows = records.table_rows(table, MODEL_FOR_TABLE[table])
            rendered = sorted(
                json.dumps(row.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
                for row in rows
            )
            digest.update(f"{table}:{len(rendered)}\n".encode())
            for line in rendered:
                digest.update(line.encode() + b"\n")
        return digest.hexdigest()

    return _fingerprint


def context_for(
    root: str | Path, *, archetype: str = "web", tier: str | None = "T2"
) -> ExtractorContext:
    """An `ExtractorContext` over a real tree -- S1.3, B1-CR-58.

    `02` §7 obligation 7 is *"calls `ctx.budget.check()` at least once per file"*,
    which needs a `ctx` the S1.1 protocol had no parameter for. Every caller that
    used to hand `extract()` a path string comes through here instead, so the
    index is built the same way the orchestrator builds it -- one walk, real blob
    shas, real language detection -- rather than each test inventing a context
    that agrees with nothing.

    The budget is a **far** deadline by default: a test exercising extraction is
    not testing the budget, and a context whose budget could expire mid-test
    would make every extraction suite intermittently a budget suite.
    """
    index = build_index(Path(root))
    return ExtractorContext(
        root=str(root),
        index=index,
        budget=Budget.starting_at(time.time(), stage1_s=_TEST_BUDGET_S, total_s=_TEST_BUDGET_S),
        archetype=archetype,
        tier=tier,
    )


def exhausted_context(root: str | Path) -> ExtractorContext:
    """A context whose budget has **already** elapsed.

    The instrument behind the budget-exhaustion drill: `Budget.now` is injected,
    so exhaustion is produced by arithmetic rather than by sleeping -- `03` §5
    bans sleeps in tests, and a drill that slept for the real budget would take
    an hour.
    """
    index = build_index(Path(root))
    start = time.time() - 10_000
    return ExtractorContext(
        root=str(root),
        index=index,
        budget=Budget.starting_at(start, stage1_s=1.0, total_s=1.0),
        archetype="web",
        tier="T2",
    )
