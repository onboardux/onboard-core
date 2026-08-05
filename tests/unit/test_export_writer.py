"""Three writer guarantees the golden fixture cannot assert about itself.

The G0 fixture fills **every** table, which is what makes it the right instrument
for the round trip and the wrong one for these: a fixture with no empty table
cannot show that an empty table still produces a file, and hand-written readable
values cannot show that a timestamp keeps its milliseconds when they are zero.

*Fails when* absence becomes indistinguishable from emptiness (F9.4), when a
timestamp renders in a second format (§1.2), or when a manifest digest describes
something other than the bytes on disk. *Matters because* each is silent: a
consumer reading a bundle with a missing file cannot tell whether the table was
empty or the export was truncated. *No other instrument catches them because* the
round trip compares two bundles this writer produced, so a consistent mistake
passes it twice.
"""

import datetime as _dt
import re
from pathlib import Path
from typing import Final

import pytest

from adopt_export import (
    MANIFEST_FILENAME,
    TABLES_DIRNAME,
    read_bundle,
    sha256_of_bytes,
    table_relative_path,
    write_bundle,
)
from adopt_obs import ManualClock
from adopt_schema.manifest import load_manifest
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle, writer_identity

pytestmark = pytest.mark.unit

#: Contracts §1.2: RFC 3339, UTC, **millisecond precision**, `Z` suffix.
_TIMESTAMP: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

#: Deliberately on a whole second, so a writer that dropped zero milliseconds
#: would still satisfy a looser pattern and fail this one.
_ON_THE_SECOND: Final[_dt.datetime] = _dt.datetime(2026, 8, 5, 10, 30, 0, tzinfo=_dt.UTC)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(_ON_THE_SECOND)


@pytest.fixture
def scoped_store(tmp_path: Path, clock: ManualClock) -> SqliteStoreHandle:
    """A store with the scope chain and nothing else, so 32 tables are empty."""
    handle = open_store(tmp_path / "sparse.db", migrate=True, clock=clock)
    facade = handle.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    yield handle
    handle.close()


def test_every_exportable_table_produces_a_file_even_when_empty(
    scoped_store: SqliteStoreHandle, clock: ManualClock, tmp_path: Path
) -> None:
    """F9.4 — absence is never ambiguous with emptiness."""
    bundle = tmp_path / "sparse"
    write_bundle(scoped_store.export_records(), bundle, written_by=writer_identity(), clock=clock)

    expected = {name for name, _ in load_manifest().exportable_tables()}
    on_disk = {path.stem for path in (bundle / TABLES_DIRNAME).iterdir()}
    assert on_disk == expected

    empty = [entry for entry in read_bundle(bundle).tables if entry.rows == 0]
    assert empty, "this fixture must leave tables empty or it is testing nothing"
    for entry in empty:
        path = bundle / table_relative_path(entry.name)
        assert path.read_bytes() == b""
        # An empty file still carries the digest of no bytes, so a consumer
        # verifies it the same way as any other -- rather than special-casing
        # the one file whose absence would otherwise go unnoticed.
        assert entry.sha256 == sha256_of_bytes(b"")


def test_timestamps_keep_their_milliseconds(
    scoped_store: SqliteStoreHandle, clock: ManualClock, tmp_path: Path
) -> None:
    """§1.2 — one timestamp format, including when the milliseconds are zero."""
    import json

    bundle = tmp_path / "instants"
    write_bundle(scoped_store.export_records(), bundle, written_by=writer_identity(), clock=clock)

    assert _TIMESTAMP.match(read_bundle(bundle).written_at)

    payload = (bundle / table_relative_path("firm")).read_bytes().decode("utf-8")
    rows = [json.loads(line) for line in payload.split("\n") if line]
    assert rows, "the scope fixture writes one firm"
    assert _TIMESTAMP.match(str(rows[0]["created_at"]))


def test_row_order_is_the_primary_key_and_not_insertion_order(
    scoped_store: SqliteStoreHandle, clock: ManualClock, tmp_path: Path
) -> None:
    """§11 — rows are emitted by primary key ascending, whatever order they arrived in.

    Rows are inserted in **descending** id order, which is the one arrangement
    that separates the two rules: a writer that preserved insertion order would
    emit them backwards, and one that happened to sort would not. Random order
    would pass a writer that did nothing roughly half the time per table.

    Asserted separately from the round trip because the round trip compares two
    bundles this writer produced, so it would pass a writer that preserved
    insertion order on both sides -- stable within one realization, and exactly
    what breaks the day a Postgres store answers instead.
    """
    import json

    from adopt_model import AudienceTag, KnowledgeItem

    facade_scope = scoped_store.scope().resolve("northwind/acme-erp/orders-api/prod")
    assert facade_scope.engagement and facade_scope.system and facade_scope.environment

    now = clock.now()
    descending = [f"ki_{index:026d}" for index in range(5, 0, -1)]
    records = scoped_store.import_records()
    with scoped_store.backend.transaction():
        records.insert_rows(
            "knowledge_item",
            [
                KnowledgeItem(
                    id=item_id,
                    firm_id=facade_scope.firm.id,
                    engagement_id=facade_scope.engagement.id,
                    system_id=facade_scope.system.id,
                    environment_id=facade_scope.environment.id,
                    kind="answer",
                    title="synthetic",
                    freshness_state="unverified",
                    created_at=now,
                    updated_at=now,
                )
                for item_id in descending
            ],
        )
        # A composite primary key too: `(item_id, audience)` orders on both
        # columns, and a writer sorting on the first alone would pass a
        # single-column check forever.
        records.insert_rows(
            "audience_tag",
            [
                AudienceTag(item_id=item_id, audience=audience)
                for item_id in descending
                for audience in ("support", "engineering")
            ],
        )

    bundle = tmp_path / "ordered"
    write_bundle(scoped_store.export_records(), bundle, written_by=writer_identity(), clock=clock)

    def emitted(table: str, *columns: str) -> list[tuple[str, ...]]:
        payload = (bundle / table_relative_path(table)).read_bytes().decode("utf-8")
        rows = [json.loads(line) for line in payload.split("\n") if line]
        return [tuple(str(row[column]) for column in columns) for row in rows]

    items = emitted("knowledge_item", "id")
    assert items == sorted(items)
    assert items[0] != (descending[0],), "insertion order would have put the highest id first"

    tags = emitted("audience_tag", "item_id", "audience")
    assert tags == sorted(tags)


def test_every_manifest_digest_describes_the_bytes_on_disk(
    scoped_store: SqliteStoreHandle, clock: ManualClock, tmp_path: Path
) -> None:
    """The manifest is written last, and everything it names is already there."""
    bundle = tmp_path / "digests"
    write_bundle(scoped_store.export_records(), bundle, written_by=writer_identity(), clock=clock)

    read = read_bundle(bundle)
    for entry in read.tables:
        content = (bundle / table_relative_path(entry.name)).read_bytes()
        assert entry.sha256 == sha256_of_bytes(content)
        assert entry.rows == len([line for line in content.split(b"\n") if line])

    assert (bundle / MANIFEST_FILENAME).stat().st_mtime >= (
        bundle / table_relative_path(read.tables[0].name)
    ).stat().st_mtime
