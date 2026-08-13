"""N7 -- `resolve_freshness` stays inside `FRESHNESS_RESOLVE_P95_MS` per item.

Measures what the requirement says: **per item**, not per batch. CUJ-4 resolves
200 items in one operator action and CUJ-5 resolves whatever a degraded sensor
touches, so the number that matters is the cost of one resolution against a
store with realistic fan-out -- one shared referent bound by many items, which is
the shape that makes the load-bearing rule necessary in the first place.

Reports always. **Asserts only on the reference runner** (`bench/RUNNER.md`
rule 1).

The p95 is taken over the resolutions themselves rather than over repeated runs
of a fixed item: every item has a different binding fan-out, and averaging one
item's cost would report the easy case.
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

from adopt_const import FRESHNESS_RESOLVE_P95_MS
from adopt_freshness import resolve_freshness
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft, open_store
from adopt_store.api import SqliteStoreHandle
from bench import REFERENCE_ENV, is_reference_runner

#: How many items bind the shared referent. CUJ-4's own number, so the benchmark
#: measures the journey the PRD describes rather than a shape invented here.
# const-sync: ok -- CUJ-4's fan-out, used as the measurement shape.
SHARED_BINDING_COUNT: Final[int] = 200

#: How many additional identities each item binds, so resolution is not a
#: single-binding walk. A real item cites the endpoint it describes, the config
#: that gates it and the job that feeds it.
# const-sync: ok -- a population shape for this harness, not a tunable.
EXTRA_BINDINGS_PER_ITEM: Final[int] = 2

#: Sensors in scope, all healthy, so the override path is walked rather than
#: skipped. A benchmark over a store with no sensors would report the cost of
#: the branch that is never taken in production.
# const-sync: ok -- a population shape for this harness, not a tunable.
SENSOR_COUNT: Final[int] = 4

# const-sync: ok -- a fixture cadence, not a product value.
_CADENCE_SECONDS: Final[int] = 900
_MILLISECONDS_PER_SECOND: Final[float] = 1000.0
# const-sync: ok -- the 95th percentile, a statistic, not MAP_CONF_GRAMMAR.
_P95: Final[float] = 0.95


def _percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, round(_P95 * len(ordered)) - 1)
    return ordered[index]


def _populate(handle: SqliteStoreHandle) -> list[str]:
    """One shared referent bound by `SHARED_BINDING_COUNT` items."""
    facade = handle.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    scope = facade.resolve("northwind/acme-erp/orders-api/prod")

    items: list[str] = []
    with handle.backend.transaction():
        shared = handle.identities().observe(
            scope=scope, kind="symbol", namespace="billing", key=("charges", "refund")
        )
        for index in range(SENSOR_COUNT):
            sensor = handle.sensors().register(
                scope=scope, kind="webhook", expected_cadence_seconds=_CADENCE_SECONDS
            )
            handle.sensors().heartbeat(sensor_id=sensor.id, outcome="success", detail=str(index))

        for index in range(SHARED_BINDING_COUNT):
            item_id, _ = handle.items().create(
                scope=scope,
                kind="answer",
                title=f"about refund path {index:04d}",
                revision=KnowledgeRevisionDraft(authority_class="artifact_observed", body_md="v1"),
            )
            handle.bindings().create(
                item_id=item_id,
                identity_id=shared.id,
                # Half load-bearing, so the rule is exercised in both directions.
                is_load_bearing=bool(index % 2),
                # const-sync: ok -- a locator rung, not a tunable that shares its value.
                revision=BindingRevisionDraft(status="active", locator_rung=3),
            )
            for extra in range(EXTRA_BINDINGS_PER_ITEM):
                other = handle.identities().observe(
                    scope=scope,
                    kind="config_key",
                    namespace="orders",
                    key=f"flag-{index:04d}-{extra}",
                )
                handle.bindings().create(
                    item_id=item_id,
                    identity_id=other.id,
                    is_load_bearing=False,
                    revision=BindingRevisionDraft(status="active", locator_rung=1),
                )
            items.append(item_id)
    return items


def _resolve_samples(path: Path, items: list[str]) -> list[float]:
    samples: list[float] = []
    with open_store(path, read_only=True) as handle:
        records = handle.freshness_records()
        for item_id in items:
            started = time.perf_counter()
            resolve_freshness(records, item_id)
            samples.append((time.perf_counter() - started) * _MILLISECONDS_PER_SECOND)
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--assert",
        dest="do_assert",
        action="store_true",
        help="Fail when the budget is breached on the reference runner.",
    )
    arguments = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "freshness-bench.db"
        with open_store(path, migrate=True) as handle:
            items = _populate(handle)
        samples = _resolve_samples(path, items)

    measured = _percentile_95(samples)
    print(
        f"freshness resolve p95: {measured:.2f} ms over {len(samples)} items, each binding "
        f"{EXTRA_BINDINGS_PER_ITEM + 1} identities with {SENSOR_COUNT} sensors in scope "
        f"(budget {FRESHNESS_RESOLVE_P95_MS} ms)"
    )

    if not arguments.do_assert:
        return 0
    if not is_reference_runner():
        print(
            f"not the reference runner ({REFERENCE_ENV} is unset): reported, not asserted. "
            "See bench/RUNNER.md rule 1."
        )
        return 0
    if measured > FRESHNESS_RESOLVE_P95_MS:
        print(f"FAIL: N7 breached -- {measured:.2f} ms exceeds {FRESHNESS_RESOLVE_P95_MS} ms")
        return 1
    print("PASS: N7 within budget on the reference runner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
