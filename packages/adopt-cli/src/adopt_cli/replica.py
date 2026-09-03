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

**The verb policy, in one place.** `refuse_write_to_replica` is called from
`store_option.open_configured_store` whenever a store is opened writable, so the
policy below is a consequence of how each verb opens rather than a list any verb
consults. `tests/unit/test_replica_write_guard.py` parametrizes over exactly
this table.

* **Refused** (they write canon): `init`, `map`, `ingest`, `harvest`, `bind`,
  `gaps --ack/--resolve/--waive`, `review` (local mode; a configured remote
  routes and never opens), `answer` (local mode, same), `ask --escalate`'s
  capture, `draft`, `pack --draft-missing`, `probe add`, `probe run` (stored),
  `probe baseline --set`, `boundary --scope`, `refresh`, and every writing
  `handover` step.
* **Allowed, with the reason stated at the call site**: `ask` and `serve`
  answering -- they open writable to rebuild the retrieval index in the annex,
  which is not canon, and refusing them would make a replica unable to do the
  one thing it exists for; `coverage recompute --rebuild`, which writes only the
  cache `recompute_coverage()` derives.
* **Allowed, no write to guard**: `export`, `import --into` (a named target),
  `pull` (it replaces the replica by design), `ci-sense`, `identity`,
  `freshness`, `store info|doctor`, `probe run FILE`, `probe diff`, `detect`,
  `doctor`, `version`, `agent`, `envelope`, `policy`, and `handover status`.
  `store migrate` is allowed too: a pulled replica is already at the plane's
  version, and the schema is not canon.

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

__all__ = [
    "MARKER_SUFFIX",
    "ReplicaMarker",
    "marker_path",
    "read_marker",
    "refuse_write_to_replica",
    "write_marker",
]

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


def refuse_write_to_replica(
    store: Path,
    *,
    verb: str,
    code: ErrorCode = ErrorCode.STORE_TARGET_IS_REPLICA,
    message: str | None = None,
    hint: str | None = None,
) -> None:
    """Refuse a canon write against a store `adopt pull` maintains.

    **One guard, called from the one door every writing verb already goes
    through** (`store_option.open_configured_store(read_only=False)`), because
    the alternative was tried and failed: `refresh` and `handover` each carried
    their own copy and remembered, and `init`, `map`, `ingest`, `harvest`,
    `bind`, `gaps`, `review`, `answer`, `draft`, `pack --draft-missing`, `probe
    add/run/baseline` and `boundary --scope` did not. Eleven verbs could write
    canon into a file the next `adopt pull` replaces wholesale, and the work
    would be gone with no trace it had existed.

    R9 is the rule underneath: after activation the plane is the sole writer of
    an operated system's canon.

    Args:
        store: The **resolved** store path -- resolved by the caller, because
            this module must not import `store_option` (which imports this one).
        verb: What the operator ran, named in the message so the refusal says
            which command was refused rather than only which file.
        code: Overridden by the two verbs that already have their own registered
            code and their own hint (`refresh`, `handover`). Their recoveries
            differ from the general one, and an operator who has met one of them
            should not meet a different code for the same store tomorrow.
        message: Overrides the default, for those same two verbs.
        hint: Likewise.

    Raises:
        AdoptError: ``STORE_TARGET_IS_REPLICA`` (or `code`) when a replica
            marker sits beside `store`. Returns silently when there is none --
            absence of a marker is "not a replica", never "unknown"
            (`read_marker`).
    """
    marker = read_marker(store)
    if marker is None:
        return
    raise AdoptError(
        code,
        message=(
            message
            if message is not None
            else (
                f"{store} is a read replica of system {marker.system_id}, pulled from "
                f"{marker.plane_url}, and `adopt {verb}` writes canon. The next `adopt pull` "
                "replaces this file wholesale, so the write would be lost with no trace."
            )
        ),
        hint=(
            hint
            if hint is not None
            else (
                "R9 makes the plane the sole writer of an operated system's canon. To read "
                "the plane's current canon run `adopt pull`; to record a confirmation or an "
                "answer use the plane's own endpoints; to sense a change from CI run "
                "`adopt ci-sense`. To write canon rather than a replica, point --store at a "
                "field store."
            )
        ),
    )
