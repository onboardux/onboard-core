"""N4 -- a full export stays inside `EXPORT_P95_SECONDS` at 50,000 items.

Measures what the requirement says: one whole bundle from a store holding
`EXPORT_ITEM_COUNT` knowledge items -- **including** the per-file SHA-256, which
is the part that makes the measurement honest. The digest is computed on every
export by contract (§11) and is the only part of the writer whose cost grows with
the *bytes* rather than the rows, so a benchmark that skipped it would report a
number no client ever experiences.

Reports always. **Asserts only on the reference runner** (`bench/RUNNER.md`
rule 1): a number from a developer's laptop is an anecdote, and an anecdote that
fails a build teaches people to disable the build.

The store is populated once and exported `ITERATIONS` times into fresh
directories, because the export is the operation under test. Rebuilding the store
per sample would measure insert throughput, which belongs to N3 and already has
its own harness.

**The re-export half of N4 is not measured here.** "Byte-identical re-export" is
a correctness property with no number attached, and it is asserted by the
`golden-g0` job and the round-trip property -- both of which fail the build
outright. Timing it would suggest it were a budget.
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

from adopt_const import EXPORT_P95_SECONDS
from adopt_export import write_bundle
from adopt_model import AudienceTag, KnowledgeItem, KnowledgeRevision
from adopt_obs import SystemClock, new_id
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle, writer_identity
from bench import REFERENCE_ENV, is_reference_runner

#: Few enough that the whole harness finishes inside the benchmark job's share of
#: the pipeline at a 30-second budget per sample, many enough for a p95 to mean
#: something. The same reading `coverage_bench` applies to N6.
# const-sync: ok -- a sample count for this harness, not a tunable.
ITERATIONS: Final[int] = 7

#: The population N4 names.
# const-sync: ok -- the N4 measurement size, stated in the NFR rather than tuned.
EXPORT_ITEM_COUNT: Final[int] = 50_000

#: Rows written per transaction. One transaction for the whole population would
#: hold a 50,000-row write open; one per row would measure fsync throughput.
# const-sync: ok -- an insert batch size for this harness, not a tunable.
BATCH_SIZE: Final[int] = 5_000

#: A body long enough that the bundle is bytes rather than a row count -- the
#: digest cost is what the measurement is about, and a store of empty strings
#: would report a number that had nothing to do with a client's.
# const-sync: ok -- a synthetic payload size for this harness, not a tunable.
BODY_CHARS: Final[int] = 400

_P95: Final[float] = 0.95


def _percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, round(_P95 * len(ordered)) - 1)
    return ordered[index]


def _populate(handle: SqliteStoreHandle) -> None:
    facade = handle.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    environment = facade.create_environment(system_id=system.id, slug="prod", name="Production")

    records = handle.import_records()
    now = SystemClock().now()
    body = "x" * BODY_CHARS

    for start in range(0, EXPORT_ITEM_COUNT, BATCH_SIZE):
        items: list[KnowledgeItem] = []
        revisions: list[KnowledgeRevision] = []
        tags: list[AudienceTag] = []
        for index in range(start, min(start + BATCH_SIZE, EXPORT_ITEM_COUNT)):
            item_id = new_id("ki")
            revision_id = new_id("krev")
            items.append(
                KnowledgeItem(
                    id=item_id,
                    firm_id=firm.id,
                    engagement_id=engagement.id,
                    system_id=system.id,
                    environment_id=environment.id,
                    kind="answer",
                    title=f"synthetic item {index}",
                    current_revision_id=revision_id,
                    freshness_state="unverified",
                    created_at=now,
                    updated_at=now,
                )
            )
            revisions.append(
                KnowledgeRevision(
                    id=revision_id,
                    item_id=item_id,
                    body_md=body,
                    authority_class="artifact_observed",
                    created_at=now,
                )
            )
            tags.append(AudienceTag(item_id=item_id, audience="engineering"))
        with handle.backend.transaction():
            records.insert_rows("knowledge_item", items)
            records.insert_rows("knowledge_revision", revisions)
            records.insert_rows("audience_tag", tags)


def _export_samples(handle: SqliteStoreHandle, scratch: Path) -> list[float]:
    samples: list[float] = []
    for index in range(ITERATIONS):
        target = scratch / f"bundle-{index}"
        started = time.perf_counter()
        write_bundle(handle.export_records(), target, written_by=writer_identity())
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
        root = Path(scratch)
        with open_store(root / "bench.db", migrate=True) as handle:
            _populate(handle)
            samples = _export_samples(handle, root)

    measured = _percentile_95(samples)
    print(
        f"export p95: {measured:.2f} s over {ITERATIONS} exports "
        f"at {EXPORT_ITEM_COUNT:,} items (budget {EXPORT_P95_SECONDS} s)"
    )

    if not arguments.do_assert:
        return 0
    if not is_reference_runner():
        print(
            f"not the reference runner ({REFERENCE_ENV} is unset): reported, not asserted. "
            "See bench/RUNNER.md rule 1."
        )
        return 0
    if measured > EXPORT_P95_SECONDS:
        print(f"FAIL: N4 breached -- {measured:.2f} s exceeds {EXPORT_P95_SECONDS} s")
        return 1
    print("PASS: N4 within budget on the reference runner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
