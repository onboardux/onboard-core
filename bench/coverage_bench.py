"""N6 -- `recompute_coverage` stays inside `COVERAGE_RECOMPUTE_P95_SECONDS`.

Measures what the requirement says: one full recompute over a system holding
`COVERAGE_IDENTITY_COUNT` identities, **including** the comparison against
`covered_cache` -- which is the part that makes the measurement honest, because
the disagreement scan runs on every call and a benchmark that skipped it would
report a number no operator ever experiences.

Reports always. **Asserts only on the reference runner** (`bench/RUNNER.md`
rule 1): a number from a developer's laptop is an anecdote, and an anecdote that
fails a build teaches people to disable the build.

The store is populated once and recomputed `ITERATIONS` times, because the
recompute is the operation under test. Building a store per sample would measure
insert throughput, which belongs to N3 and already has its own harness.
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

from adopt_const import COVERAGE_RECOMPUTE_P95_SECONDS
from adopt_coverage import rebuild_cache, recompute_coverage
from adopt_obs import SystemClock, format_timestamp, new_id
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft, open_store
from adopt_store.api import SqliteStoreHandle
from bench import REFERENCE_ENV, is_reference_runner

#: Few enough that the whole harness finishes inside the benchmark job's share
#: of the pipeline at a 20-second budget per sample, many enough for a p95 to
#: mean something. N3 can afford forty samples because an open costs
#: milliseconds; a full recompute over 50k identities cannot.
# const-sync: ok -- a sample count for this harness, not a tunable.
ITERATIONS: Final[int] = 7

#: The population N6 names.
# const-sync: ok -- the N6 measurement size, stated in the NFR rather than tuned.
COVERAGE_IDENTITY_COUNT: Final[int] = 50_000

#: How many identities carry a binding to a knowledge item. Every identity is
#: read and evaluated regardless; this fraction decides how many also traverse
#: the binding, item, audience and verification lookups. One in ten is a
#: deliberately conservative reading of a real engagement -- an adoption-phase
#: store has far more surface than documented surface, which is the gap the
#: product exists to close.
# const-sync: ok -- a population shape for this harness, not a tunable.
BOUND_FRACTION: Final[int] = 10

_P95: Final[float] = 0.95


def _percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, round(_P95 * len(ordered)) - 1)
    return ordered[index]


def _populate(handle: SqliteStoreHandle) -> str:
    """One system with `COVERAGE_IDENTITY_COUNT` identities. Returns its id."""
    facade = handle.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    scope = facade.resolve("northwind/acme-erp/orders-api/prod")

    # One transaction for the whole population: per-row commits would measure
    # fsync throughput, and the store under test is one a client already has.
    with handle.backend.transaction():
        handle.backend.execute(
            "INSERT INTO observability_boundary "
            "(id, system_id, environment_id, tier, knowledge_plane_location, "
            " control_plane_location, permitted_outbound_categories, declared_at, contractual) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id("ob"),
                system.id,
                None,
                "T2",
                "customer",
                "customer",
                '["metadata_only"]',
                format_timestamp(SystemClock().now()),
                0,
            ),
        )
        for index in range(COVERAGE_IDENTITY_COUNT):
            identity = handle.identities().observe(
                scope=scope, kind="endpoint", namespace=None, key=f"GET /v1/r{index:06d}"
            )
            if index % BOUND_FRACTION:
                continue
            item_id, _ = handle.items().create(
                scope=scope,
                kind="answer",
                title=f"about r{index:06d}",
                revision=KnowledgeRevisionDraft(
                    authority_class="artifact_observed", body_md="v1", verification="verified"
                ),
            )
            handle.backend.execute(
                "INSERT INTO audience_tag (item_id, audience) VALUES (?, ?)",
                (item_id, "engineering"),
            )
            handle.bindings().create(
                item_id=item_id,
                identity_id=identity.id,
                is_load_bearing=True,
                revision=BindingRevisionDraft(status="active", locator_rung=1),
            )
    return system.id


def _recompute_samples(path: Path, system_id: str) -> list[float]:
    samples: list[float] = []
    with open_store(path, read_only=True) as handle:
        records = handle.coverage_records()
        for _ in range(ITERATIONS):
            started = time.perf_counter()
            recompute_coverage(records, system_id)
            samples.append(time.perf_counter() - started)
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
        path = Path(scratch) / "coverage-bench.db"
        with open_store(path, migrate=True) as handle:
            system_id = _populate(handle)
            # Seed the cache from one recompute before measuring. A freshly
            # populated store has `covered_cache` false on every row, so the
            # first call disagrees 50,000 times -- which measures the alarm path
            # rather than the recompute, and is not the state any operator's
            # store is in.
            rebuild_cache(handle.backend, recompute_coverage(handle.coverage_records(), system_id))
        samples = _recompute_samples(path, system_id)

    measured = _percentile_95(samples)
    print(
        f"coverage recompute p95: {measured:.2f} s over {ITERATIONS} runs at "
        f"{COVERAGE_IDENTITY_COUNT:,} identities "
        f"(budget {COVERAGE_RECOMPUTE_P95_SECONDS} s)"
    )

    if not arguments.do_assert:
        return 0
    if not is_reference_runner():
        print(
            f"not the reference runner ({REFERENCE_ENV} is unset): reported, not asserted. "
            "See bench/RUNNER.md rule 1."
        )
        return 0
    if measured > COVERAGE_RECOMPUTE_P95_SECONDS:
        print(f"FAIL: N6 breached -- {measured:.2f} s exceeds {COVERAGE_RECOMPUTE_P95_SECONDS} s")
        return 1
    print("PASS: N6 within budget on the reference runner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
