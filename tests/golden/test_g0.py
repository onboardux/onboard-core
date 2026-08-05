"""Gate **G0** — the portability promise, asserted rather than described.

PRD F9, CUJ-6, N4 and diagnostic D1; Build 0 definition of done, condition 2.
There is no soft-fail mode here, because the commercial promise -- *stop paying
and you keep the knowledge* -- does not have one either.

Three assertions, and each fails for a different reason:

1. **Byte-identical round trip.** Export, import into a fresh store, export
   again; the `tables/` files must match byte for byte. This is the one that
   catches a writer whose output depends on anything but its rows -- a key
   order, a timestamp format, a collation.
2. **Resolvable by URI alone.** Every identity in the *second* export must be
   findable from its URI and the bundle's own scope files, with no ULID lookup.
   This is what the URI design exists for: a consumer who has the bundle and not
   our database can still say what a row is about.
3. **An older binary opens a newer store read-only.** Implementation spec §7.4
   names exactly one recovery for a bad schema deploy, and it is older code
   against the newer store. A binary that corrupts, migrates or refuses that
   store has removed the only rollback surface the schema has.

The fixture covers **every** exportable table (`tests/golden/fixture.py`), and
`test_fixture_covers_every_exportable_table` is what keeps that true when a table
is added.
"""

import datetime
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from adopt_const import MAX_SUPPORTED_SCHEMA_VERSION
from adopt_export import (
    BLOBS_DIRNAME,
    MANIFEST_FILENAME,
    SCHEMA_FILENAME,
    apply_bundle,
    read_bundle,
    sha256_of_bytes,
    table_files,
    table_relative_path,
    verify_roundtrip,
    write_bundle,
)
from adopt_identity import parse_uri
from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_schema.manifest import Manifest
from adopt_scope import Scope
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle, writer_identity

pytestmark = pytest.mark.golden

_SCOPE_TABLES = ("firm", "engagement", "system", "environment")


def _export(handle: SqliteStoreHandle, target: Path, clock: ManualClock) -> Path:
    write_bundle(handle.export_records(), target, written_by=writer_identity(), clock=clock)
    return target


def _rows(bundle: Path, table: str) -> list[dict[str, object]]:
    """One table's rows, read the way a consumer of the bundle would."""
    payload = (bundle / table_relative_path(table)).read_bytes().decode("utf-8")
    return [json.loads(line) for line in payload.split("\n") if line]


def _round_trip(
    handle: SqliteStoreHandle, clock: ManualClock, workspace: Path
) -> tuple[Path, Path]:
    """Export, import into a fresh store, export again. Returns both bundles."""
    first = _export(handle, workspace / "first", clock)
    with open_store(workspace / "restored.db", migrate=True, clock=clock) as restored:
        apply_bundle(restored.import_records(), first)
        # Advanced between the two exports so `written_at` genuinely differs:
        # the comparison is of the table files alone (PRD F9.2), and a run in
        # which the two timestamps happened to match would not have tested that.
        clock.advance(datetime.timedelta(seconds=90))
        second = _export(restored, workspace / "second", clock)
    return first, second


# ---------------------------------------------------------------------------
# The fixture's own precondition
# ---------------------------------------------------------------------------


def test_fixture_covers_every_exportable_table(
    golden_store: tuple[SqliteStoreHandle, Scope],
    golden_clock: ManualClock,
    manifest: Manifest,
    tmp_path: Path,
) -> None:
    """Every exportable table holds at least one row.

    Asserted against the *manifest*, not a checked-in list, so adding a table
    without extending the fixture fails here -- rather than silently narrowing
    what G0 covers, which is the failure that would make the other three
    assertions quietly weaker every sprint.
    """
    handle, _ = golden_store
    read = read_bundle(_export(handle, tmp_path / "coverage", golden_clock))

    assert sorted(entry.name for entry in read.tables) == sorted(
        name for name, _ in manifest.exportable_tables()
    )
    empty = sorted(entry.name for entry in read.tables if entry.rows == 0)
    assert not empty, (
        f"the G0 fixture leaves {empty} empty, so the round trip is unproven for those "
        "tables. Add a row in tests/golden/fixture.py."
    )


def test_schema_meta_is_never_exported(
    golden_store: tuple[SqliteStoreHandle, Scope], golden_clock: ManualClock, tmp_path: Path
) -> None:
    """The one non-exportable table, and the annex, are absent by construction.

    `schema_meta` records how a *store* was opened, not what a client knows, and
    the runtime annex is not in the canonical manifest at all -- so neither is
    filtered out, both are simply never iterated (contracts §12).
    """
    handle, _ = golden_store
    bundle = _export(handle, tmp_path / "annex", golden_clock)

    names = set(table_files(bundle))
    assert "schema_meta.ndjson" not in names
    assert "agent_run.ndjson" not in names
    assert b"runtime" not in (bundle / MANIFEST_FILENAME).read_bytes()


# ---------------------------------------------------------------------------
# Assertion 1 -- byte-identical round trip
# ---------------------------------------------------------------------------


def test_export_import_export_is_byte_identical(
    golden_store: tuple[SqliteStoreHandle, Scope], golden_clock: ManualClock, tmp_path: Path
) -> None:
    handle, _ = golden_store
    first, second = _round_trip(handle, golden_clock, tmp_path)

    verify_roundtrip(first, second)
    assert read_bundle(first).written_at != read_bundle(second).written_at


def test_verify_roundtrip_reports_a_planted_difference(
    golden_store: tuple[SqliteStoreHandle, Scope], golden_clock: ManualClock, tmp_path: Path
) -> None:
    """The instrument is watched failing, exactly as every other gate here is.

    Assertion 1 is only worth having if `verify_roundtrip` would notice. The
    difference is planted in a copy of the second bundle -- one row's value,
    which is the smallest thing a writer defect could change -- and the failure
    must name the file.
    """
    handle, _ = golden_store
    first = _export(handle, tmp_path / "planted-a", golden_clock)
    second = tmp_path / "planted-b"
    shutil.copytree(first, second)

    victim = second / table_relative_path("audience_tag")
    victim.write_bytes(victim.read_bytes().replace(b"engineering", b"operationsx"))

    with pytest.raises(AdoptError) as caught:
        verify_roundtrip(first, second)
    assert caught.value.code is ErrorCode.EXPORT_ROUNDTRIP_UNSTABLE
    assert "audience_tag.ndjson" in caught.value.message


# ---------------------------------------------------------------------------
# Assertion 2 -- every identity resolves by URI alone
# ---------------------------------------------------------------------------


def test_every_identity_resolves_by_uri_alone(
    golden_store: tuple[SqliteStoreHandle, Scope], golden_clock: ManualClock, tmp_path: Path
) -> None:
    """Parse each URI, walk the bundle's slugs, and land on the row.

    The resolution deliberately uses only what a *consumer of the bundle* has:
    the NDJSON files. Reading the store to check would be resolving by ULID with
    extra steps, which is the thing this assertion exists to rule out.
    """
    handle, _ = golden_store
    _, second = _round_trip(handle, golden_clock, tmp_path)

    slugs = {
        table: {str(row["id"]): str(row["slug"]) for row in _rows(second, table)}
        for table in _SCOPE_TABLES
    }
    identities = _rows(second, "identity")
    assert identities, "the fixture must hold at least one identity for this to mean anything"

    for row in identities:
        parsed = parse_uri(str(row["uri"]))
        assert parsed.firm == slugs["firm"][str(row["firm_id"])]
        assert parsed.engagement == slugs["engagement"][str(row["engagement_id"])]
        assert parsed.system == slugs["system"][str(row["system_id"])]
        assert parsed.environment == slugs["environment"][str(row["environment_id"])]
        assert parsed.kind == row["identity_kind"]
        # One segment, because the fixture's key is one segment whose slash is
        # data (`POST /v1/orders`). Comparing `key_path` would pass for a
        # three-segment key too, which is the distinction §4 exists to keep.
        assert parsed.key == (str(row["local_key"]),)


# ---------------------------------------------------------------------------
# Assertion 3 -- an older binary opens a newer store read-only
# ---------------------------------------------------------------------------


def test_newer_store_opens_read_only_and_is_not_corrupted(
    golden_store: tuple[SqliteStoreHandle, Scope], golden_clock: ManualClock, tmp_path: Path
) -> None:
    handle, _ = golden_store
    source = Path(handle.backend.path)
    handle.close()

    newer = tmp_path / "newer.db"
    shutil.copy2(source, newer)
    # Stand in for a store written by a future binary: `user_version` is the only
    # thing that makes a store newer, so raising it is the whole simulation.
    raw = sqlite3.connect(newer)
    try:
        raw.execute(f"PRAGMA user_version = {MAX_SUPPORTED_SCHEMA_VERSION + 1}")
        raw.commit()
    finally:
        raw.close()
    before = sha256_of_bytes(newer.read_bytes())

    with open_store(newer, clock=golden_clock) as opened:
        assert opened.read_only is True
        assert opened.restriction is not None
        assert opened.restriction.code is ErrorCode.SCHEMA_VERSION_TOO_NEW
        # Still readable: refusing to read is the failure mode §7.4 forbids.
        assert opened.backend.query("SELECT COUNT(*) AS n FROM firm")[0]["n"] == 1
        with pytest.raises(AdoptError) as caught:
            opened.scope().create_firm(slug="second-firm", name="Second")

    assert caught.value.code is ErrorCode.STORE_READ_ONLY
    assert sha256_of_bytes(newer.read_bytes()) == before, (
        "opening a newer store must not modify it: the rollback surface is older code "
        "reading the newer file, and a read that writes has destroyed it."
    )


# ---------------------------------------------------------------------------
# Bundle shape
# ---------------------------------------------------------------------------


def test_bundle_carries_the_schema_and_no_blobs_directory(
    golden_store: tuple[SqliteStoreHandle, Scope], golden_clock: ManualClock, tmp_path: Path
) -> None:
    handle, _ = golden_store
    bundle = _export(handle, tmp_path / "shape", golden_clock)

    assert (bundle / MANIFEST_FILENAME).is_file()
    assert (bundle / SCHEMA_FILENAME).is_file()
    # No exportable table at schema version 3 references a blob, so §11's
    # "present only when referenced" means the directory is absent.
    assert not (bundle / BLOBS_DIRNAME).exists()

    read = read_bundle(bundle)
    assert read.blobs.count == 0
    assert read.blobs.total_bytes == 0
    assert all(entry.omitted_columns == [] for entry in read.tables)
    assert read.scope.firm == "northwind"
    assert read.scope.engagement == "acme-erp"
