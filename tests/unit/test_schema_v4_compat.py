"""Schema version 4 is additive, and the version-3 line keeps working.

Build 4 adds one table. Everything here exists because *adding* a table is the
operation with the most ways to silently break stores that already exist, and
every one of them is invisible on a freshly created store:

* the initial migration gets rewritten, so a store that already ran it has no
  pending migration and never receives the new table (the CR-53 failure, one
  layer up -- it surfaces as `no such table` at first use);
* the new tranche is emitted without its RLS policy, so the table exists in
  Postgres outside tenant isolation while looking entirely normal;
* an older bundle stops importing, which would quietly end the portability
  promise the export exists to make.

*No other instrument catches these* because `adopt-schema generate --check`
compares the tree against a fresh generation -- if the generator rewrote
`0001`, the check passes on the rewritten file and reports nothing.
"""

import json
from pathlib import Path

import pytest

from adopt_const import (
    EXPORT_VERSION,
    INITIAL_SCHEMA_VERSION,
    MIN_SUPPORTED_EXPORT_VERSION,
    MIN_SUPPORTED_SCHEMA_VERSION,
    SCHEMA_VERSION,
)
from adopt_export import MANIFEST_FILENAME, apply_bundle, table_relative_path, write_bundle
from adopt_export.bundle import canonical_json
from adopt_obs import ManualClock
from adopt_schema.emitters import postgres, sqlite
from adopt_schema.generate import MIGRATIONS, repo_root
from adopt_schema.manifest import load_manifest
from adopt_store import open_store
from adopt_store.api import writer_identity
from tests.golden.fixture import FIXTURE_START, build_fixture_store

pytestmark = pytest.mark.unit

#: The table version 4 introduced. Named once so every assertion below is about
#: the same thing.
_V4_TABLE = "coverage_gap"


def _clock() -> ManualClock:
    return ManualClock(FIXTURE_START)


@pytest.fixture(scope="module")
def manifest():  # type: ignore[no-untyped-def]
    return load_manifest()


def test_the_initial_migration_is_byte_identical_under_a_later_manifest(manifest) -> None:  # type: ignore[no-untyped-def]
    """Version 3's file on disk is exactly what the v4 manifest still emits for it.

    *Fails when* a build adds a table and the initial migration changes as a
    side effect -- a different `PRAGMA user_version`, a different
    `schema-version` marker, or the new table appended to it. *Matters because*
    that file has already run against real stores: rewriting it means those
    stores have no pending migration and silently lack the table.
    """
    for dialect, emitter in (("sqlite", sqlite), ("postgres", postgres)):
        committed = (repo_root() / "schema" / "migrations" / dialect / MIGRATIONS[0][1]).read_text(
            encoding="utf-8"
        )
        assert emitter.emit(manifest, version=INITIAL_SCHEMA_VERSION) == committed, (
            f"{dialect} initial migration changed. It has already run on real stores; "
            "a table added at a later version belongs in that version's own file."
        )


def test_the_initial_migration_declares_three_and_carries_no_v4_table() -> None:
    for dialect in ("sqlite", "postgres"):
        text = (repo_root() / "schema" / "migrations" / dialect / MIGRATIONS[0][1]).read_text(
            encoding="utf-8"
        )
        assert f"-- schema-version: {INITIAL_SCHEMA_VERSION}" in text
        assert _V4_TABLE not in text


def test_the_v4_tranche_creates_only_its_own_table_and_sets_the_version() -> None:
    """The later tranche is exactly what version 4 introduced, and nothing else."""
    version, filename = MIGRATIONS[-1]
    assert version == SCHEMA_VERSION

    sqlite_text = (repo_root() / "schema" / "migrations" / "sqlite" / filename).read_text(
        encoding="utf-8"
    )
    assert f"-- schema-version: {version}" in sqlite_text
    # Read by `SqliteStore.current_version`, and applied inside the migration
    # transaction -- without it the file re-applies on every open.
    assert f"PRAGMA user_version = {version};" in sqlite_text
    assert f"CREATE TABLE {_V4_TABLE} (" in sqlite_text
    assert sqlite_text.count("CREATE TABLE ") == 1
    assert "-- back-out:" in sqlite_text


def test_the_v4_tranche_carries_its_row_level_security_policy() -> None:
    """A scoped table must never exist for even one migration without its policy.

    *Fails when* a later tranche emits DDL and leaves the derived policy in some
    other file, or nowhere. *Matters because* the table then holds tenant rows
    outside tenant isolation while looking entirely normal in the DDL -- the
    exact hole `test_no_canonical_table_is_unscoped` exists to prevent, arriving
    through the one path that test does not look at.
    """
    text = (repo_root() / "schema" / "migrations" / "postgres" / MIGRATIONS[-1][1]).read_text(
        encoding="utf-8"
    )
    assert f"ALTER TABLE {_V4_TABLE} ENABLE ROW LEVEL SECURITY;" in text
    assert f"ALTER TABLE {_V4_TABLE} FORCE  ROW LEVEL SECURITY;" in text
    assert f"CREATE POLICY scope_isolation ON {_V4_TABLE}" in text


def test_every_migration_version_has_exactly_one_file(manifest) -> None:  # type: ignore[no-untyped-def]
    """The generator's ledger and the manifest agree about which versions exist."""
    assert [version for version, _ in MIGRATIONS] == manifest.migration_versions()


def test_a_fresh_store_reaches_the_current_version_and_holds_the_new_table(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "store.db", migrate=True, clock=_clock()) as handle:
        assert handle.schema_version == SCHEMA_VERSION
        assert handle.backend.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?;", (_V4_TABLE,)
        )


def test_a_version_three_bundle_still_imports_into_a_version_four_store(
    tmp_path: Path,
) -> None:
    """The portability promise, across the version boundary this build crossed.

    *Fails when* raising `EXPORT_VERSION` makes older bundles unreadable.
    *Matters because* the published v3 reference bundle -- and every bundle a
    customer already holds -- must keep importing forever; that standing
    self-serve export is the whole of the cancellation-portability claim
    (v6.1 §7), and additive-only is what is supposed to make it safe.
    *No other instrument catches it because* the round-trip gate exports and
    imports with the same binary, so both halves move together and agree.

    The bundle is written by this binary and then relabelled as version 3 with
    the v4-only table removed -- which is precisely what a v3 bundle is. Table
    digests are untouched, so this exercises version negotiation rather than
    the digest check.
    """
    with open_store(tmp_path / "source.db", migrate=True, clock=_clock()) as handle:
        build_fixture_store(handle, _clock())
        bundle = tmp_path / "bundle"
        write_bundle(handle.export_records(), bundle, written_by=writer_identity(), clock=_clock())

    payload = json.loads((bundle / MANIFEST_FILENAME).read_bytes().decode("utf-8"))
    payload["export_version"] = MIN_SUPPORTED_EXPORT_VERSION
    payload["schema_version"] = MIN_SUPPORTED_SCHEMA_VERSION
    payload["tables"] = [entry for entry in payload["tables"] if entry["name"] != _V4_TABLE]
    (bundle / MANIFEST_FILENAME).write_bytes((canonical_json(payload) + "\n").encode("utf-8"))
    (bundle / table_relative_path(_V4_TABLE)).unlink()

    with open_store(tmp_path / "target.db", migrate=True, clock=_clock()) as target:
        applied = apply_bundle(target.import_records(), bundle)
        assert applied.tables
        # The v3 bundle carried no dispositions, and the v4 store it landed in
        # has the table ready and empty rather than absent.
        # The table name is a module constant, not input -- written literally so
        # the statement carries no interpolation at all.
        assert target.backend.query("SELECT COUNT(*) AS n FROM coverage_gap;")[0]["n"] == 0


def test_the_current_bundle_carries_the_new_table(tmp_path: Path) -> None:
    """A v4 export includes `coverage_gap`, so a disposition survives a round trip."""
    with open_store(tmp_path / "source.db", migrate=True, clock=_clock()) as handle:
        build_fixture_store(handle, _clock())
        bundle = tmp_path / "bundle"
        write_bundle(handle.export_records(), bundle, written_by=writer_identity(), clock=_clock())

    payload = json.loads((bundle / MANIFEST_FILENAME).read_bytes().decode("utf-8"))
    names = {entry["name"] for entry in payload["tables"]}
    assert _V4_TABLE in names
    assert payload["export_version"] == EXPORT_VERSION
    assert payload["schema_version"] == SCHEMA_VERSION
