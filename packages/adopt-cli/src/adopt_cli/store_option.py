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
"""

from pathlib import Path

from adopt_cli.config import resolve_all
from adopt_obs import AdoptError, ErrorCode
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle

__all__ = ["configured_store_path", "open_configured_store"]

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
