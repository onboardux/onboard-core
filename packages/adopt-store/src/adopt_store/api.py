"""`open_store` and the `Store` seam.

Implementation spec §4.7 behaviour 1 and contracts §10.3. Opening a store is
where two guarantees are made that everything above depends on:

**The version matrix, and why it opens rather than refuses.** A store newer than
the binary opens **read-only** and reports `SCHEMA_VERSION_TOO_NEW`; an older
store opened without `migrate` opens **read-only** and reports
`SCHEMA_MIGRATION_PENDING`. Neither raises, because §7.4 names exactly one
recovery for a bad schema deploy — *older code against the newer store* — and a
binary that refuses to open the store it was rolled back to have has removed the
only rollback surface the schema has. The condition travels on
`Store.restriction`, and any *write* then raises `STORE_READ_ONLY`.

**`schema_meta` is read and written on every open** (contracts §14, CR-04),
appended and never updated, which is what turns the table into the migration log
CR-04 describes. A read-only open reads it and cannot append — that is a
property of the file being read-only, not a branch anyone chose, and pretending
otherwise would mean a reader silently mutating a store it was asked not to
touch.

**Facades arrive with their tables.** §10.3 declares eleven accessors. `scope()`
is the one whose tables exist at this sprint; the rest land in the sprints that
create the tables they front (identities, items, bindings and revisions in S3,
coverage in S4, and so on). An accessor that raises is not a seam, it is a
placeholder wearing one.
"""

from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Final, Protocol

from adopt_const import (
    EXPORT_VERSION,
    MAX_SUPPORTED_SCHEMA_VERSION,
    MIN_SUPPORTED_SCHEMA_VERSION,
    SCHEMA_VERSION,
)
from adopt_obs import AdoptError, Clock, ErrorCode
from adopt_schema.migrate import apply as apply_migrations
from adopt_scope import ScopeFacade
from adopt_store.sqlite.records import SqliteScopeRecords
from adopt_store.sqlite.store import SqliteStore

__all__ = ["OpenRestriction", "Store", "open_store", "scope_facade", "writer_identity"]

_DISTRIBUTION: Final[str] = "adopt-store"
_SQLITE_DIALECT: Final[str] = "sqlite"


def scope_facade(store: SqliteStore, *, clock: Clock | None = None) -> ScopeFacade:
    """Bind the scope facade to a SQLite store.

    The assembly lives here rather than in `adopt_store.facades` because that
    package may not reach the driver even transitively (`no-raw-sqlite`), and
    binding a facade to a realization is exactly such a reach. The facade
    generates every id and accepts none, so there is no argument through which a
    caller could supply one (contracts §10.3).
    """
    return ScopeFacade(SqliteScopeRecords(store), clock=clock)


def writer_identity() -> str:
    """What lands in `schema_meta.written_by`, e.g. ``adopt-core/0.3.0``.

    Honest stamping: a development checkout that cannot resolve its own version
    says so rather than claiming a release number it does not have.
    """
    try:
        version = metadata.version(_DISTRIBUTION)
    except metadata.PackageNotFoundError:  # pragma: no cover -- source checkout only
        version = "0.0.0+unknown"
    return f"adopt-core/{version}"


@dataclass(frozen=True, slots=True)
class OpenRestriction:
    """Why a store opened read-only, in the vocabulary of contracts §13."""

    code: ErrorCode
    reason: str


class Store(Protocol):
    """The store seam (contracts §10.3), at the accessors this sprint provides."""

    read_only: bool
    restriction: OpenRestriction | None
    schema_version: int

    def scope(self) -> ScopeFacade: ...
    def close(self) -> None: ...


@dataclass(slots=True)
class SqliteStoreHandle:
    """A `Store` over SQLite. Returned by `open_store`; never constructed directly."""

    backend: SqliteStore
    read_only: bool
    restriction: OpenRestriction | None
    schema_version: int
    clock: Clock | None = None
    _scope: ScopeFacade | None = None

    def scope(self) -> ScopeFacade:
        if self._scope is None:
            self._scope = scope_facade(self.backend, clock=self.clock)
        return self._scope

    def transaction(self) -> object:
        """The shared transaction boundary (contracts §10.3)."""
        return self.backend.transaction()

    def close(self) -> None:
        self.backend.close()

    def __enter__(self) -> "SqliteStoreHandle":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _restriction_for(version: int) -> OpenRestriction | None:
    if version > MAX_SUPPORTED_SCHEMA_VERSION:
        return OpenRestriction(
            ErrorCode.SCHEMA_VERSION_TOO_NEW,
            f"the store is at schema version {version} and this binary supports "
            f"at most {MAX_SUPPORTED_SCHEMA_VERSION}",
        )
    if version < MIN_SUPPORTED_SCHEMA_VERSION:
        return OpenRestriction(
            ErrorCode.SCHEMA_MIGRATION_PENDING,
            f"the store is at schema version {version} and this binary needs at "
            f"least {MIN_SUPPORTED_SCHEMA_VERSION}; re-open with migrate=True",
        )
    return None


def open_store(
    path: Path | str,
    *,
    migrate: bool = False,
    read_only: bool = False,
    repo_root: Path | None = None,
    clock: Clock | None = None,
) -> SqliteStoreHandle:
    """Open a store, applying the version matrix in implementation spec §4.7.1.

    Args:
        path: The store file.
        migrate: Create or upgrade the store to `SCHEMA_VERSION` first.
        read_only: Open for reading only. Never creates and never migrates.
        repo_root: Where `schema/migrations/` lives. Defaults to the packaged
            location, and is injected by tests working against a scratch tree.
        clock: Injected clock; tests pass `ManualClock`.

    Returns:
        A handle carrying `read_only`, `restriction` and `schema_version`.

    Raises:
        AdoptError: ``SCHEMA_MIGRATION_PENDING`` when asked to open a store that
            does not exist without `migrate`. ``SCHEMA_MIGRATION_FAILED`` when a
            migration fails; the store is left exactly as it was found.
    """
    store_path = Path(path)

    if read_only and migrate:
        raise AdoptError(
            ErrorCode.STORE_READ_ONLY,
            message="a read-only open cannot migrate",
            hint="Migrating is a write. Choose one: open read-only to read a store as "
            "it is, or open for writing with migrate=True to bring it to the "
            "current schema version.",
        )

    if not store_path.exists() and not migrate:
        # Deliberately checked before opening: a read-write connect would create
        # an empty file, and an empty file that is not a store is worse than no
        # file at all -- the next open finds something and has to decide what.
        raise AdoptError(
            ErrorCode.SCHEMA_MIGRATION_PENDING,
            message=f"no store exists at {store_path}",
            hint="Pass migrate=True to create schema version 3.",
        )

    if read_only:
        backend = SqliteStore(store_path, read_only=True, clock=clock)
        version = backend.current_version()
        restriction = _restriction_for(version)
        if restriction is not None:
            backend.read_only_reason = restriction.reason
        else:
            backend.read_only_reason = "the caller opened it read-only"
        return SqliteStoreHandle(
            backend=backend,
            read_only=True,
            restriction=restriction,
            schema_version=version,
            clock=clock,
        )

    backend = SqliteStore(store_path, clock=clock)
    version = backend.current_version()

    if version > MAX_SUPPORTED_SCHEMA_VERSION:
        backend.close()
        return _reopen_read_only(store_path, version, clock)

    if migrate and (version < SCHEMA_VERSION or backend.is_empty()):
        root = repo_root if repo_root is not None else _packaged_repo_root()
        # `apply` appends the `schema_meta` row for every file it runs, so a
        # freshly created store already carries its open record.
        applied = apply_migrations(backend, root, _SQLITE_DIALECT, writer_identity())
        version = backend.current_version()
        if applied:
            return SqliteStoreHandle(
                backend=backend,
                read_only=False,
                restriction=None,
                schema_version=version,
                clock=clock,
            )

    if version < MIN_SUPPORTED_SCHEMA_VERSION:
        backend.close()
        return _reopen_read_only(store_path, version, clock)

    backend.append_schema_meta(SCHEMA_VERSION, EXPORT_VERSION, writer_identity())
    return SqliteStoreHandle(
        backend=backend, read_only=False, restriction=None, schema_version=version, clock=clock
    )


def _reopen_read_only(path: Path, version: int, clock: Clock | None) -> SqliteStoreHandle:
    restriction = _restriction_for(version)
    assert restriction is not None  # noqa: S101 -- only reached for an out-of-range version
    backend = SqliteStore(path, read_only=True, read_only_reason=restriction.reason, clock=clock)
    return SqliteStoreHandle(
        backend=backend,
        read_only=True,
        restriction=restriction,
        schema_version=version,
        clock=clock,
    )


def _packaged_repo_root() -> Path:
    """The repository root, found from this file rather than from the cwd.

    `packages/adopt-store/src/adopt_store/api.py` -> five parents up. A
    cwd-relative default would work in a checkout and fail from a wheel, which
    is the failure that only shows up after release.
    """
    return Path(__file__).resolve().parents[4]
