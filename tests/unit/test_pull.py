"""`adopt pull`: the replica is refreshed, or the old one is still there.

Build 7 S7.3, v6.1 §6 demo line 4. Every case here aims at a failure that
destroys something, because that is the only risk this verb carries: it replaces
a store file wholesale, and the two things it must never do are replace the
**wrong** file and replace the right one with **half** a store.

    the safety refusals   -> a field store's unexported canon, gone
    the digest check      -> a truncated bundle believed whole
    the atomic swap       -> a replica that is neither the old one nor the new
    the marker            -> a replica nobody can trace to a source

**The transport is real**, on `test_remote_mode`'s precedent: a loopback
`ThreadingHTTPServer` serves a real tar of a real bundle, so `urllib`, the
`Authorization` header, the timeout and the digest header all actually run.
Patching `fetch_bytes` would assert that this module calls a function, which is
not in doubt, and would pass just as happily against a pull that never verified
anything.
"""

import datetime as _dt
import hashlib
import io
import json
import tarfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, Final

import pytest

from adopt_cli.commands.pull import check_target, run_pull
from adopt_cli.remote import PLANE_SYSTEM_KEY, PLANE_TOKEN_ENV_KEY, PLANE_URL_KEY, Remote
from adopt_cli.replica import ReplicaMarker, marker_path, read_marker, write_marker
from adopt_export import MANIFEST_FILENAME, write_bundle
from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle, writer_identity

pytestmark = pytest.mark.unit

_TOKEN_VARIABLE: Final[str] = "ADOPT_PLANE_TOKEN"
_TOKEN: Final[str] = "handle.secret-not-a-real-token"
_SYSTEM: Final[str] = "sys_orders"
_OTHER_SYSTEM: Final[str] = "sys_billing"
_AT: Final[_dt.datetime] = _dt.datetime(2026, 8, 27, 12, 0, 0, tzinfo=_dt.UTC)


def _archive_of(bundle: Path) -> bytes:
    """Tar a bundle directory the way `plane_api.export` does."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(bundle.rglob("*"), key=lambda p: p.relative_to(bundle).as_posix()):
            archive.add(path, arcname=path.relative_to(bundle).as_posix(), recursive=False)
    return buffer.getvalue()


class _Plane(BaseHTTPRequestHandler):
    """Serves whatever the test staged, and records what was asked for."""

    body: ClassVar[bytes] = b""
    digest_header: ClassVar[str | None] = None
    seen: ClassVar[dict[str, Any]] = {}

    def do_GET(self) -> None:
        _Plane.seen = {"path": self.path, "authorization": self.headers.get("Authorization")}
        self.send_response(200)
        self.send_header("Content-Type", "application/x-tar")
        self.send_header("Content-Length", str(len(_Plane.body)))
        if _Plane.digest_header is not None:
            self.send_header("X-Adopt-Bundle-Sha256", _Plane.digest_header)
        self.end_headers()
        self.wfile.write(_Plane.body)

    def log_message(self, *_args: Any) -> None:
        """Silence. The suite's output is not this server's log."""


@pytest.fixture
def field_bundle(tmp_path: Path) -> Path:
    """A real bundle, written by the real writer from a real store."""
    clock = ManualClock(_AT)
    source = tmp_path / "source.db"
    handle: SqliteStoreHandle = open_store(source, migrate=True, clock=clock)
    try:
        facade = handle.scope()
        firm = facade.create_firm(slug="northwind", name="Northwind LLP")
        engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
        system = facade.create_system(
            engagement_id=engagement.id, slug="orders-api", name="Orders API"
        )
        facade.create_environment(system_id=system.id, slug="prod", name="Production")
        bundle = tmp_path / "bundle"
        write_bundle(handle.export_records(), bundle, written_by=writer_identity(), clock=clock)
    finally:
        handle.close()
    return bundle


@pytest.fixture
def plane(field_bundle: Path) -> Iterator[str]:
    """A loopback stand-in for the plane's `/v1/export`. Returns its base URL."""
    _Plane.body = _archive_of(field_bundle)
    _Plane.digest_header = hashlib.sha256(_Plane.body).hexdigest()
    _Plane.seen = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Plane)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
    thread.daemon = True
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture
def configured(
    monkeypatch: pytest.MonkeyPatch, plane: str, tmp_path: Path
) -> Iterator[tuple[str, Path]]:
    """Environment naming the loopback plane and a replica path. Yields both."""
    replica = tmp_path / "replica" / "store.db"
    replica.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(PLANE_URL_KEY, plane)
    monkeypatch.setenv(PLANE_SYSTEM_KEY, _SYSTEM)
    monkeypatch.setenv(PLANE_TOKEN_ENV_KEY, _TOKEN_VARIABLE)
    monkeypatch.setenv(_TOKEN_VARIABLE, _TOKEN)
    monkeypatch.setenv("ADOPT_STORE_PATH", str(replica))
    yield plane, replica


def _remote(url: str = "http://plane.invalid", system: str = _SYSTEM) -> Remote:
    return Remote(url=url, system_id=system, token=_TOKEN)


def _stamp(store: Path, *, system: str) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"not really a database")
    write_marker(
        store,
        ReplicaMarker(
            plane_url="http://plane.invalid",
            system_id=system,
            pulled_at=_AT,
            bundle_sha256="0" * 64,
        ),
    )


# ---------------------------------------------------------------------------
# The safety refusals
# ---------------------------------------------------------------------------


def test_a_store_with_no_marker_is_refused(tmp_path: Path) -> None:
    """*Fails when* `pull` will silently replace a field store.

    *Matters because* a field store holding a week of unexported capture looks,
    to this command, exactly like a stale replica -- and only one of those is
    recoverable after the swap. The marker is the only thing that distinguishes
    them, so its absence has to mean "refuse", never "probably fine".

    *No other instrument catches it because* every other pull test starts from a
    store this command itself created, which always has a marker.
    """
    store = tmp_path / "field.db"
    store.write_bytes(b"canon nobody exported")

    with pytest.raises(AdoptError) as refused:
        check_target(store, _remote(), init_replica=False)

    assert refused.value.code is ErrorCode.PULL_TARGET_NOT_REPLICA
    assert "--init-replica" in (refused.value.hint or "")


def test_a_marker_naming_another_system_is_refused(tmp_path: Path) -> None:
    """*Fails when* one system's canon can land over another system's replica.

    *Matters because* an FDE working two engagements has two replicas and one
    shell, and `ADOPT_PLANE_SYSTEM` is the thing most likely to be stale in it.
    The refusal names both ids, because "wrong system" without saying which two
    sends somebody to the wrong config file.

    *No other instrument catches it because* the no-marker case above passes
    against a version that only checks for existence.
    """
    store = tmp_path / "other.db"
    _stamp(store, system=_OTHER_SYSTEM)

    with pytest.raises(AdoptError) as refused:
        check_target(store, _remote(), init_replica=False)

    assert refused.value.code is ErrorCode.PULL_TARGET_NOT_REPLICA
    assert _OTHER_SYSTEM in refused.value.message
    assert _SYSTEM in refused.value.message


def test_a_matching_replica_and_an_absent_file_are_both_permitted(tmp_path: Path) -> None:
    """The control. *Fails when* the refusals above refuse everything.

    *Matters because* a check that refused the ordinary case would make the two
    tests above pass while `adopt pull` never worked at all -- and "the second
    pull is silent" is the whole reason the first one is allowed to be noisy.
    """
    matching = tmp_path / "replica.db"
    _stamp(matching, system=_SYSTEM)
    check_target(matching, _remote(), init_replica=False)

    check_target(tmp_path / "nothing-here.db", _remote(), init_replica=False)


def test_init_replica_overrides_every_refusal(tmp_path: Path) -> None:
    """*Fails when* the escape hatch does not open.

    *Matters because* the refusals are deliberately strict, and an operator with
    no way past them either edits a marker by hand or deletes the store -- both
    of which are worse than the flag.
    """
    store = tmp_path / "other.db"
    _stamp(store, system=_OTHER_SYSTEM)

    check_target(store, _remote(), init_replica=True)


def test_an_unreadable_marker_refuses_rather_than_reading_as_absent(tmp_path: Path) -> None:
    """*Fails when* a corrupt marker degrades into "no marker", i.e. into "refuse
    unless it is a field store" -- which is the same branch, but reached for the
    wrong reason and one edit away from becoming "proceed".

    *Matters because* a truncated or hand-edited marker is a store somebody has
    told us something about. Treating the message as absent is the shape of
    every "we could not parse the safety check, so we skipped it" incident.
    """
    store = tmp_path / "replica.db"
    _stamp(store, system=_SYSTEM)
    marker_path(store).write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(AdoptError) as refused:
        read_marker(store)

    assert refused.value.code is ErrorCode.PULL_TARGET_NOT_REPLICA


# ---------------------------------------------------------------------------
# The pull itself
# ---------------------------------------------------------------------------


def test_a_pull_verifies_imports_swaps_and_stamps(configured: tuple[str, Path]) -> None:
    """The whole verb, over a real socket. *Fails when* any step of the sequence
    stops happening.

    *Matters because* this is demo line 4: an FDE's laptop becomes a **verified**
    replica. Each assertion below is one word of that -- the token was presented,
    the bundle was read whole, the store landed at the configured path, and the
    marker records what it is a replica of.

    *No other instrument catches it because* the safety cases above never fetch
    anything and the plane's own e2e never runs a CLI.
    """
    plane_url, replica = configured

    envelope = run_pull(None, init_replica=False, clock=ManualClock(_AT))

    assert _Plane.seen["path"] == "/v1/export"
    assert _Plane.seen["authorization"] == f"Bearer {_TOKEN}"

    assert replica.exists(), "the pull reported success and wrote no store"
    assert envelope["bytes"] == len(_Plane.body)
    assert envelope["replica"] == {
        "plane_url": plane_url,
        "system_id": _SYSTEM,
        "pulled_at": "2026-08-27T12:00:00.000Z",
        "bundle_sha256": hashlib.sha256(_Plane.body).hexdigest(),
    }

    # The store is real: it opens, and it holds the firm the bundle carried.
    handle = open_store(replica, read_only=True)
    try:
        assert handle.export_records().firm_slugs() == ["northwind"]
    finally:
        handle.close()

    stamped = read_marker(replica)
    assert stamped is not None and stamped.system_id == _SYSTEM


def test_a_second_pull_over_its_own_replica_is_silent(configured: tuple[str, Path]) -> None:
    """*Fails when* refreshing a replica needs `--init-replica` every time.

    *Matters because* a safety refusal an operator has to bypass on every run is
    a safety refusal they alias away, and then it is not protecting the case it
    was written for.
    """
    run_pull(None, init_replica=False, clock=ManualClock(_AT))
    run_pull(None, init_replica=False, clock=ManualClock(_AT))


def test_a_truncated_archive_is_refused_and_leaves_the_replica_intact(
    configured: tuple[str, Path],
) -> None:
    """*Fails when* a partial download becomes a partial store.

    *Matters because* the reader verifies each table file against the manifest
    and **cannot see** a response cut short whose remaining files all verify --
    which is exactly what a dropped connection produces. The digest the plane
    sent is the only thing that closes that gap.

    *And the second assertion is the one that matters*: refusing is worth little
    if the refusal happened after the swap. The replica from the first pull must
    still be there, and still open.
    """
    _, replica = configured
    run_pull(None, init_replica=False, clock=ManualClock(_AT))
    intact = replica.read_bytes()

    _Plane.body = _Plane.body[: len(_Plane.body) // 2]

    with pytest.raises(AdoptError) as refused:
        run_pull(None, init_replica=False, clock=ManualClock(_AT))

    assert refused.value.code is ErrorCode.EXPORT_DIGEST_MISMATCH
    assert replica.read_bytes() == intact, (
        "a refused pull replaced the replica anyway, which is the one failure the "
        "staged import and the atomic swap exist to make impossible"
    )


def test_a_pull_that_dies_before_the_swap_leaves_the_old_replica_answering(
    configured: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash injection between write and replace. *Fails when* the swap is not
    atomic -- when the store path can hold a half-written database.

    *Matters because* an FDE pulls on a train. A verb that leaves an unopenable
    file at `ADOPT_STORE_PATH` turns a lost refresh into a lost store, and the
    difference between those two is the whole argument for staging the import in
    a temporary file instead of importing over the live one.

    *No other instrument catches it because* every other test lets the pull
    finish, and a non-atomic implementation passes all of them.
    """
    _, replica = configured
    run_pull(None, init_replica=False, clock=ManualClock(_AT))
    intact = replica.read_bytes()
    marker_before = marker_path(replica).read_text(encoding="utf-8")

    class _Died(RuntimeError):
        """The process, as far as this pull is concerned."""

    original = Path.replace

    def _die(self: Path, target: Any) -> Path:
        if self.name == "store.db" and "adopt-pull-" in str(self.parent):
            raise _Died("killed between the staged import and the swap")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", _die)

    with pytest.raises(_Died):
        run_pull(None, init_replica=False, clock=ManualClock(_AT))

    assert replica.read_bytes() == intact
    assert marker_path(replica).read_text(encoding="utf-8") == marker_before

    handle = open_store(replica, read_only=True)
    try:
        assert handle.export_records().firm_slugs() == ["northwind"]
    finally:
        handle.close()


def test_a_pull_with_no_remote_configured_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Fails when* `adopt pull` on a field store does something instead of
    saying it cannot.

    *Matters because* absence means "you are an ordinary field store" for every
    other verb, and for this one it means there is nothing to pull from. Getting
    that wrong in the permissive direction would mean a `pull` that quietly did
    nothing and reported success -- and an FDE who believed they were current.
    """
    monkeypatch.delenv(PLANE_URL_KEY, raising=False)
    monkeypatch.setenv("ADOPT_STORE_PATH", str(tmp_path / "store.db"))

    with pytest.raises(AdoptError) as refused:
        run_pull(None, init_replica=False)

    assert refused.value.code is ErrorCode.PLANE_REMOTE_NOT_CONFIGURED


def test_a_bundle_naming_a_path_outside_itself_is_refused(configured: tuple[str, Path]) -> None:
    """*Fails when* an archive can write outside the directory it unpacks into.

    *Matters because* `tarfile` will follow `../../` unless told not to, and this
    archive arrives over a network. The server is ours today; "the server is
    ours" is the sentence at the top of every path-traversal advisory, and the
    refusal costs one loop.

    *No other instrument catches it because* every well-formed bundle passes
    whether the check exists or not.
    """
    _, replica = configured
    hostile = io.BytesIO()
    with tarfile.open(fileobj=hostile, mode="w", format=tarfile.PAX_FORMAT) as archive:
        payload = json.dumps({"escaped": True}).encode("utf-8")
        info = tarfile.TarInfo("../escaped.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    _Plane.body = hostile.getvalue()
    _Plane.digest_header = hashlib.sha256(_Plane.body).hexdigest()

    with pytest.raises(AdoptError) as refused:
        run_pull(None, init_replica=False, clock=ManualClock(_AT))

    assert refused.value.code is ErrorCode.EXPORT_BUNDLE_MALFORMED
    assert not (replica.parent.parent / "escaped.json").exists()


def test_the_marker_round_trips_through_its_own_file(tmp_path: Path) -> None:
    """*Fails when* what `pull` writes is not what the next `pull` reads.

    *Matters because* the marker is the input to the safety refusal, so a
    rendering the reader cannot parse turns every later pull into either a
    spurious refusal or -- if the reader ever became lenient -- a silent
    replacement. The instant is the part most likely to break, since it is the
    one field that is not already a string.
    """
    store = tmp_path / "replica.db"
    marker = ReplicaMarker(
        plane_url="https://plane.example",
        system_id=_SYSTEM,
        pulled_at=_AT,
        bundle_sha256="a" * 64,
    )
    write_marker(store, marker)

    assert read_marker(store) == marker
    assert json.loads(marker_path(store).read_text(encoding="utf-8"))["pulled_at"].endswith("Z")


def test_the_pulled_store_is_the_bundle_the_plane_named(configured: tuple[str, Path]) -> None:
    """*Fails when* the replica's contents and the served manifest disagree.

    *Matters because* the digest header proves the **archive** arrived whole and
    the reader proves each table file matches the manifest -- neither proves the
    rows reached the store. An import that silently dropped a table would pass
    both.
    """
    _, replica = configured
    envelope = run_pull(None, init_replica=False, clock=ManualClock(_AT))

    with tarfile.open(fileobj=io.BytesIO(_Plane.body)) as archive:
        member = archive.extractfile(MANIFEST_FILENAME)
        assert member is not None
        served = json.loads(member.read())

    assert {entry["name"] for entry in envelope["tables"]} == {
        entry["name"] for entry in served["tables"]
    }
    assert sum(entry["rows"] for entry in envelope["tables"]) == sum(
        entry["rows"] for entry in served["tables"]
    )
    assert replica.exists()
