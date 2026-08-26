"""File content hashing for the RENDER-ONLY class -- pure, and deliberately weak.

**The weakest signal in Build 6, and confined by where it lives.** A file hash
cannot say what changed or whether it mattered; it says only that bytes differ.
So it feeds exactly one class -- the informational one -- and nothing here can
stale a piece of knowledge. The attribute digest (`adopt_map.digest`) remains
the sole authority on whether a referent changed (H5); a file hash that could
reach freshness would reintroduce precisely the false staleness H5 deletes.

**Here rather than beside the table that stores it.** The persistence half is
`adopt_store.annex.filestate`, and these three functions are pure: a caller
comparing two snapshots needs no database, and `no-raw-sqlite` follows indirect
chains into `adopt_cli`, so a CLI module reaching for `changed_paths` through
the store package would break the contract to call a function that does not
touch a store. Splitting them puts each half where its callers already are.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = ["FileState", "changed_paths", "hash_file"]


@dataclass(frozen=True, slots=True)
class FileState:
    """One walked file's content hash at the end of one refresh."""

    path: str
    sha256: str


def hash_file(path: Path) -> str | None:
    """`sha256` of a file's **bytes**, or `None` when it cannot be read.

    Bytes rather than decoded text, deliberately: a CRLF checkout and an LF
    checkout of one commit must not look like an edit to every file in the tree.
    But neither may this claim to be a semantic comparison -- it is not one, and
    the class it feeds is the informational one for exactly that reason.

    Unreadable returns `None` rather than raising: a file that vanished between
    the walk and the hash is a race, not a failure of the refresh, and the run
    that found real changes should not die of it.

    `hashlib.file_digest` does the buffering, so this module owns no block size:
    a map walks whole repositories, and the one number worth getting right here
    -- how much of a file is held in memory at once -- is one the standard
    library already maintains.
    """
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return None


def changed_paths(previous: Mapping[str, str] | None, current: Mapping[str, str]) -> frozenset[str]:
    """Paths whose bytes differ from the snapshot. Empty when there is none.

    **A file absent from the previous snapshot is not "changed".** It is new,
    and a new file's identities are already reported as `UNBOUND_NEW` -- adding
    the file would make every addition produce two entries about one event, one
    of them informational noise a reviewer cannot act on.

    Deletions are likewise absent: a deleted file's identities surface as
    `BINDING_DEAD`, which is the actionable statement about it.
    """
    if previous is None:
        return frozenset()
    return frozenset(
        path for path, digest in current.items() if path in previous and previous[path] != digest
    )
