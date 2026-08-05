"""Cache and recompute agree while writers run, and the recompute always wins.

*Fails when* a concurrent writer can leave `covered_cache` holding a value the
recompute never produced, or when the reconciliation converges on the cache
instead of on the function. *Matters because* this is CUJ-3's failure branch and
the reason the whole cache-versus-authority distinction exists: in the withdrawn
`0.1.x` line `covered` was truth, and a racing writer's value became permanent
with nothing to contradict it. *No other instrument catches it because* the
equivalence property runs serially and the unit table injects drift by hand --
neither can produce drift the way production does, which is two writers and a
clock.

**Named so `pytest -k coverage_concurrent` selects it**, which is the sprint's
Final Output Validation item 2.

SQLite connects with `check_same_thread=True`, so each thread opens its own
handle on the same file. That is the real deployment shape rather than a test
convenience: WAL gives one writer and many readers, and a test sharing one
connection would be testing a configuration nobody runs.
"""

import datetime as _dt
import io
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from adopt_coverage import rebuild_cache, recompute_coverage
from adopt_obs import LogLevel, ManualClock, new_id, set_sink
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft, open_store

_START = _dt.datetime(2026, 8, 5, 9, 0, 0, tzinfo=_dt.UTC)

#: How many identities the writer brings into coverage while the reconciler
#: runs. Enough for the two threads to interleave; small enough to stay inside
#: the unit budget.
# const-sync: ok -- a thread-interleaving size for this test, not a product value.
_IDENTITY_COUNT = 12

#: How many reconcile passes run against the moving store.
# const-sync: ok -- a loop count for this test, not a product value.
_RECONCILE_PASSES = 25


@pytest.fixture
def store_path(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "concurrent.db"
    handle = open_store(path, migrate=True, clock=ManualClock(_START))
    handle.close()
    yield path


@pytest.fixture
def seeded(store_path: Path) -> tuple[str, list[str], list[str]]:
    """A system, `_IDENTITY_COUNT` uncovered identities, and an item each.

    Returns `(system_id, identity_ids, item_ids)`. Every identity is one binding
    away from covered, so the writer's job is to flip each of them and the
    reconciler's job is to keep up.
    """
    handle = open_store(store_path, clock=ManualClock(_START))
    try:
        facade = handle.scope()
        firm = facade.create_firm(slug="northwind", name="Northwind LLP")
        engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
        system = facade.create_system(
            engagement_id=engagement.id, slug="orders-api", name="Orders API"
        )
        facade.create_environment(system_id=system.id, slug="prod", name="Production")
        scope: Scope = facade.resolve("northwind/acme-erp/orders-api/prod")

        identity_ids: list[str] = []
        item_ids: list[str] = []
        with handle.backend.transaction():
            handle.backend.execute(
                "INSERT INTO observability_boundary "
                "(id, system_id, environment_id, tier, knowledge_plane_location, "
                " control_plane_location, permitted_outbound_categories, declared_at, "
                " contractual) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("ob"),
                    system.id,
                    None,
                    "T2",
                    "customer",
                    "customer",
                    '["metadata_only"]',
                    "2026-08-05T09:00:00.000Z",
                    0,
                ),
            )
        for index in range(_IDENTITY_COUNT):
            identity = handle.identities().observe(
                scope=scope, kind="endpoint", namespace=None, key=f"GET /v1/r{index}"
            )
            item_id, _ = handle.items().create(
                scope=scope,
                kind="answer",
                title=f"about r{index}",
                revision=KnowledgeRevisionDraft(
                    authority_class="human_confirmed", body_md="v1", verification="verified"
                ),
            )
            with handle.backend.transaction():
                handle.backend.execute(
                    "INSERT INTO audience_tag (item_id, audience) VALUES (?, ?)",
                    (item_id, "engineering"),
                )
            identity_ids.append(identity.id)
            item_ids.append(item_id)
        return system.id, identity_ids, item_ids
    finally:
        handle.close()


@pytest.mark.unit
def test_coverage_concurrent_reconciliation_converges_on_the_recompute(
    store_path: Path, seeded: tuple[str, list[str], list[str]]
) -> None:
    system_id, identity_ids, item_ids = seeded
    failures: list[BaseException] = []
    started = threading.Event()

    def write() -> None:
        """Bring each identity into coverage, one binding at a time."""
        handle = open_store(store_path, clock=ManualClock(_START))
        try:
            started.set()
            for identity_id, item_id in zip(identity_ids, item_ids, strict=True):
                handle.bindings().create(
                    item_id=item_id,
                    identity_id=identity_id,
                    is_load_bearing=True,
                    revision=BindingRevisionDraft(status="active", locator_rung=1),
                )
        except BaseException as error:
            failures.append(error)
        finally:
            handle.close()

    sink = io.StringIO()
    set_sink(sink, min_level=LogLevel.DEBUG)
    writer = threading.Thread(target=write, name="coverage-writer")
    reader = open_store(store_path, read_only=True)
    reconciler = open_store(store_path, clock=ManualClock(_START))
    observed_disagreement = False
    try:
        writer.start()
        started.wait(timeout=5)
        for _ in range(_RECONCILE_PASSES):
            result = recompute_coverage(reader.coverage_records(), system_id)
            observed_disagreement = observed_disagreement or bool(result.disagreements)
            rebuild_cache(reconciler.backend, result)
        writer.join(timeout=30)

        # The writers have stopped. One more pass has to reach a fixed point:
        # the cache now holds what the function last produced, and a fresh
        # recompute agrees with it.
        final = recompute_coverage(reader.coverage_records(), system_id)
        rebuild_cache(reconciler.backend, final)
        settled = recompute_coverage(reader.coverage_records(), system_id)
    finally:
        set_sink(io.StringIO())
        reader.close()
        reconciler.close()

    assert not failures, failures
    assert observed_disagreement, (
        "no disagreement was ever observed, so this run proved nothing about "
        "reconciliation under concurrent writes"
    )
    assert settled.disagreements == ()
    assert settled.covered == _IDENTITY_COUNT, "the recompute did not win"
    assert '"level": "alarm"' in sink.getvalue()
