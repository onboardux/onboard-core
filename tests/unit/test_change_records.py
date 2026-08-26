"""`ChangeFacade`: what a refresh records, and the two writes it refuses to widen.

Build 6's store half. The cascade itself is `adopt_map.diff`'s and is tested
there; these tests are about the recording being faithful, the classifier
version being a version rather than a run log, and propagation staling exactly
the bindings PRD F8.3 says it may.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

from adopt_model import Binding
from adopt_obs import ManualClock
from adopt_store import open_store
from adopt_store.facades.change import CLASSIFIER_LABEL
from tests.golden.fixture import FIXTURE_START

pytestmark = pytest.mark.unit

_BATCH = "refresh:01JQZZZZZZZZZZZZZZZZZZZZZZ"


@pytest.fixture
def store(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A store with one scope and two real identities.

    Real identities because `classification.identity_id` is a foreign key: a
    test on invented ids would assert facade behaviour against rows the database
    would refuse, which is the failure mode that makes a green unit test lie
    about an integration.
    """
    handle = open_store(tmp_path / "store.db", migrate=True, clock=ManualClock(FIXTURE_START))
    scopes = handle.scope()
    firm = scopes.create_firm(slug="northwind", name="Northwind LLP")
    engagement = scopes.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP rollout")
    system = scopes.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    scopes.create_environment(system_id=system.id, slug="prod", name="Production")
    scope = scopes.resolve("northwind/acme-erp/orders-api/prod")
    handle.identities().observe(scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders")
    handle.identities().observe(scope=scope, kind="config_key", namespace=None, key="DATABASE_URL")
    yield handle
    handle.close()


@pytest.fixture
def scope_ids(store) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    system = str(store.backend.query("SELECT id FROM system LIMIT 1")[0]["id"])
    environment = str(store.backend.query("SELECT id FROM environment LIMIT 1")[0]["id"])
    return system, environment


@pytest.fixture
def identity_ids(store) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    rows = store.backend.query("SELECT id FROM identity ORDER BY uri")
    return tuple(str(row["id"]) for row in rows)


@dataclass(frozen=True, slots=True)
class _Change:
    """A local realization of `ClassifiedChange`, which is a Protocol.

    Deliberately declared here rather than imported: the facade reads four
    fields off whatever it is handed, and the shape it is handed in production
    is `adopt_map.diff.ChangeEntry` -- which `no-raw-sqlite` forbids
    `adopt_store` from importing. A test that could only drive the facade with a
    type the facade owns would be proving the wiring it invented rather than the
    contract the CLI actually satisfies.
    """

    identity_id: str
    impact_class: str
    decided_by: str
    evidence: str


def _change(identity_id: str, impact_class: str = "BINDING_INTACT_SEMANTICS_CHANGED") -> _Change:
    return _Change(
        identity_id=identity_id,
        impact_class=impact_class,
        decided_by="cascade_step_3",
        evidence="digest a1 -> b2 at extractor v1",
    )


# -- recording --------------------------------------------------------------


def test_a_run_records_one_event_and_one_classification_per_identity(
    store, scope_ids, identity_ids
) -> None:  # type: ignore[no-untyped-def]
    """*Fails when* the event/classification split collapses. *Matters because*
    `classification`'s `(change_event_id, identity_id)` index is UNIQUE: one
    change means exactly one thing for one identity, and a facade writing an
    event per identity would make a rebase of two hundred files two hundred
    change events -- the flood v6.1 coalescing exists to prevent. *No other
    instrument catches it* because both shapes satisfy the foreign keys."""
    system_id, environment_id = scope_ids
    event_id, classification_ids = store.changes().record_run(
        system_id=system_id,
        environment_id=environment_id,
        source="artifact",
        batch_key=_BATCH,
        changes=[_change(identity_ids[0]), _change(identity_ids[1])],
    )

    assert len(classification_ids) == 2
    events = store.backend.query("SELECT * FROM change_event")
    assert len(events) == 1
    assert events[0]["id"] == event_id
    assert events[0]["source"] == "artifact"
    assert events[0]["batch_key"] == _BATCH
    rows = store.backend.query("SELECT * FROM classification ORDER BY id")
    assert [row["change_event_id"] for row in rows] == [event_id, event_id]
    assert [row["class"] for row in rows] == ["BINDING_INTACT_SEMANTICS_CHANGED"] * 2


def test_every_classification_records_the_deterministic_classifier_version(
    store, scope_ids, identity_ids
) -> None:  # type: ignore[no-untyped-def]
    """*Fails when* rows land with a NULL classifier version. *Matters because*
    v6.1 §6 Build 8 lands its ML classifier "as a version, not a rewrite" --
    which only works if the deterministic rows say which classifier decided
    them. Unversioned history cannot be compared against a successor."""
    system_id, environment_id = scope_ids
    store.changes().record_run(
        system_id=system_id,
        environment_id=environment_id,
        source="artifact",
        batch_key=_BATCH,
        changes=[_change(identity_ids[0])],
    )

    row = store.backend.query("SELECT * FROM classification")[0]
    assert row["classifier_version_id"] is not None
    version = store.backend.query(
        "SELECT * FROM classifier_version WHERE id = ?", (row["classifier_version_id"],)
    )[0]
    assert version["version_label"] == CLASSIFIER_LABEL


def test_two_runs_share_one_classifier_version_row(store, scope_ids, identity_ids) -> None:  # type: ignore[no-untyped-def]
    """*Fails when* get-or-create degrades to insert. *Matters because* a row
    per run turns a version table into a run log, and "which classifier decided
    this" stops having one answer. *No other instrument catches it* because
    `version_label` carries no UNIQUE index -- the duplicate is legal SQL."""
    system_id, environment_id = scope_ids
    for _ in range(2):
        store.changes().record_run(
            system_id=system_id,
            environment_id=environment_id,
            source="artifact",
            batch_key=_BATCH,
            changes=[_change(identity_ids[0])],
        )

    assert len(store.backend.query("SELECT * FROM classifier_version")) == 1


def test_a_run_with_nothing_to_report_writes_no_event(store, scope_ids) -> None:  # type: ignore[no-untyped-def]
    """*Fails when* an empty run records an event anyway. *Matters because* an
    event with no classification claims something changed without saying what it
    meant, and the clean-refresh promise (exit 0, zero rows) would be false."""
    system_id, environment_id = scope_ids
    with pytest.raises(ValueError, match="at least one classification"):
        store.changes().record_run(
            system_id=system_id,
            environment_id=environment_id,
            source="artifact",
            batch_key=_BATCH,
            changes=[],
        )
    assert store.backend.query("SELECT * FROM change_event") == []


def test_acted_silently_is_false_on_every_row(store, scope_ids, identity_ids) -> None:  # type: ignore[no-untyped-def]
    """*Fails when* v1 records a silent action. *Matters because* "nothing is
    silent in v1" is Build 6's stated promise; silence is earned per slice in
    Build 8 with measured precision and a sampled audit, and a `true` here would
    be a claim that a human never saw the change."""
    system_id, environment_id = scope_ids
    store.changes().record_run(
        system_id=system_id,
        environment_id=environment_id,
        source="artifact",
        batch_key=_BATCH,
        changes=[_change(identity_ids[0])],
    )
    assert store.backend.query("SELECT acted_silently FROM classification")[0][0] == 0


# -- reads ------------------------------------------------------------------


def test_the_batch_reads_return_only_that_runs_rows(store, scope_ids, identity_ids) -> None:  # type: ignore[no-untyped-def]
    """*Fails when* the batch join is dropped and every run's rows come back.
    *Matters because* `adopt review` renders one session: a queue showing last
    week's resolved changes beside today's is the flood, not the queue."""
    system_id, environment_id = scope_ids
    changes = store.changes()
    changes.record_run(
        system_id=system_id,
        environment_id=environment_id,
        source="artifact",
        batch_key=_BATCH,
        changes=[_change(identity_ids[0])],
    )
    changes.record_run(
        system_id=system_id,
        environment_id=environment_id,
        source="artifact",
        batch_key="refresh:other",
        changes=[_change(identity_ids[1])],
    )

    assert len(changes.classifications_in(_BATCH)) == 1
    assert len(changes.events_in(_BATCH)) == 1
    assert set(changes.classified_identities(_BATCH)) == {identity_ids[0]}


# -- propagation, the narrow write ------------------------------------------


def _binding(store, item_id: str, identity_id: str, *, load_bearing: bool) -> Binding:  # type: ignore[no-untyped-def]
    binding_id, _ = store.bindings().create(
        item_id=item_id, identity_id=identity_id, is_load_bearing=load_bearing
    )
    binding = store.bindings().get(binding_id)
    assert binding is not None
    return binding


def test_propagation_stales_load_bearing_bindings_and_leaves_the_rest(store, identity_ids) -> None:  # type: ignore[no-untyped-def]
    """**Invariant #4's store half.** *Fails when* the `is_load_bearing` filter
    is dropped. *Matters because* blanket propagation stales everything a shared
    identity touches -- mass false staleness, which trains reviewers to ignore
    the queue. *No other instrument catches it* because both bindings are
    otherwise identical rows and every foreign key still resolves."""
    scope = store.scope().resolve("northwind/acme-erp/orders-api/prod")
    load_bearing_item, _ = store.items().record(
        scope=scope,
        kind="procedure",
        title="Refund runbook",
        body_md="how refunds work",
        authority_class="human_confirmed",
    )
    incidental_item, _ = store.items().record(
        scope=scope,
        kind="procedure",
        title="Style guide",
        body_md="how we name things",
        authority_class="human_confirmed",
    )
    kept = _binding(store, load_bearing_item, identity_ids[0], load_bearing=True)
    ignored = _binding(store, incidental_item, identity_ids[0], load_bearing=False)

    staled = store.changes().stale_load_bearing_bindings(
        [kept, ignored], identity_ids=[identity_ids[0]]
    )

    assert staled == (kept.id,)
    states = {
        str(row["id"]): str(row["freshness_state"])
        for row in store.backend.query("SELECT id, freshness_state FROM binding")
    }
    assert states[kept.id] == "stale"
    assert states[ignored.id] != "stale"


def test_propagation_ignores_bindings_to_identities_that_did_not_change(
    store, identity_ids
) -> None:  # type: ignore[no-untyped-def]
    """**Invariant #4's negative case.** *Fails when* the identity filter is
    dropped and a refresh stales the whole store. *Matters because* an unrelated
    change staling bound knowledge is indistinguishable to a reviewer from a real
    one, and it is the failure that makes freshness worthless."""
    scope = store.scope().resolve("northwind/acme-erp/orders-api/prod")
    item_id, _ = store.items().record(
        scope=scope,
        kind="procedure",
        title="Config notes",
        body_md="about the database url",
        authority_class="human_confirmed",
    )
    unrelated = _binding(store, item_id, identity_ids[1], load_bearing=True)

    staled = store.changes().stale_load_bearing_bindings(
        [unrelated], identity_ids=[identity_ids[0]]
    )

    assert staled == ()
    state = store.backend.query("SELECT freshness_state FROM binding WHERE id = ?", (unrelated.id,))
    assert str(state[0][0]) != "stale"
