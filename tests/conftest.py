"""Shared test configuration.

Puts the repository root on `sys.path` so the CI gate scripts and the custom
import-linter contracts can be imported and driven directly. A gate that can
only be exercised through a subprocess is a gate nobody writes a negative test
for -- and these four gates are only worth having if they are known to reject.

**The fixtures below are S4's and are shared rather than copied.** Coverage and
freshness both need the same world -- a four-level scope, an identity, an item,
a binding, a boundary -- across six files, and six copies of a forty-line
builder is six places for the fixture to drift from the contract it encodes.
Every earlier file keeps its own local `store`/`scope`, which pytest lets
shadow these, so nothing already written changes behaviour.

**`audience_tag` is written here through the backend rather than a facade.** It
belongs to whichever sprint gives the item writer its full surface -- but
`recompute_coverage` reads it, so a coverage test has to be able to put a row
there. A facade written to satisfy a test is a facade the sprint that owns it
then has to argue with.

**`add_boundary` went through raw SQL until S6 and now goes through
`Store.boundary()`**, which is the facade that sprint was waiting for. Six S4
tests get the real write path for free, and the raw `INSERT` that stood in for it
is gone rather than left beside its replacement.
"""

import datetime as _dt
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adopt_obs import ManualClock  # noqa: E402
from adopt_scope import Scope, ScopeNode  # noqa: E402
from adopt_store import open_store  # noqa: E402
from adopt_store.api import SqliteStoreHandle  # noqa: E402
from adopt_store.sqlite.records import SqliteKnowledgeRecords  # noqa: E402

#: A fixed instant. Every S4 test drives time with `ManualClock.advance`;
#: `sleep` is banned (implementation spec §5).
S4_START = _dt.datetime(2026, 8, 5, 9, 0, 0, tzinfo=_dt.UTC)


@pytest.fixture
def s4_clock() -> ManualClock:
    return ManualClock(S4_START)


@pytest.fixture
def s4_store(tmp_path: Path, s4_clock: ManualClock) -> Iterator[SqliteStoreHandle]:
    handle = open_store(tmp_path / "store.db", migrate=True, clock=s4_clock)
    yield handle
    handle.close()


@pytest.fixture
def s4_scope(s4_store: SqliteStoreHandle) -> Scope:
    """`northwind/acme-erp/orders-api/prod` -- the `02` §4 worked example."""
    facade = s4_store.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    return facade.resolve("northwind/acme-erp/orders-api/prod")


@pytest.fixture
def s4_scope_staging(s4_store: SqliteStoreHandle, s4_scope: Scope) -> Scope:
    """A **second** environment on the same system: `.../orders-api/staging`.

    Deliberately a separate fixture rather than a second row inside `s4_scope`,
    so that every existing single-environment test keeps the data it was written
    against and this one is opt-in.

    **Why it exists.** Build 1's environment-isolation gate (contracts C9, PRD
    N6) asserts that a staging run emits zero production URIs. With one
    environment in the store that assertion passes without testing anything --
    the same shape as an escape case run against a database with no second
    tenant. The second row is what gives the gate something to be wrong about,
    which is why Build 1's prerequisite check names it explicitly.
    """
    assert s4_scope.system is not None, "s4_scope resolves all four levels"
    facade = s4_store.scope()
    facade.create_environment(system_id=s4_scope.system.id, slug="staging", name="Staging")
    return facade.resolve("northwind/acme-erp/orders-api/staging")


@pytest.fixture
def add_boundary(s4_store: SqliteStoreHandle) -> Callable[..., str]:
    """Declare one `observability_boundary` through the facade. Input 5 of contracts §6.

    `environment_id=None` is the system-wide declaration and governs every
    environment; naming one governs only that environment. The distinction is
    what `recompute_coverage` turns on, so the helper exposes it rather than
    picking one.

    The `Scope` is built from ids alone because that is all `declare` reads --
    the slug is set to the id rather than to an empty string so that anything
    which did misuse one fails visibly instead of joining to a row with an empty
    slug, the same reasoning `adopt_cli.commands.identity` records.
    """

    def _add(*, system_id: str, environment_id: str | None = None, tier: str = "T2") -> str:
        scope = Scope(
            firm=ScopeNode(id=system_id, slug=system_id),
            system=ScopeNode(id=system_id, slug=system_id),
            environment=(
                None
                if environment_id is None
                else ScopeNode(id=environment_id, slug=environment_id)
            ),
        )
        row = s4_store.boundary().declare(
            scope=scope,
            tier=tier,  # type: ignore[arg-type]
            knowledge_plane_location="customer",
            control_plane_location="customer",
            permitted_outbound_categories=["metadata_only"],
        )
        return row.id

    return _add


@pytest.fixture
def add_audience(s4_store: SqliteStoreHandle) -> Callable[..., None]:
    """Write one `audience_tag`. Half of input 4 of contracts §6."""

    def _add(*, item_id: str, audience: str = "engineering") -> None:
        with s4_store.backend.transaction():
            s4_store.backend.execute(
                "INSERT INTO audience_tag (item_id, audience) VALUES (?, ?)", (item_id, audience)
            )

    return _add


@pytest.fixture
def inject_cache(s4_store: SqliteStoreHandle) -> Callable[..., None]:
    """Force a `covered_cache` value, to create the drift CUJ-3 exists to catch.

    **The one legitimate cache write outside `adopt_coverage`**, and legitimate
    only because `tests/` is outside every path `no-covered-cache-write` scans
    (`packages`, `scripts`, `tools`, `bench`, `schema`). A test that could not
    create the defect could not assert the detector, and an alarm nobody has
    seen fire is an alarm nobody should trust.
    """

    def _inject(*, identity_id: str, covered: bool) -> None:
        with s4_store.backend.transaction():
            s4_store.backend.execute(
                "UPDATE identity SET covered_cache = ? WHERE id = ?",
                (int(covered), identity_id),
            )

    return _inject


@pytest.fixture
def set_item_freshness(s4_store: SqliteStoreHandle, s4_clock: ManualClock) -> Callable[..., None]:
    """Put an item into a given `freshness_state`.

    Build 0 has no production writer for `fresh`: classification and repair are
    items 9 and 10, and `resolve_freshness` only ever *reads* the column. The
    fixture goes through the real records port rather than raw SQL, so a test
    setting up a state cannot set up one the store would refuse.
    """
    records = SqliteKnowledgeRecords(s4_store.backend)

    def _set(*, item_id: str, state: str) -> None:
        with s4_store.backend.transaction():
            records.set_item_freshness(item_id, state, s4_clock.now())  # type: ignore[arg-type]

    return _set


@pytest.fixture
def set_binding_freshness(s4_store: SqliteStoreHandle) -> Callable[..., None]:
    """Put a binding into a given `freshness_state`.

    Per-binding freshness is a first-class column (PRD F8.2) that Build 0 reads
    and does not write -- the writer is item 10's classifier. Injected here so
    the binding-level rule has something to decide on.
    """

    def _set(*, binding_id: str, state: str) -> None:
        with s4_store.backend.transaction():
            s4_store.backend.execute(
                "UPDATE binding SET freshness_state = ? WHERE id = ?", (state, binding_id)
            )

    return _set


# --------------------------------------------------------------------------
# Build 1 (`adopt map`) fixtures -- S1.1.
#
# Imported rather than declared in a second `conftest.py` so there is one
# fixture namespace: a Build 1 test and a Build 0 test that both need a store
# must get the *same* store fixture, or "zero writes" is being asserted against
# a different database from the one under test.
# --------------------------------------------------------------------------
from tests.build1_conftest import (  # noqa: E402
    aux_records,
    scope_records,
    store_fingerprint,
)

__all__ = ["aux_records", "scope_records", "store_fingerprint"]
