"""The slug rules, and the two ways a slug can be broken after it is assigned.

*Fails when* a value that is not a slug is accepted, an assigned slug is
renamed, or a slug belonging to an archived scope is handed to a new one.
*Matters because* the slug is what every identity URI is built from (CR-05): a
rename or a reissue silently re-points every URI ever emitted for the earlier
scope, and nothing downstream can tell that it happened. *No other instrument
catches it because* the DDL's `UNIQUE (parent, slug)` enforces uniqueness among
rows that exist and says nothing about the character class, the length bounds,
or whether an existing row's slug may change.

The last test is the PRD F3 acceptance signal end to end, through a real store:
*a system moved to `ARCHIVED` and a new system created in the same engagement
cannot take the retired slug.* It is here rather than in the store tests because
it is the same claim as the pure rows above, asserted where it can actually fail.
"""

import datetime as _dt
from pathlib import Path

import pytest

from adopt_const import SLUG_MAX_CHARS, SLUG_MIN_CHARS
from adopt_identity import build_uri
from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_scope import (
    Scope,
    ensure_slug_available,
    ensure_slug_unchanged,
    validate_slug,
)
from adopt_store import open_store

_MAX = "a" * SLUG_MAX_CHARS
_OVER = "a" * (SLUG_MAX_CHARS + 1)
_MIN = "a" * SLUG_MIN_CHARS
_UNDER = "a" * (SLUG_MIN_CHARS - 1)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("slug", "valid", "why"),
    [
        (_MIN, True, "the shortest permitted slug"),
        (_MAX, True, "the longest permitted slug"),
        ("north-wind", True, "internal hyphens are the separator"),
        ("0rders-api2", True, "a slug may start and end with a digit"),
        (_UNDER, False, "below SLUG_MIN_CHARS, which the pattern alone permits"),
        (_OVER, False, "above SLUG_MAX_CHARS"),
        ("Northwind", False, "uppercase: URIs compare byte-exact and never case-fold"),
        ("-northwind", False, "leading hyphen"),
        ("northwind-", False, "trailing hyphen"),
        ("north_wind", False, "underscore is not in the character class"),
        ("", False, "empty"),
        ("nörthwind", False, "non-ascii would encode differently in a URI segment"),
        ("north wind", False, "a space would have to be percent-encoded in a scope segment"),
    ],
)
def test_slug_validity(slug: str, valid: bool, why: str) -> None:
    if valid:
        validate_slug(slug, level="firm")
        return
    with pytest.raises(AdoptError) as raised:
        validate_slug(slug, level="firm")
    assert raised.value.code is ErrorCode.SCOPE_SLUG_INVALID, why


@pytest.mark.unit
def test_renaming_an_assigned_slug_is_refused() -> None:
    ensure_slug_unchanged("northwind", "northwind", level="firm")

    with pytest.raises(AdoptError) as raised:
        ensure_slug_unchanged("northwind", "northwind-llp", level="firm")

    assert raised.value.code is ErrorCode.SCOPE_SLUG_IMMUTABLE


@pytest.mark.unit
def test_a_taken_slug_is_refused_whatever_state_its_holder_is_in() -> None:
    ensure_slug_available("orders-api", [], level="system")

    with pytest.raises(AdoptError) as raised:
        ensure_slug_available("orders-api", ["orders-api"], level="system")

    assert raised.value.code is ErrorCode.SCOPE_SLUG_REUSED


@pytest.mark.unit
def test_an_archived_systems_slug_cannot_be_reissued(tmp_path: Path) -> None:
    """PRD F3 acceptance signal, through a real store."""
    clock = ManualClock(_dt.datetime(2026, 8, 3, tzinfo=_dt.UTC))
    with open_store(tmp_path / "store.db", migrate=True, clock=clock) as handle:
        scope = handle.scope()
        firm = scope.create_firm(slug="northwind", name="Northwind LLP")
        engagement = scope.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
        system = scope.create_system(
            engagement_id=engagement.id, slug="orders-api", name="Orders API"
        )

        scope.transition(system.id, "ARCHIVED", "engagement closed")

        with pytest.raises(AdoptError) as raised:
            scope.create_system(
                engagement_id=engagement.id, slug="orders-api", name="Orders API, again"
            )

    assert raised.value.code is ErrorCode.SCOPE_SLUG_REUSED


@pytest.mark.unit
def test_two_environments_on_one_system_mint_distinct_uri_segments(
    s4_scope: Scope, s4_scope_staging: Scope
) -> None:
    """Build 1's prerequisite 9, asserted rather than assumed.

    *Fails when* the fixture stops producing two distinct environments on one
    system. *Matters because* Build 1's environment-isolation gate (contracts
    C9, PRD N6) asserts a staging run emits zero production URIs, and with one
    environment in the store that assertion passes without testing anything --
    the same vacuity as a tenant-escape case run against a database with no
    second tenant. *No other instrument catches it because* every existing test
    uses a single environment and would stay green.
    """
    assert s4_scope.environment is not None
    assert s4_scope_staging.environment is not None
    assert s4_scope.environment.slug != s4_scope_staging.environment.slug
    assert s4_scope.system is not None and s4_scope_staging.system is not None
    assert s4_scope.system.id == s4_scope_staging.system.id, "one system, two environments"

    prod = build_uri(s4_scope, "endpoint", "http", "GET /v1/orders")
    staging = build_uri(s4_scope_staging, "endpoint", "http", "GET /v1/orders")

    assert prod != staging, "the environment segment must separate the two URIs"
    assert "/prod/" in prod and "/staging/" in staging
