"""Every way a bundle or a target is refused — and that nothing is applied.

*Fails when* import accepts a bundle it cannot faithfully restore, or applies
part of one before noticing. *Matters because* F9.5 makes import all-or-nothing
and the digest the thing that decides: a bundle is the client's copy of their own
knowledge, and half-restoring it produces a store that looks populated and is
wrong. *No other instrument catches it because* the round-trip property only ever
feeds import a bundle export just wrote, so it can prove the happy path and
nothing about a corrupt or foreign one.

**Each row asserts the target store is still empty afterwards**, not merely that
an error was raised. "Refused" and "refused without writing" are different
claims, and only the second is the contract.

**The digests are recomputed after each mutation that is not about digests.**
Otherwise every row in this table would fail at the digest check and the six
distinct refusals below would all be testing one of them.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from adopt_const import (
    EXPORT_NDJSON_MAX_LINE_BYTES,
    MAX_SUPPORTED_EXPORT_VERSION,
    SCHEMA_VERSION,
)
from adopt_export import (
    MANIFEST_FILENAME,
    apply_bundle,
    canonical_json,
    read_bundle,
    sha256_of_bytes,
    table_relative_path,
    write_bundle,
)
from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle, writer_identity
from tests.golden.fixture import FIXTURE_START, build_fixture_store

pytestmark = pytest.mark.unit

#: The table every mutation edits. Small, unambiguous, and one the fixture fills.
_VICTIM = "audience_tag"


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(FIXTURE_START)


@pytest.fixture
def bundle(tmp_path: Path, clock: ManualClock) -> Path:
    """A valid bundle written from the fixture store."""
    with open_store(tmp_path / "source.db", migrate=True, clock=clock) as handle:
        build_fixture_store(handle, clock)
        target = tmp_path / "bundle"
        write_bundle(handle.export_records(), target, written_by=writer_identity(), clock=clock)
    return target


@pytest.fixture
def target(tmp_path: Path, clock: ManualClock) -> SqliteStoreHandle:
    """An empty store at the current schema version, ready to import into."""
    handle = open_store(tmp_path / "target.db", migrate=True, clock=clock)
    yield handle
    handle.close()


def _read_manifest(bundle: Path) -> dict[str, object]:
    return json.loads((bundle / MANIFEST_FILENAME).read_bytes().decode("utf-8"))


def _write_manifest(bundle: Path, payload: dict[str, object]) -> None:
    (bundle / MANIFEST_FILENAME).write_bytes((canonical_json(payload) + "\n").encode("utf-8"))


def _rewrite_table(bundle: Path, table: str, content: bytes) -> None:
    """Replace a table file **and** the digest that describes it.

    Keeping the digest honest is what makes the row below test the check it
    names rather than the digest check that would otherwise fire first.
    """
    (bundle / table_relative_path(table)).write_bytes(content)
    payload = _read_manifest(bundle)
    for entry in payload["tables"]:  # type: ignore[union-attr]
        if entry["name"] == table:
            entry["sha256"] = sha256_of_bytes(content)
            entry["rows"] = len([line for line in content.decode("utf-8").split("\n") if line])
    _write_manifest(bundle, payload)


# ---------------------------------------------------------------------------
# Import refusals
# ---------------------------------------------------------------------------


def _unsupported_version(bundle: Path) -> None:
    payload = _read_manifest(bundle)
    payload["export_version"] = MAX_SUPPORTED_EXPORT_VERSION + 1
    _write_manifest(bundle, payload)


def _schema_version_contradicts_compat(bundle: Path) -> None:
    payload = _read_manifest(bundle)
    payload["schema_version"] = SCHEMA_VERSION + 1
    _write_manifest(bundle, payload)


def _corrupt_one_byte(bundle: Path) -> None:
    path = bundle / table_relative_path(_VICTIM)
    content = bytearray(path.read_bytes())
    content[-2] ^= 0x20
    path.write_bytes(bytes(content))


def _delete_a_table_file(bundle: Path) -> None:
    (bundle / table_relative_path(_VICTIM)).unlink()


def _declare_a_table_the_schema_has_not(bundle: Path) -> None:
    payload = _read_manifest(bundle)
    payload["tables"].append(  # type: ignore[union-attr]
        {"name": "invented_table", "rows": 0, "sha256": sha256_of_bytes(b""), "omitted_columns": []}
    )
    _write_manifest(bundle, payload)


def _row_with_an_unknown_column(bundle: Path) -> None:
    line = canonical_json({"item_id": "ki_x", "audience": "engineering", "invented": 1})
    _rewrite_table(bundle, _VICTIM, (line + "\n").encode("utf-8"))


def _line_over_the_limit(bundle: Path) -> None:
    oversized = "x" * (EXPORT_NDJSON_MAX_LINE_BYTES + 1)
    line = canonical_json({"item_id": "ki_x", "audience": oversized})
    _rewrite_table(bundle, _VICTIM, (line + "\n").encode("utf-8"))


def _remove_the_manifest(bundle: Path) -> None:
    (bundle / MANIFEST_FILENAME).unlink()


@pytest.mark.parametrize(
    ("mutate", "code", "names"),
    [
        (
            _unsupported_version,
            ErrorCode.EXPORT_VERSION_UNSUPPORTED,
            str(MAX_SUPPORTED_EXPORT_VERSION),
        ),
        (
            _schema_version_contradicts_compat,
            ErrorCode.EXPORT_VERSION_UNSUPPORTED,
            "schema_version",
        ),
        (_corrupt_one_byte, ErrorCode.EXPORT_DIGEST_MISMATCH, f"{_VICTIM}.ndjson"),
        (_delete_a_table_file, ErrorCode.EXPORT_BUNDLE_MALFORMED, _VICTIM),
        (_declare_a_table_the_schema_has_not, ErrorCode.EXPORT_BUNDLE_MALFORMED, "invented_table"),
        (_row_with_an_unknown_column, ErrorCode.EXPORT_BUNDLE_MALFORMED, "invented"),
        (_line_over_the_limit, ErrorCode.EXPORT_BUNDLE_MALFORMED, "limit"),
        (_remove_the_manifest, ErrorCode.EXPORT_BUNDLE_MALFORMED, MANIFEST_FILENAME),
    ],
    ids=[
        "export_version_above_the_supported_range",
        "schema_version_the_export_version_does_not_imply",
        "one_corrupted_byte",
        "a_table_file_the_manifest_names_is_missing",
        "a_table_this_schema_version_does_not_declare",
        "a_row_carrying_a_column_the_model_forbids",
        "a_line_over_the_ndjson_limit",
        "no_manifest_at_all",
    ],
)
def test_import_refuses_and_applies_nothing(
    bundle: Path,
    target: SqliteStoreHandle,
    mutate: Callable[[Path], None],
    code: ErrorCode,
    names: str,
) -> None:
    mutate(bundle)

    with pytest.raises(AdoptError) as caught:
        apply_bundle(target.import_records(), bundle)

    assert caught.value.code is code
    assert names in caught.value.message
    assert target.import_records().row_count(_VICTIM) == 0
    assert target.import_records().row_count("firm") == 0


def test_import_refuses_a_store_that_already_holds_rows(
    bundle: Path, target: SqliteStoreHandle, clock: ManualClock
) -> None:
    """Import is a restore, never a merge.

    Asserted with a store the *same* bundle was already imported into, which is
    the realistic mistake -- running the command twice -- rather than a store
    populated some other way.
    """
    apply_bundle(target.import_records(), bundle)

    with pytest.raises(AdoptError) as caught:
        apply_bundle(target.import_records(), bundle)

    assert caught.value.code is ErrorCode.EXPORT_TARGET_NOT_EMPTY
    # The message names a table rather than saying "the store is not empty": an
    # operator who has to go looking is an operator who guesses.
    named = caught.value.message.rsplit("row(s) in ", maxsplit=1)[-1]
    assert target.import_records().row_count(named) == 1
    # The first import's rows are untouched: the second refusal is not a rollback
    # of the first, and a store that lost its contents to a repeated command
    # would be worse than one that merged them.
    assert target.import_records().row_count("firm") == 1


# ---------------------------------------------------------------------------
# Export refusals
# ---------------------------------------------------------------------------


def test_export_refuses_a_store_holding_two_firms(tmp_path: Path, clock: ManualClock) -> None:
    """A bundle names one firm, so a store holding two cannot produce one.

    The refusal happens **before anything is created**, because a partial bundle
    on disk is something an operator then has to reason about -- and a bundle
    directory that exists but is not a bundle is exactly what §11's
    manifest-written-last rule exists to prevent.
    """
    with open_store(tmp_path / "two.db", migrate=True, clock=clock) as handle:
        build_fixture_store(handle, clock)
        handle.scope().create_firm(slug="second-firm", name="Second")

        target = tmp_path / "refused"
        with pytest.raises(AdoptError) as caught:
            write_bundle(handle.export_records(), target, written_by=writer_identity(), clock=clock)

    assert caught.value.code is ErrorCode.EXPORT_SCOPE_AMBIGUOUS
    assert "second-firm" in caught.value.message
    assert not target.exists()


def test_export_refuses_a_store_with_no_scope(tmp_path: Path, clock: ManualClock) -> None:
    """An empty store has no scope to record, and a guessed one is worse than none."""
    with (
        open_store(tmp_path / "empty.db", migrate=True, clock=clock) as handle,
        pytest.raises(AdoptError) as caught,
    ):
        write_bundle(
            handle.export_records(),
            tmp_path / "nothing",
            written_by=writer_identity(),
            clock=clock,
        )

    assert caught.value.code is ErrorCode.EXPORT_SCOPE_AMBIGUOUS
    assert "no firm" in caught.value.message


def test_export_refuses_a_non_empty_directory(
    bundle: Path, tmp_path: Path, clock: ManualClock
) -> None:
    """Writing into an existing bundle would leave a manifest describing files it did not write."""
    with open_store(tmp_path / "again.db", migrate=True, clock=clock) as handle:
        build_fixture_store(handle, clock)
        with pytest.raises(AdoptError) as caught:
            write_bundle(handle.export_records(), bundle, written_by=writer_identity(), clock=clock)

    assert caught.value.code is ErrorCode.EXPORT_TARGET_NOT_EMPTY
    # The bundle already there is intact.
    assert read_bundle(bundle).export_version == MAX_SUPPORTED_EXPORT_VERSION
