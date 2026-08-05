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

from pathlib import Path

from adopt_cli.config import resolve_all
from adopt_obs import AdoptError, ErrorCode
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle, writer_identity

__all__ = [
    "configured_store_path",
    "open_configured_store",
    "open_named_store",
    "open_or_create_store",
    "writer_identity",
]

_STORE_KEY = "ADOPT_STORE_PATH"


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


def open_named_store(path: Path, *, migrate: bool = False) -> SqliteStoreHandle:
    """Open a store the caller named outright, bypassing the resolution order.

    For `adopt import --into`, where the path is an argument rather than
    configuration: an import target resolved from `ADOPT_STORE_PATH` would
    restore a bundle over whichever store the shell happened to be pointing at.
    """
    return open_store(path, migrate=migrate)
