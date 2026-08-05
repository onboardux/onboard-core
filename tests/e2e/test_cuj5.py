"""CUJ-5 -- a sensor degrades; freshness is overridden.

*Fails when* knowledge stays `fresh` while the channel that would have noticed a
change is not reporting, or when the resolver reports the override without
naming what caused it. *Matters because* this is the difference between "we know
this is current" and "we cannot currently tell", and a system that cannot tell
the difference answers confidently from a store nobody is watching. *No other
instrument catches it because* the sensor table proves each health value blocks
`fresh` in isolation, and nothing else asserts that an operator running the
command is told which channel to go and fix.

PRD §4 CUJ-5, four steps and one failure branch. **Silence is never stability.**
"""

import datetime as _dt
import json
from collections.abc import Callable

import pytest

from adopt_cli.main import main
from adopt_const import SENSOR_MISSED_CADENCE_MULTIPLIER
from adopt_freshness import RULE_SENSOR_MISSED_CADENCE, RULE_SENSOR_UNHEALTHY, resolve_freshness
from adopt_obs import ExitCode, ManualClock
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft
from adopt_store.api import SqliteStoreHandle

# const-sync: ok -- a fixture cadence chosen for legibility, not a product value.
_CADENCE_SECONDS = 900


def _watched_fresh_item(
    store: SqliteStoreHandle,
    scope: Scope,
    set_item_freshness: Callable[..., None],
    *,
    heartbeat: bool = True,
) -> tuple[str, str]:
    """A `fresh` item and the healthy sensor watching its environment."""
    identity = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )
    item_id, _ = store.items().create(
        scope=scope,
        kind="answer",
        title="How a refund is issued",
        revision=KnowledgeRevisionDraft(authority_class="behavior_observed", body_md="v1"),
    )
    store.bindings().create(
        item_id=item_id,
        identity_id=identity.id,
        is_load_bearing=True,
        revision=BindingRevisionDraft(status="active", locator_rung=1),
    )
    sensor = store.sensors().register(
        scope=scope, kind="webhook", expected_cadence_seconds=_CADENCE_SECONDS
    )
    if heartbeat:
        store.sensors().heartbeat(sensor_id=sensor.id, outcome="success")
    set_item_freshness(item_id=item_id, state="fresh")
    return item_id, sensor.id


@pytest.mark.e2e
def test_cuj5_a_degraded_sensor_flips_freshness_to_observation_stale(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    s4_clock: ManualClock,
    set_item_freshness: Callable[..., None],
) -> None:
    item_id, sensor_id = _watched_fresh_item(s4_store, s4_scope, set_item_freshness)
    records = s4_store.freshness_records()
    assert resolve_freshness(records, item_id, clock=s4_clock).state == "fresh"

    # Step 1 -- the sensor's health leaves HEALTHY and the reason is recorded.
    s4_store.sensors().degrade(
        sensor_id=sensor_id, health="DEGRADED", reason="webhook returning 5xx"
    )
    sensor = s4_store.sensors().get(sensor_id)
    assert sensor is not None
    assert sensor.degradation_reason == "webhook returning 5xx"

    # Step 2 -- affected knowledge returns `observation_stale`, not `fresh`.
    resolution = resolve_freshness(records, item_id, clock=s4_clock)

    assert resolution.state == "observation_stale"
    assert resolution.level == "system"
    assert resolution.deciding_rule == RULE_SENSOR_UNHEALTHY
    # Step 3 -- the channel is named, so the Answer path can qualify and an
    # operator knows what to go and fix.
    assert resolution.sensor_id == sensor_id


@pytest.mark.e2e
def test_cuj5_failure_branch_no_heartbeat_at_all_resolves_stale_not_healthy(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    s4_clock: ManualClock,
    set_item_freshness: Callable[..., None],
) -> None:
    """*Missing heartbeats past `SENSOR_MISSED_CADENCE_MULTIPLIER x
    expected_cadence_seconds` resolve to `STALE`, **not** to healthy. Connector
    silence is never read as stability.*

    Both silences are asserted: a channel that reported once and then went quiet,
    and one that never reported at all. The second is the dangerous one -- a
    sensor registered and never wired up looks identical to a healthy one to
    anything that only checks the stored `health` column.
    """
    quiet_item, _ = _watched_fresh_item(s4_store, s4_scope, set_item_freshness)
    records = s4_store.freshness_records()

    s4_clock.advance(_dt.timedelta(seconds=_CADENCE_SECONDS * SENSOR_MISSED_CADENCE_MULTIPLIER + 1))
    went_quiet = resolve_freshness(records, quiet_item, clock=s4_clock)

    assert went_quiet.state == "observation_stale"
    assert went_quiet.deciding_rule == RULE_SENSOR_MISSED_CADENCE


@pytest.mark.e2e
def test_cuj5_a_sensor_that_never_reported_is_not_evidence_of_health(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    s4_clock: ManualClock,
    set_item_freshness: Callable[..., None],
) -> None:
    item_id, _ = _watched_fresh_item(s4_store, s4_scope, set_item_freshness, heartbeat=False)

    resolution = resolve_freshness(s4_store.freshness_records(), item_id, clock=s4_clock)

    assert resolution.state == "observation_stale"


@pytest.mark.e2e
def test_cuj5_the_cli_reports_the_deciding_rule(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    set_item_freshness: Callable[..., None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`adopt freshness resolve --json` -- contracts §14, and the sprint's
    Final Output Validation item 5.

    The rule is the reason the command is worth running. `observation_stale` on
    its own tells an operator to wait; `observation_stale` *because this webhook
    is failing* tells them who to call.
    """
    item_id, sensor_id = _watched_fresh_item(s4_store, s4_scope, set_item_freshness)
    s4_store.sensors().degrade(
        sensor_id=sensor_id, health="DEGRADED", reason="webhook returning 5xx"
    )
    store_path = str(s4_store.backend.path)
    s4_store.backend.close()

    exit_code = main(["freshness", "resolve", "--item", item_id, "--store", store_path, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert set(payload) == {"state", "level", "deciding_rule"}
    assert payload["state"] == "observation_stale"
    assert payload["level"] == "system"
    assert payload["deciding_rule"] == RULE_SENSOR_UNHEALTHY
