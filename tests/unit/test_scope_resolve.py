"""Scope resolution -- contracts §2, §10 C8; implementation spec §5.2; PRD F1, F13.

`03` §5.2 calls this **T1: one case per abort path, each asserting zero writes.
These are the tests that make PRD F1 and F6 real.** The manifest:

| Behavior | Tier | Instrument |
|---|---|---|
| A missing firm / engagement / system / environment aborts | T1 | table, +fingerprint |
| A broken parent chain aborts | **T1** | two cases, +fingerprint |
| Two environments and no `--environment` aborts, listing both | **T1** | case, +fingerprint |
| One environment is auto-selected | T2 | case |
| A missing boundary aborts with the `adopt boundary` command | T1 | case, +fingerprint |
| Tier `T0` declines | T1 | case, +fingerprint |
| Every abort carries a runnable remediation command | T2 | swept over all aborts |
| `ResolvedScope` is frozen | T3 | case |

**Every abort case asserts the store fingerprint is unchanged.** The module has
no write port, so this is belt-and-braces -- and it is declared T1
defence-in-depth rather than accidental duplication: "writes nothing" is the
promise a client security reviewer is actually given, and a promise resting only
on "the function does not currently import a writer" is one refactor from false.
"""

from collections.abc import Callable

import pytest
from adopt_map.ports import ScopeLookupRecords
from adopt_map.scope_resolve import ResolvedScope, resolve_scope
from pydantic import ValidationError

from adopt_obs import AdoptError, ErrorCode, MapExitCode, map_exit_code_for
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

pytestmark = pytest.mark.unit


def _ids(store: SqliteStoreHandle, scope: Scope) -> dict[str, str]:
    assert scope.engagement and scope.system and scope.environment
    return {
        "firm_id": scope.firm.id,
        "engagement_id": scope.engagement.id,
        "system_id": scope.system.id,
        "environment_id": scope.environment.id,
    }


def _resolve(
    records: ScopeLookupRecords,
    ids: dict[str, str],
    *,
    environment_id: str | None = "keep",
    tier: str | None = "T2",
) -> ResolvedScope:
    return resolve_scope(
        records,
        firm_id=ids["firm_id"],
        engagement_id=ids["engagement_id"],
        system_id=ids["system_id"],
        environment_id=ids["environment_id"] if environment_id == "keep" else environment_id,
        archetype="web",
        tier=tier,  # type: ignore[arg-type]
    )


def test_a_resolvable_scope_carries_both_ids_and_slugs(
    s4_store: SqliteStoreHandle, s4_scope: Scope, scope_records: ScopeLookupRecords
) -> None:
    """B1-CR-25: ids populate the scope columns, slugs build the URI. Both, always."""
    resolved = _resolve(scope_records, _ids(s4_store, s4_scope))

    assert resolved.firm_id.startswith("firm_")
    assert resolved.environment_slug == "prod"
    assert resolved.scope.slugs() == ("northwind", "acme-erp", "orders-api", "prod")
    assert resolved.tier == "T2"


@pytest.mark.parametrize(
    ("field", "bogus"),
    [
        ("firm_id", "firm_00000000000000000000000000"),
        ("engagement_id", "eng_00000000000000000000000000"),
        ("system_id", "sys_00000000000000000000000000"),
        ("environment_id", "env_00000000000000000000000000"),
    ],
)
def test_an_unresolvable_id_aborts_and_writes_nothing(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    scope_records: ScopeLookupRecords,
    store_fingerprint: Callable[[], str],
    field: str,
    bogus: str,
) -> None:
    """*Fails when* a missing id is treated as a warning rather than an abort.

    *Matters because* PRD F1.2 makes this the acceptance signal for F1: a config
    naming a non-existent environment exits 4 **before the file walk** and writes
    nothing. *No other instrument catches it because* a run that continued would
    produce a plausible map attributed to a scope that does not exist.
    """
    before = store_fingerprint()
    ids = _ids(s4_store, s4_scope) | {field: bogus}

    with pytest.raises(AdoptError) as caught:
        _resolve(scope_records, ids)

    assert caught.value.code is ErrorCode.MAP_SCOPE_UNRESOLVED
    assert map_exit_code_for(caught.value.code) == MapExitCode.DECLINED
    assert store_fingerprint() == before


def test_a_broken_parent_chain_aborts_and_writes_nothing(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    scope_records: ScopeLookupRecords,
    store_fingerprint: Callable[[], str],
) -> None:
    """*Fails when* only existence is checked and parentage is not.

    *Matters because* every written row carries all four scope columns, and a
    system whose engagement belongs to a different firm puts **one client's
    system inside another client's engagement** -- which the plane's RLS policy
    is then expressed from. *No other instrument catches it because* all four ids
    exist, so an existence-only check passes and the rows look entirely ordinary.
    """
    facade = s4_store.scope()
    other_firm = facade.create_firm(slug="contoso", name="Contoso Ltd")
    other_engagement = facade.create_engagement(
        firm_id=other_firm.id, slug="other-erp", name="Other ERP"
    )
    before = store_fingerprint()

    ids = _ids(s4_store, s4_scope) | {"firm_id": other_firm.id}
    with pytest.raises(AdoptError) as caught:
        _resolve(scope_records, ids)
    assert caught.value.code is ErrorCode.MAP_SCOPE_UNRESOLVED
    assert "chain is broken" in caught.value.message

    ids = _ids(s4_store, s4_scope) | {
        "firm_id": other_firm.id,
        "engagement_id": other_engagement.id,
    }
    with pytest.raises(AdoptError) as caught:
        _resolve(scope_records, ids)
    assert caught.value.code is ErrorCode.MAP_SCOPE_UNRESOLVED
    assert "chain is broken" in caught.value.message

    assert store_fingerprint() == before


def test_a_single_environment_is_selected_without_being_named(
    s4_store: SqliteStoreHandle, s4_scope: Scope, scope_records: ScopeLookupRecords
) -> None:
    """Rule 3's first half: exactly one environment, so there is nothing to choose."""
    resolved = _resolve(scope_records, _ids(s4_store, s4_scope), environment_id=None)
    assert resolved.environment_slug == "prod"


def test_two_environments_and_no_flag_aborts_listing_both(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    s4_scope_staging: Scope,
    scope_records: ScopeLookupRecords,
    store_fingerprint: Callable[[], str],
) -> None:
    """*Fails when* a default environment is introduced.

    *Matters because* "default to production" is **precisely** the failure the
    mandatory environment segment exists to prevent (PRD F1.4, B1-CR-20): a
    staging run silently attributed to production would merge two disjoint
    identity sets, and nothing downstream could separate them again. *No other
    instrument catches it because* the run would succeed and the map would look
    correct.
    """
    before = store_fingerprint()

    with pytest.raises(AdoptError) as caught:
        _resolve(scope_records, _ids(s4_store, s4_scope), environment_id=None)

    assert caught.value.code is ErrorCode.MAP_ENVIRONMENT_AMBIGUOUS
    assert map_exit_code_for(caught.value.code) == MapExitCode.DECLINED
    # Both are listed, by slug and by id, so the operator can act without a query.
    assert "prod" in caught.value.message
    assert "staging" in caught.value.message
    assert s4_scope_staging.environment is not None
    assert s4_scope_staging.environment.id in caught.value.message
    assert store_fingerprint() == before


def test_a_missing_boundary_aborts_with_the_boundary_command(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    scope_records: ScopeLookupRecords,
    store_fingerprint: Callable[[], str],
) -> None:
    """*Fails when* Build 1 writes a provisional boundary row (B1-CR-06).

    *Matters because* Doc 8 is implemented and inventing a boundary would let an
    **unnegotiated tier silently license claims** about a client's system. *No
    other instrument catches it because* a provisional row makes the run succeed,
    and the resulting map is indistinguishable from a negotiated one.
    """
    before = store_fingerprint()

    with pytest.raises(AdoptError) as caught:
        _resolve(scope_records, _ids(s4_store, s4_scope), tier=None)

    assert caught.value.code is ErrorCode.MAP_BOUNDARY_MISSING
    assert map_exit_code_for(caught.value.code) == MapExitCode.DECLINED
    assert caught.value.hint is not None
    assert "adopt boundary" in caught.value.hint
    assert store_fingerprint() == before


def test_tier_t0_declines_and_writes_nothing(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    scope_records: ScopeLookupRecords,
    store_fingerprint: Callable[[], str],
) -> None:
    """*Fails when* `T0` is treated as a low tier rather than as a refusal.

    *Matters because* PRD F13.2 makes `T0` mean *observe nothing*: extraction
    must not begin, not merely be trimmed. *No other instrument catches it
    because* a tier-trimmed run still writes rows, and the client agreed to none.
    """
    before = store_fingerprint()

    with pytest.raises(AdoptError) as caught:
        _resolve(scope_records, _ids(s4_store, s4_scope), tier="T0")

    assert caught.value.code is ErrorCode.MAP_TIER_DECLINED
    assert map_exit_code_for(caught.value.code) == MapExitCode.DECLINED
    assert store_fingerprint() == before


def test_every_abort_carries_a_runnable_remediation_command(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    s4_scope_staging: Scope,
    scope_records: ScopeLookupRecords,
) -> None:
    """*Fails when* an abort is added without a remediation hint.

    *Matters because* `02` §2 rule 5 and §8's exit-4 row both promise *"the exact
    remediation command"*. An abort that stops without saying what to type is one
    the operator resolves by guessing, and the obvious guess -- `adopt init` --
    creates the scope row this build must never create. *No other instrument
    catches it because* the abort is otherwise entirely correct.
    """
    ids = _ids(s4_store, s4_scope)
    cases = [
        (ids | {"firm_id": "firm_0"}, {"environment_id": "keep", "tier": "T2"}),
        (ids | {"environment_id": "env_0"}, {"environment_id": "keep", "tier": "T2"}),
        (ids, {"environment_id": None, "tier": "T2"}),
        (ids, {"environment_id": "keep", "tier": None}),
        (ids, {"environment_id": "keep", "tier": "T0"}),
    ]
    for case_ids, kwargs in cases:
        with pytest.raises(AdoptError) as caught:
            _resolve(scope_records, case_ids, **kwargs)  # type: ignore[arg-type]
        hint = caught.value.hint or ""
        assert "adopt " in hint, f"{caught.value.code} has no remediation command"


def test_resolved_scope_is_frozen(
    s4_store: SqliteStoreHandle, s4_scope: Scope, scope_records: ScopeLookupRecords
) -> None:
    """*Fails when* `ResolvedScope` becomes mutable.

    *Matters because* it is passed read-only into every extractor, and PRD F6.2
    -- "extractors have no API through which to set or override the environment"
    -- is that sentence plus this frozen model. *No other instrument catches it
    because* the fuzz suite fuzzes extractor *output*, not what an extractor
    might do to a mutable context object it was handed.
    """
    resolved = _resolve(scope_records, _ids(s4_store, s4_scope))
    with pytest.raises(ValidationError):
        resolved.environment_slug = "prod-2"  # type: ignore[misc]
