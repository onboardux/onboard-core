"""`adopt pull` — the laptop becomes a verified read replica of the plane.

v6.1 §6 Build 7 demo line 4, and the half of R9 that makes the other half
liveable: once a system is operated the plane is the **sole writer**, so an FDE's
local store stops being canon and becomes a copy. A copy nobody can refresh is
a copy nobody trusts, and an FDE who cannot trust their own store goes back to
asking in Slack — which is the loop this build exists to delete.

**Consent is the configuration** (sprint plan D-9), exactly as it is for the
capture verbs. There is no `--allow-network`: the entire purpose of this verb is
to reach one configured host, so configuring that host *is* the decision, and its
absence is `PLANE_REMOTE_NOT_CONFIGURED` rather than a quiet no-op.

**The sequence, and why it is this order:**

    fetch → verify digests → import into a fresh file → swap → stamp

Verification happens before anything is written into a store, and the import
happens into a **new file** rather than into the live one, so the replica an FDE
is currently asking questions of is untouched until the moment a complete,
verified store is ready to take its place. The replacement is `Path.replace`
(`os.replace` underneath), which is atomic on every platform this ships to:
there is no instant at which the store path holds a half-written database. A crash anywhere before it leaves the
old replica intact and answering; a crash after it leaves the new one intact and
answering.

**The refusal that costs something, and is worth it.** A store with no replica
marker is refused (`PULL_TARGET_NOT_REPLICA`) unless `--init-replica`. That is
deliberately annoying the first time and silent every time after, because the
alternative is a verb that replaces a field store holding a week of unexported
work with a copy of somebody else's canon — and nothing recovers it. A marker
naming a **different** system is refused the same way and never overwritten by
default: pulling one system's canon over another's replica is the mistake most
likely to be made twice.

**The annex is deliberately not swapped.** It holds derived and operational
state — the retrieval index, agent-run idempotency, refresh file state — none of
which is canon and all of which is rebuildable from the store beside it. Moving
it here would make `pull` a verb that discards an FDE's local working state to
refresh their reading copy.
"""

import hashlib
import io
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Annotated, Any

import typer

from adopt_cli.commands._remote_support import configured_remote
from adopt_cli.json_out import emit
from adopt_cli.remote import _HINT as REMOTE_HINT
from adopt_cli.remote import Remote, fetch_bytes
from adopt_cli.replica import ReplicaMarker, marker_path, read_marker, write_marker
from adopt_cli.store_option import configured_store_path, open_named_store
from adopt_export import BundleManifest, apply_bundle
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, get_logger

__all__ = ["EXPORT_PATH", "check_target", "pull", "run_pull"]

_log = get_logger(__name__)

#: The plane's self-serve export. Named here rather than passed in, because a
#: `pull` that could be aimed at another path would be a download command that
#: happens to write a store.
EXPORT_PATH = "/v1/export"

_DIGEST_HEADER = "x-adopt-bundle-sha256"

StoreOption = Annotated[
    Path | None,
    typer.Option("--store", help="Store path. Defaults to the resolved ADOPT_STORE_PATH."),
]
InitOption = Annotated[
    bool,
    typer.Option(
        "--init-replica",
        help="Permit replacing a store that is not already a replica of this system.",
    ),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def _refuse(message: str, hint: str) -> AdoptError:
    return AdoptError(ErrorCode.PULL_TARGET_NOT_REPLICA, message=message, hint=hint)


def check_target(store: Path, remote: Remote, *, init_replica: bool) -> None:
    """Refuse unless `store` may be replaced by a pull from `remote`.

    Three states, and only the first two are safe by default:

    * **No file at all.** Nothing to lose; the pull creates one.
    * **A marker naming this system.** A replica being refreshed, which is the
      ordinary case and the one that must stay silent.
    * **Anything else** — a store with no marker, or a marker naming another
      system. Refused, because both may hold canon nobody has exported and the
      command's whole effect is to replace the file.

    `--init-replica` overrides all of it, and the log line records that it was
    used: a destructive flag that leaves no trace is a flag nobody can audit
    after the fact.

    Raises:
        AdoptError: ``PULL_TARGET_NOT_REPLICA``.
    """
    if init_replica:
        _log.warn("pull_init_replica", store=str(store), system_id=remote.system_id)
        return
    if not store.exists():
        return

    marker = read_marker(store)
    if marker is None:
        raise _refuse(
            f"{store} exists and carries no replica marker",
            "A store with no marker may hold canon that has never been exported, and "
            "`adopt pull` replaces the file wholesale. Export it first "
            "(`adopt export DIR`) if it is a field store, or pass `--init-replica` to "
            "say it is expendable. Every later pull against this path is silent.",
        )
    if marker.system_id != remote.system_id:
        raise _refuse(
            f"{store} is a replica of {marker.system_id}, not of {remote.system_id}",
            "Pulling one system's canon over another system's replica is the mistake "
            "most likely to be made twice, so it is never the default. Point --store "
            "at this system's own replica, correct ADOPT_PLANE_SYSTEM, or pass "
            "`--init-replica` to repurpose this path deliberately.",
        )


def _unpack(archive: bytes, into: Path) -> Path:
    """Write the served archive into `into` and return the bundle directory.

    **Members are checked before extraction, not after.** `tarfile` will happily
    write `../../etc/anything` if an archive says so, and the archive here comes
    off a network — a trusted one, but "we wrote the server" is the argument
    every path-traversal advisory starts with. `data` filtering plus the explicit
    refusal below means a malformed or hostile bundle fails naming itself rather
    than landing somewhere nobody looks.
    """
    bundle = into / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as opened:
        for member in opened.getmembers():
            target = (bundle / member.name).resolve()
            if not target.is_relative_to(bundle.resolve()):
                raise AdoptError(
                    ErrorCode.EXPORT_BUNDLE_MALFORMED,
                    message=f"the served bundle names a path outside itself: {member.name!r}",
                    hint="A bundle is a directory of table files. An entry that escapes "
                    "it is not a bundle this command will unpack.",
                )
        opened.extractall(bundle, filter="data")
    return bundle


def _payload(
    store: Path, manifest: BundleManifest, marker: ReplicaMarker, *, archive_bytes: int
) -> dict[str, Any]:
    """The §14-shaped envelope, plus what makes this store a replica.

    The first four keys are `adopt import`'s exactly, because a pull **is** an
    import with a fetch in front of it and an integrator comparing the two should
    not have to reconcile two shapes. `bytes` is therefore what moved -- the
    archive as served -- rather than a row count wearing the same name.

    The `replica` block is what is new, and it is what `adopt doctor` reads back.
    """
    return {
        "bundle_path": str(store),
        "export_version": manifest.export_version,
        "tables": [
            {"name": entry.name, "rows": entry.rows, "sha256": entry.sha256}
            for entry in manifest.tables
        ],
        "bytes": archive_bytes,
        "replica": marker.payload(),
    }


def run_pull(
    store: Path | None,
    *,
    init_replica: bool,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Do the pull and return its envelope. The verb below only renders it.

    **Split from `pull` so the clock can be injected**, and only for that
    reason: typer builds its option parser from the annotations of the function
    it is handed, and a `Clock` parameter is a type it refuses outright. Every
    other command in this package reaches for the store handle's clock
    (`commands/refresh._now`), which works because they open a store that
    already exists -- `pull` creates the one it will stamp, so there is no
    earlier clock to borrow.

    Raises:
        AdoptError: ``PLANE_REMOTE_NOT_CONFIGURED`` when no remote is configured
            or only part of one is; ``PULL_TARGET_NOT_REPLICA`` when the target
            is not this system's replica and `--init-replica` was not passed;
            ``EXPORT_DIGEST_MISMATCH`` / ``EXPORT_BUNDLE_MALFORMED`` from the
            reader, which verifies the bundle whole before a row is written;
            ``ADOPT_OFFLINE_DENIED`` when the plane cannot be reached.
    """
    ticking = clock if clock is not None else SystemClock()
    remote = configured_remote()
    if remote is None:
        # **`None` is a refusal here and a mode everywhere else**, which is the
        # one place `pull` differs from the capture verbs. For `adopt answer`,
        # no configured plane means an ordinary field store and the local path
        # runs unchanged; for `pull` there is nothing to pull *from*, so the
        # absence gets the same typed refusal a half-configured remote does --
        # and the same hint, which names all three keys.
        raise AdoptError(
            ErrorCode.PLANE_REMOTE_NOT_CONFIGURED,
            message="no control plane is configured, and `adopt pull` has nothing to pull from",
            hint=REMOTE_HINT,
        )

    target = configured_store_path(store)
    check_target(target, remote, init_replica=init_replica)

    archive, headers = fetch_bytes(remote, EXPORT_PATH)
    digest = hashlib.sha256(archive).hexdigest()
    served = headers.get(_DIGEST_HEADER)
    if served is not None and served != digest:
        # **The transport's own claim, checked.** The bundle's table digests are
        # verified by the reader below, so this adds one thing they cannot: that
        # what arrived is what the plane says it sent. A truncated response whose
        # remaining files all verify is exactly the case the reader cannot see.
        raise AdoptError(
            ErrorCode.EXPORT_DIGEST_MISMATCH,
            message=f"the plane served {served} and {digest} arrived",
            hint="The archive was altered or truncated in transit. Nothing has been "
            "written; the replica beside you is untouched. Re-run the pull.",
        )

    workspace = Path(tempfile.mkdtemp(prefix="adopt-pull-", dir=target.parent))
    try:
        bundle = _unpack(archive, workspace)
        staged = workspace / "store.db"
        # `apply_bundle` verifies every digest before it writes a row and refuses
        # a non-empty target, so "into a fresh file" is not caution here -- it is
        # what makes the reader's all-or-nothing boundary reach the live store.
        with open_named_store(staged, migrate=True) as handle:
            manifest = apply_bundle(handle.import_records(), bundle)

        marker = ReplicaMarker(
            plane_url=remote.url,
            system_id=remote.system_id,
            pulled_at=ticking.now(),
            bundle_sha256=digest,
        )
        staged_marker = write_marker(target, marker, target=workspace / "marker.json")

        # **Store first, then marker**, and the order matters exactly once: a
        # crash between them leaves a fresh replica carrying the previous pull's
        # marker -- same system, same plane, a stale instant and digest, which
        # the next pull corrects. The other order would leave a marker vouching
        # for a store that had not arrived.
        staged.replace(target)
        staged_marker.replace(marker_path(target))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    _log.info(
        "pull_completed",
        store=str(target),
        system_id=remote.system_id,
        tables=len(manifest.tables),
        rows=sum(entry.rows for entry in manifest.tables),
        sha256=digest,
    )
    return _payload(target, manifest, marker, archive_bytes=len(archive))


def pull(
    store: StoreOption = None,
    init_replica: InitOption = False,
    json_output: JsonOption = False,
) -> None:
    """Refresh this store from the control plane it replicates."""
    emit(run_pull(store, init_replica=init_replica), as_json=json_output, title="adopt pull")
