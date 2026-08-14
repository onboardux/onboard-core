"""Moves and aliases -- PRD F5, CUJ-3; contracts §4.3, §10 C11; `03` §5.6.

The five cases `03` §5.6 names, plus the three the rule needs in order to be
safe rather than merely correct. **Move-ambiguity declination is one of the five
instruments that survive any budget cut** (`05` Quality notes), because the
failure it prevents is not a wrong number: it is an alias chain asserting that
one referent became another when nobody knows that.

| Case | Expected | Defect it catches |
|---|---|---|
| Clean rename | 1 move, alias set | A rename read as a deletion plus an unrelated add |
| Two identical copies | 0 moves, 1 conflict | A coin-flip alias between two candidates |
| Rename **and** edit | 0 moves, 1 conflict | A move claimed on a referent that also changed |
| Cross-environment disappearance | 0 moves, prod untouched | Staging rewriting production's history |
| Opaque rename | 0 moves, 1 conflict | Two `ZFIELD_003`s fused on a null digest |
| A declined move, run twice | 1 conflict total | A second run that is not a no-op (B1-CR-46) |
| Any move at all | never `status='dead'` | A false death, which is a false retirement downstream |
| A moved identity's binding | `moved`, still present | A binding removed instead of superseded |
"""

from collections.abc import Callable

import pytest
from adopt_map.schemas import ExtractorManifest, SurfaceFact
from adopt_map.scope_resolve import ResolvedScope
from adopt_map.writer import SurfaceWriter, SurfaceWriteResult

from adopt_model import Binding, BindingRevision, Conflict, Identity, IdentityRevision
from adopt_store.api import SqliteStoreHandle

pytestmark = pytest.mark.integration

_MANIFEST = ExtractorManifest(
    id="common.fixture", version="1.0.0", pack="common", kinds=["symbol"], method="grammar"
)

_ORIGINAL = "orders.api.OrderDetailView.get"
_RENAMED = "orders.views.OrderDetailView.get"


def _symbol(key: str, *, signature: str = "get(self, request, id)", opaque: bool = False):
    return SurfaceFact(
        identity_kind="symbol",
        namespace="python",
        local_key=key,
        title=key,
        attributes={"signature": signature, "return_type": "HttpResponse"},
        opaque=opaque,
    )


@pytest.fixture
def run(
    surface_writer: SurfaceWriter, resolved_scope: ResolvedScope
) -> Callable[..., SurfaceWriteResult]:
    def _run(*facts: SurfaceFact, scope: ResolvedScope | None = None) -> SurfaceWriteResult:
        return surface_writer.write_run(
            resolved=scope if scope is not None else resolved_scope,
            manifest=_MANIFEST,
            facts=list(facts),
            vcs_revision=None,
        )

    return _run


def _rows[TModel](handle: SqliteStoreHandle, table: str, model: type[TModel]) -> list[TModel]:
    return list(handle.export_records().table_rows(table, model))


def test_move_clean_rename_becomes_an_alias_chain(
    run: Callable[..., SurfaceWriteResult], s4_store: SqliteStoreHandle
) -> None:
    """CUJ-3: one `moved` revision with `alias_of_identity_id`, and no orphan."""
    run(_symbol(_ORIGINAL))
    result = run(_symbol(_RENAMED))

    assert len(result.moves) == 1
    assert result.moves[0].from_uri.endswith("orders.api.OrderDetailView.get")
    assert result.moves[0].to_uri.endswith("orders.views.OrderDetailView.get")
    assert result.conflicts == []

    identities = {row.uri: row for row in _rows(s4_store, "identity", Identity)}
    assert len(identities) == 2, "the old identity keeps its row and its URI"

    moved = [
        row
        for row in _rows(s4_store, "identity_revision", IdentityRevision)
        if row.status == "moved"
    ]
    assert len(moved) == 1
    destination = next(
        row for uri, row in identities.items() if uri.endswith("views.OrderDetailView.get")
    )
    assert moved[0].alias_of_identity_id == destination.id


def test_move_binding_is_superseded_and_never_removed(
    run: Callable[..., SurfaceWriteResult], s4_store: SqliteStoreHandle
) -> None:
    """Build 0 CR-07: bindings are retired, never removed -- and Build 1 does neither."""
    run(_symbol(_ORIGINAL))
    run(_symbol(_RENAMED))

    assert len(_rows(s4_store, "binding", Binding)) == 2, "both bindings still exist"
    statuses = [row.status for row in _rows(s4_store, "binding_revision", BindingRevision)]
    assert statuses.count("moved") == 1
    assert "retired" not in statuses, "retirement is Build 3's"


def test_move_two_identical_copies_declines_and_records_a_conflict(
    run: Callable[..., SurfaceWriteResult], s4_store: SqliteStoreHandle
) -> None:
    """Two candidates is not a better guess than none -- B1-CR-08."""
    run(_symbol(_ORIGINAL))
    result = run(_symbol("orders.views.copy_one"), _symbol("orders.views.copy_two"))

    assert result.moves == []
    assert len(result.conflicts) == 1
    assert result.conflicts[0].candidates == 2

    rows = _rows(s4_store, "conflict", Conflict)
    assert len(rows) == 1
    assert rows[0].disposition == "open", "Build 3 resolves it; Build 1 records it"


def test_move_rename_plus_edit_is_not_a_move(
    run: Callable[..., SurfaceWriteResult],
) -> None:
    """`sem` differs, so there is no evidence the two are one referent."""
    run(_symbol(_ORIGINAL))
    result = run(_symbol(_RENAMED, signature="get(self, request, id, *, expand)"))

    assert result.moves == []
    assert len(result.conflicts) == 1
    assert result.conflicts[0].candidates == 0


def test_move_of_an_opaque_identity_is_declined(
    run: Callable[..., SurfaceWriteResult],
) -> None:
    """Null never matches null -- `02` §4.3 row 6.

    Fails when an opaque referent is allowed to match another; matters because
    two `ZFIELD_003`-shaped identities carry no evidence at all that one became
    the other, and an alias chain between them is a fabrication a human would
    later have to disprove; no other instrument catches it because the digests
    compare equal under any implementation that treats null as a value.
    """
    run(_symbol(_ORIGINAL, opaque=True))
    result = run(_symbol(_RENAMED, opaque=True))

    assert result.moves == []
    assert len(result.conflicts) == 1


def test_move_across_environments_is_not_a_move(
    run: Callable[..., SurfaceWriteResult],
    staging_scope: ResolvedScope,
    s4_store: SqliteStoreHandle,
) -> None:
    """PRD F5.4 -- a referent in staging and not in production has not moved.

    Fails when move matching is not scoped to one `(system, environment)`;
    matters because the two identity sets are required to be disjoint (F6.4) and
    a cross-environment alias would merge exactly the two registries environment
    separation exists to keep apart; no other instrument catches it because both
    runs individually look correct.
    """
    run(_symbol(_ORIGINAL))
    before = len(_rows(s4_store, "identity_revision", IdentityRevision))

    result = run(_symbol(_RENAMED), scope=staging_scope)

    assert result.moves == []
    assert result.conflicts == []
    production = [
        row
        for row in _rows(s4_store, "identity", Identity)
        if row.environment_id != staging_scope.environment_id
    ]
    assert len(production) == 1, "production gained nothing"
    moved = [
        row
        for row in _rows(s4_store, "identity_revision", IdentityRevision)
        if row.status == "moved"
    ]
    assert moved == [], "nothing in production was superseded by a staging run"
    assert len(_rows(s4_store, "identity_revision", IdentityRevision)) == before + 1


def test_move_declination_is_not_repeated_on_a_later_run(
    run: Callable[..., SurfaceWriteResult], s4_store: SqliteStoreHandle
) -> None:
    """B1-CR-46 -- the conflict is written once, not once per run.

    Fails when the rule re-examines an identity it already declined; matters
    because an identity that disappeared stays disappeared, so a rule with no
    memory writes a fresh `conflict` row on every run for the rest of the
    store's life and CUJ-2's *"no other row changes"* fails from the third run
    onward; no other instrument catches it because two runs is exactly the
    number at which the bug is invisible.
    """
    run(_symbol(_ORIGINAL))
    run(_symbol(_RENAMED, signature="changed()"))
    assert len(_rows(s4_store, "conflict", Conflict)) == 1

    third = run(_symbol(_RENAMED, signature="changed()"))

    assert third.conflicts == []
    assert third.revisions_written == {"identity": 0, "knowledge": 0, "binding": 0}
    assert len(_rows(s4_store, "conflict", Conflict)) == 1


def test_move_never_writes_status_dead(
    run: Callable[..., SurfaceWriteResult], s4_store: SqliteStoreHandle
) -> None:
    """PRD F5.3, B1-CR-07 -- swept over every revision the move cases produce."""
    run(_symbol(_ORIGINAL))
    run(_symbol(_RENAMED))
    run(_symbol("orders.views.a"), _symbol("orders.views.b"))

    statuses = {row.status for row in _rows(s4_store, "identity_revision", IdentityRevision)}
    assert "dead" not in statuses
