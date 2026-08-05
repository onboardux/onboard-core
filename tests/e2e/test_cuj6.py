"""CUJ-6 — portability: export → fresh store → export, through the CLI.

The journey a client actually performs, driven through `adopt export` and
`adopt import` rather than through the library, because what a client is promised
is the *command*: the envelope keys, the exit codes and the bundle on disk are
the contract (§14), and a journey asserted at the library boundary would pass
with the CLI wired to the wrong store.

**Steps 1-4 and the failure branch**, in the order §4 states them:

1. Export a fully populated store.
2. Import into an empty store created from the same schema version.
3. Export again; the table files are byte-identical.
4. Every identity in the second export resolves by URI alone.

**Failure branch** — any digest mismatch on import refuses, applies nothing, and
names the file.
"""

import json
from pathlib import Path

import pytest

from adopt_cli.main import main
from adopt_const import EXPORT_VERSION
from adopt_export import TABLES_DIRNAME, table_relative_path, verify_roundtrip
from adopt_obs import ErrorCode, ExitCode, ManualClock
from adopt_store import open_store
from tests.golden.fixture import FIXTURE_START, build_fixture_store

pytestmark = pytest.mark.e2e


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(FIXTURE_START)


@pytest.fixture
def source_store(tmp_path: Path, clock: ManualClock, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A populated store, reachable the way a command resolves one."""
    path = tmp_path / "source.db"
    with open_store(path, migrate=True, clock=clock) as handle:
        build_fixture_store(handle, clock)
    monkeypatch.setenv("ADOPT_STORE_PATH", str(path))
    return path


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = main(argv)
    captured = capsys.readouterr().out
    return code, json.loads(captured) if captured.strip() else {}


def test_cuj6_export_import_export_is_byte_identical(
    source_store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "b1"
    restored = tmp_path / "s2.db"
    second = tmp_path / "b2"

    code, exported = _run(["export", str(first), "--json"], capsys)
    assert code == ExitCode.SUCCESS
    assert exported["export_version"] == EXPORT_VERSION
    assert exported["bundle_path"] == str(first)
    assert len(exported["tables"]) == len(list((first / TABLES_DIRNAME).iterdir()))
    assert int(exported["bytes"]) > 0

    code, applied = _run(["import", str(first), "--into", str(restored), "--json"], capsys)
    assert code == ExitCode.SUCCESS
    assert applied["tables"] == exported["tables"]

    code, _ = _run(["export", str(second), "--store", str(restored), "--json"], capsys)
    assert code == ExitCode.SUCCESS

    # Step 3, and the assertion the whole journey exists for.
    verify_roundtrip(first, second)


def test_cuj6_identities_resolve_by_uri_alone(
    source_store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Step 4, using `adopt identity parse` -- the only tool a bundle holder has."""
    bundle = tmp_path / "b3"
    _run(["export", str(bundle), "--json"], capsys)

    payload = (bundle / table_relative_path("identity")).read_bytes().decode("utf-8")
    identities = [json.loads(line) for line in payload.split("\n") if line]
    assert identities

    for row in identities:
        code, parsed = _run(["identity", "parse", str(row["uri"]), "--json"], capsys)
        assert code == ExitCode.SUCCESS
        assert parsed["firm"] == "northwind"
        assert parsed["engagement"] == "acme-erp"
        assert parsed["system"] == "orders-api"
        assert parsed["environment"] == "prod"
        assert parsed["kind"] == row["identity_kind"]


def test_cuj6_failure_branch_one_corrupted_byte_applies_nothing(
    source_store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A digest mismatch refuses, names the file, and leaves the target empty.

    The exit code is `1`: `EXPORT_DIGEST_MISMATCH` is an integrity failure, not a
    policy refusal, and §13 maps the two categories to different codes precisely
    so an operator's script can tell "you may not" from "something is wrong".
    """
    bundle = tmp_path / "b4"
    restored = tmp_path / "s4.db"
    _run(["export", str(bundle), "--json"], capsys)

    victim = bundle / table_relative_path("audience_tag")
    corrupted = bytearray(victim.read_bytes())
    corrupted[-2] ^= 0x20
    victim.write_bytes(bytes(corrupted))

    code = main(["import", str(bundle), "--into", str(restored), "--json"])
    # The error envelope goes to **stderr** (`adopt_cli.json_out`), so a caller
    # piping stdout into `jq` gets a result or nothing, never a result or an error.
    envelope = json.loads(capsys.readouterr().err)

    assert code == ExitCode.OPERATIONAL_FAILURE
    assert envelope["error"]["code"] == str(ErrorCode.EXPORT_DIGEST_MISMATCH)
    assert "audience_tag.ndjson" in envelope["error"]["message"]

    # Applied nothing: the store exists (import created it at the current schema
    # version before verifying) and holds no row from the bundle.
    with open_store(restored, read_only=True) as handle:
        assert handle.import_records().row_count("firm") == 0
