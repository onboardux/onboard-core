"""Seed a scratch store carrying S4's two observable defects.

Not a test and not part of the suite: this is the setup for the sprint's Final
Output Validation items 5 and 6, which name **commands** rather than test files.
It builds a store and stops, so the two commands are then run from a shell
exactly as an operator runs them, rather than from inside a Python process that
could have arranged the answer.

**Two referents, because the two defects would otherwise mask each other.**
`orders` carries the freshness demonstration: it stays covered and `fresh`, and a
degraded sensor in its environment is what moves it. `refunds` carries the
coverage demonstration: its cache is built honestly and its binding is then
retired, so the recompute disagrees. Putting both on one referent makes the
retired binding stale the item, and the sensor override -- which only ever
blocks `fresh` -- then has nothing left to act on.

    uv run python scripts/s4_validation_walkthrough.py /tmp/walkthrough.db
    uv run adopt coverage recompute --system <system_id> --store <path> --json
    uv run adopt freshness resolve --item <item_id> --store <path> --json
"""

import sys
from pathlib import Path

from adopt_coverage import rebuild_cache, recompute_coverage
from adopt_obs import SystemClock, format_timestamp, new_id
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft, doctor, open_store
from adopt_store.api import SqliteStoreHandle


def _bound_pair(handle: SqliteStoreHandle, scope: Scope, key: str) -> tuple[str, str, str]:
    """One identity, one covered item, one live binding. `(identity, item, binding)`."""
    identity = handle.identities().observe(scope=scope, kind="endpoint", namespace=None, key=key)
    item_id, _ = handle.items().create(
        scope=scope,
        kind="answer",
        title=f"How {key} behaves",
        revision=KnowledgeRevisionDraft(
            authority_class="human_confirmed", body_md="v1", verification="verified"
        ),
    )
    binding_id, _ = handle.bindings().create(
        item_id=item_id,
        identity_id=identity.id,
        is_load_bearing=True,
        revision=BindingRevisionDraft(status="active", locator_rung=1),
    )
    with handle.backend.transaction():
        handle.backend.execute(
            "INSERT INTO audience_tag (item_id, audience) VALUES (?, ?)",
            (item_id, "engineering"),
        )
    return identity.id, item_id, binding_id


def main(argv: list[str] | None = None) -> int:
    arguments = argv if argv is not None else sys.argv[1:]
    if len(arguments) != 1:
        print("usage: s4_validation_walkthrough.py <store-path>")
        return 2
    path = Path(arguments[0])

    with open_store(path, migrate=True) as handle:
        facade = handle.scope()
        firm = facade.create_firm(slug="northwind", name="Northwind LLP")
        engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
        system = facade.create_system(
            engagement_id=engagement.id, slug="orders-api", name="Orders API"
        )
        facade.create_environment(system_id=system.id, slug="prod", name="Production")
        scope = facade.resolve("northwind/acme-erp/orders-api/prod")

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
                    format_timestamp(SystemClock().now()),
                    0,
                ),
            )

        _, watched_item, _ = _bound_pair(handle, scope, "POST /v1/orders")
        _, _, drifting_binding = _bound_pair(handle, scope, "POST /v1/refunds")

        # The watched item is `fresh`, so the sensor override has something to
        # block. Build 0 has no production writer for `fresh` -- classification
        # is item 10 -- so the state is set directly here.
        with handle.backend.transaction():
            handle.backend.execute(
                "UPDATE knowledge_item SET freshness_state = ? WHERE id = ?",
                ("fresh", watched_item),
            )

        # Build the cache honestly, from a recompute: the only permitted
        # direction and the only permitted writer.
        rebuild_cache(handle.backend, recompute_coverage(handle.coverage_records(), system.id))

        # Now produce the drift **the way production produces it**: the world
        # moves and nobody recomputes. Hand-writing the cache here would be
        # simpler and would also be a `no-covered-cache-write` violation --
        # correctly, because a script that can forge the cache is a second
        # writer, which is the entire defect this alarm exists for.
        handle.bindings().retire(binding_id=drifting_binding, reason="endpoint withdrawn")

        sensor = handle.sensors().register(
            scope=scope,
            kind="webhook",
            # const-sync: ok -- a fixture sensor's cadence, not MAP_STAGE1_BUDGET_S.
            expected_cadence_seconds=900,
        )
        handle.sensors().heartbeat(sensor_id=sensor.id, outcome="success")
        handle.sensors().degrade(
            sensor_id=sensor.id, health="DEGRADED", reason="webhook returning 5xx"
        )

        print("== store doctor ==")
        for finding in doctor(handle):
            print(" ", finding.render())

        print(f"\nstore   {path}")
        print(f"system  {system.id}")
        print(f"item    {watched_item}   (fresh, watched by a degraded sensor)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
