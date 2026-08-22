"""Opening the configured store, in one place.

Every command that touches a store resolves its path the same way -- flag, then
`ADOPT_STORE_PATH`, then the project file, then the user file, then the default
(`adopt_cli.config`). A second resolution order in a second command is a second
answer to "which store am I looking at", and an operator who gets that wrong
reads a healthy store while a different one is on fire.

**Read-only unless the command actually writes.** `adopt freshness resolve`
writes nothing by contract and `adopt coverage recompute` writes only under
`--rebuild`, so the default open is read-only and a write attempt raises
`STORE_READ_ONLY` rather than succeeding somewhere unexpected.

**This is the only `adopt_cli` module permitted to reach `adopt_store`** (CR-36),
so every other way a command needs a store arrives here rather than growing a
second exemption -- including `adopt import`'s explicitly-named target store and
the `written_by` provenance string an export bundle records.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from adopt_cli.config import resolve_all
from adopt_obs import AdoptError, Clock, ErrorCode
from adopt_store import open_store
from adopt_store.annex import SqliteAnnexRecords, annex_path, open_annex
from adopt_store.annex.questions import SqliteQuestionLog, open_question_log
from adopt_store.annex.search import SqliteSearchRecords, open_search
from adopt_store.api import SqliteStoreHandle, writer_identity

__all__ = [
    "SqliteStoreHandle",
    "configured_annex",
    "configured_question_log",
    "configured_search",
    "configured_store_path",
    "open_configured_store",
    "open_for_migration",
    "open_named_store",
    "open_or_create_store",
    "writer_identity",
]

_STORE_KEY = "ADOPT_STORE_PATH"
_RUNTIME_KEY = "ADOPT_RUNTIME_PATH"


def configured_store_path(override: Path | None = None) -> Path:
    """The store path a command should use, with the registry's resolution order.

    Raises:
        AdoptError: ``ADOPT_CONFIG_UNRESOLVED`` when no layer supplies a value.
            The key carries a default, so this is unreachable in a normal tree
            and is raised rather than defaulted anyway: silently inventing a
            store path is how a command creates an empty database beside the
            real one.
    """
    if override is not None:
        return override
    for resolution in resolve_all():
        if resolution.key == _STORE_KEY and resolution.value:
            return Path(resolution.value)
    raise AdoptError(
        ErrorCode.ADOPT_CONFIG_UNRESOLVED,
        message=f"{_STORE_KEY} has no value in any configuration source",
        hint=f"Pass --store, or set {_STORE_KEY}.",
    )


def open_configured_store(
    override: Path | None = None, *, read_only: bool = True
) -> SqliteStoreHandle:
    """Open the configured store. The caller closes it."""
    return open_store(configured_store_path(override), read_only=read_only)


def open_or_create_store(override: Path | None = None) -> SqliteStoreHandle:
    """The configured store, created at the current schema version if absent.

    `adopt init`'s door, and the only one that creates. Every other command opens
    what is already there -- which is why the parent directory is created here
    and nowhere else: `.adopt/` not existing is the normal state before `init`
    runs, and any later command finding it absent is reporting a real problem
    rather than one it should quietly fix.
    """
    target = configured_store_path(override)
    target.parent.mkdir(parents=True, exist_ok=True)
    return open_store(target, migrate=True)


def open_for_migration(override: Path | None = None) -> SqliteStoreHandle:
    """The configured store, opened writable with pending migrations applied.

    `adopt store migrate`'s door. Forward-only, always: implementation spec §7.4
    states the schema has no rollback and recovery is older code against a newer
    store.
    """
    return open_store(configured_store_path(override), migrate=True)


@contextmanager
def configured_annex() -> Iterator[SqliteAnnexRecords]:
    """The runtime annex at `ADOPT_RUNTIME_PATH`, opened for one call.

    **Here rather than in the command that needs it**, for the reason this whole
    module exists (CR-36): `no-raw-sqlite` follows indirect chains into
    `adopt_cli` and exempts only this module by name, so a command reaching
    `adopt_store.annex` directly breaks the contract. The seam itself never sees a
    driver either -- it is handed an `AnnexRecords`, which this satisfies
    structurally.

    The annex is a **second store, outside `schema_version`** (contracts §12,
    CR-08), which is why it does not go through `configured_store_path`: pointing
    both at one resolution order is the coupling the ratification exists to
    prevent.
    """
    for resolution in resolve_all():
        if resolution.key == _RUNTIME_KEY and resolution.value:
            with open_annex(Path(resolution.value)) as records:
                yield records
            return
    raise AdoptError(
        ErrorCode.ADOPT_CONFIG_UNRESOLVED,
        message=f"{_RUNTIME_KEY} has no value in any configuration source",
        hint=f"Set {_RUNTIME_KEY}. It carries agent-run idempotency and in-client "
        f"audit, and is never exported.",
    )


@contextmanager
def configured_search(
    handle: SqliteStoreHandle, *, clock: Clock | None = None
) -> Iterator[SqliteSearchRecords]:
    """The retrieval index beside `handle`'s store, opened for one command.

    **Here rather than in `commands/ask.py`**, for the reason this module exists
    (CR-36, extended by CR-47 to the annex): `no-raw-sqlite` follows indirect
    chains into `adopt_cli` and exempts only this module by name, so a command
    importing `adopt_store.annex.search` reaches `sqlite3` through it and breaks
    the contract. `adopt_ask` is handed a `SearchRecords`, which this satisfies
    structurally, and never learns which engine answered.

    Unlike `configured_annex` this takes no path of its own: the index is
    resolved from the store's path, because an index beside a *different* store
    answers questions about knowledge that store never held.
    """
    with open_search(handle.backend, clock=clock) as records:
        yield records


@contextmanager
def configured_question_log(handle: SqliteStoreHandle) -> Iterator[SqliteQuestionLog]:
    """The passive question log in the annex beside `handle`'s store.

    Resolved from the store's path rather than from `ADOPT_RUNTIME_PATH`, for
    `configured_search`'s reason: the log records what was asked *of this
    store*, and a log beside a different one answers for questions that store
    never received. `configured_annex` keeps the path key because agent-run
    idempotency is genuinely store-independent.
    """
    with open_question_log(annex_path(handle.backend.path)) as log:
        yield log


def open_named_store(path: Path, *, migrate: bool = False) -> SqliteStoreHandle:
    """Open a store the caller named outright, bypassing the resolution order.

    For `adopt import --into`, where the path is an argument rather than
    configuration: an import target resolved from `ADOPT_STORE_PATH` would
    restore a bundle over whichever store the shell happened to be pointing at.
    """
    return open_store(path, migrate=migrate)
