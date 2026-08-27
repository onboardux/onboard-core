"""The replica marker: what a store is a replica *of*, beside the store itself.

v6.1 §6 Build 7 F4 demotes the field store to a read replica once a system is
operated, and R9 makes the plane the sole writer. That is a fact about **this
store file**, and nothing in the canonical schema records it -- deliberately, and
the sprint plan's D-4 keeps it that way: the schema budget for Builds 1-10 is
spent, and a bundle carrying "which plane did this come from" would be a bundle
whose contents depend on how it was fetched.

So the marker is a **sidecar**: `<store>.replica.json`, written beside the store
by `adopt pull` and read by `adopt pull` and `adopt doctor`. Three properties
follow from that placement and each is load-bearing:

* **It travels with the store path.** The check `adopt pull` needs is "is *this
  file* a replica", not "has this machine ever pulled" -- so a marker in the
  runtime annex would say yes while `ADOPT_STORE_PATH` pointed at a colleague's
  field store full of unexported canon, and `pull` would replace it.
* **It is outside the schema and outside the export.** It is operational state
  about a copy, exactly as `schema_meta` is state about a file rather than canon
  in it. Nothing here reaches a bundle.
* **A human can read it.** `cat store.db.replica.json` answers "where did this
  come from and when", which is the first question anybody asks of a replica
  that is answering oddly.

**Absence means "not a replica", never "unknown".** `adopt pull` refuses a store
with no marker unless `--init-replica`, because the two states it cannot tell
apart -- a fresh empty file and a field store holding a week of unexported work
-- have wildly different costs and only one of them is recoverable.
"""

import datetime as _dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from adopt_obs import AdoptError, ErrorCode, format_timestamp

__all__ = ["MARKER_SUFFIX", "ReplicaMarker", "marker_path", "read_marker", "write_marker"]

#: Appended to the store's own filename rather than replacing its suffix, so
#: `store.db` and `store.db.replica.json` sort together and neither can be
#: mistaken for the other by a glob that matches `*.db`.
MARKER_SUFFIX: Final[str] = ".replica.json"

_ENCODING: Final[str] = "utf-8"


@dataclass(frozen=True, slots=True)
class ReplicaMarker:
    """Where this replica came from, and when.

    `bundle_sha256` is the digest of the archive the plane served, which is also
    what the plane's own `export_served` audit row recorded. That is the whole
    point of keeping it: an operator with a puzzling replica and an operator
    reading the plane's audit trail can establish they are talking about the
    same bundle without either of them guessing.
    """

    plane_url: str
    system_id: str
    pulled_at: _dt.datetime
    bundle_sha256: str

    def payload(self) -> dict[str, Any]:
        return {
            "plane_url": self.plane_url,
            "system_id": self.system_id,
            "pulled_at": format_timestamp(self.pulled_at),
            "bundle_sha256": self.bundle_sha256,
        }


def _parsed(value: str) -> _dt.datetime:
    """The inverse of `format_timestamp`, spelled the way the annex spells it.

    `fromisoformat` accepts a literal `Z` only from 3.11 onwards and this tree
    already carries the `+00:00` substitution in `adopt_store.annex.questions`;
    duplicating three characters is cheaper than a shared helper in `adopt_obs`
    that would exist for two callers and have to be registered as surface.
    """
    return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def marker_path(store: Path) -> Path:
    """`<store>.replica.json`, beside the store it describes."""
    return store.with_name(store.name + MARKER_SUFFIX)


def read_marker(store: Path) -> ReplicaMarker | None:
    """The marker beside `store`, or `None` when there is none.

    Raises:
        AdoptError: ``PULL_TARGET_NOT_REPLICA`` when a marker exists but cannot
            be read as one. **Refusing rather than treating it as absent**: an
            unreadable marker is a store somebody has told us something about,
            and the failure mode of guessing is replacing a store that was
            trying to say "do not". A hand-edited or truncated file is fixed by
            deleting it and passing `--init-replica`, which the hint says.
    """
    path = marker_path(store)
    if not path.exists():
        return None
    try:
        decoded = json.loads(path.read_text(encoding=_ENCODING))
        return ReplicaMarker(
            plane_url=str(decoded["plane_url"]),
            system_id=str(decoded["system_id"]),
            pulled_at=_parsed(str(decoded["pulled_at"])),
            bundle_sha256=str(decoded["bundle_sha256"]),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as broken:
        raise AdoptError(
            ErrorCode.PULL_TARGET_NOT_REPLICA,
            message=f"{path} exists but is not a readable replica marker: {broken}",
            hint="A marker that cannot be read is treated as a refusal rather than as "
            "an absence, because the store beside it may hold canon nobody has "
            "exported. Delete the marker and re-run with `--init-replica` if you are "
            "certain the store is expendable.",
        ) from broken


def write_marker(store: Path, marker: ReplicaMarker, *, target: Path | None = None) -> Path:
    """Write `marker` for `store`, returning the path written.

    Args:
        store: The store the marker describes.
        marker: What to record.
        target: Where to write, overriding the derived path. `adopt pull` uses
            it to stage the new marker under a temporary name before the swap,
            so the file that finally lands is one `os.replace` rather than a
            partial write somebody could read halfway through.

    Rendered with sorted keys and a trailing newline, on the same argument
    `adopt_export.canonical_json` makes: a file two tools may write is a file
    whose bytes should not depend on which one did.
    """
    path = target if target is not None else marker_path(store)
    body = json.dumps(marker.payload(), sort_keys=True, indent=2) + "\n"
    path.write_text(body, encoding=_ENCODING)
    return path
