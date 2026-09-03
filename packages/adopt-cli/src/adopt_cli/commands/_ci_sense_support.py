"""What `adopt ci-sense` observes, and the shape it posts.

**Observations travel; the plane classifies** (sprint plan D8-2). This module
extracts what the repository now contains and renders it as a payload. It
deliberately does not compare anything, does not decide what changed, and writes
nothing at all -- not to a store, not to the tree. The plane holds the canon
this observation is *about*, and only the plane can compare against the state it
is about to mutate (R9: after activation the plane is the sole writer).

The rejected alternative is worth stating because it looks cheaper: have
ci-sense diff locally against the replica and post change events. A replica is
only as fresh as its last `adopt pull`, so that classifies against state that
may already be wrong, and it puts canon-shaped conclusions in the mouth of a
caller that is not the writer. What travels here is evidence, not verdicts.

**The payload is versioned and first-party** (R4). Both ends are our code -- the
CLI emits it, `plane-freshness` reads it -- so there is no interchange contract
here and none should be published; `payload_version` exists so the two can
disagree loudly rather than silently. `tests/fixtures/ci_sense_payload.json` is
the committed rendering both repositories pin, because `adopt-cli` is
deliberately not a plane dependency and the two would otherwise agree only by
accident (Build 7 T4's precedent).

**What is deliberately absent: file contents, and any client text.** The payload
carries per-file sha256 digests and per-identity *attribute* digests, never a
line of the repository. That is what lets a customer run this step in CI without
their source leaving their network, and it is why `raw` on the change event the
plane writes is a summary rather than a diff.
"""

import datetime as _dt
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from adopt_map import MapReport
from adopt_map.filestate import FileState
from adopt_map.observe import PackSightings

from adopt_cli.remote import Remote, post_json
from adopt_const import CI_SENSE_TIMEOUT_SECONDS
from adopt_obs import format_timestamp, get_logger, new_id
from adopt_scope import Scope

__all__ = [
    "PAYLOAD_VERSION",
    "SENSE_PATH",
    "SenseOutcome",
    "build_payload",
    "post_payload",
    "sense_run_id",
]

_log = get_logger(__name__)

#: Bumped when a field the plane needs changes shape. The plane refuses a
#: version it does not know (`PLANE_SENSE_PAYLOAD_INVALID`) rather than reading
#: a field that has moved: a sensing step that silently posts something the
#: cascade misreads produces confident classifications of the wrong thing.
PAYLOAD_VERSION: Final[int] = 1

#: The sensor kind this verb registers. `ci` is one of the manifest's declared
#: `sensor_kind` values, and no enum changes for Build 8.
SENSOR_KIND: Final[str] = "ci"

SENSE_PATH: Final[str] = "/v1/systems/{system_id}/sense"

#: What the `probes` section says when the half was not run at all. A literal
#: here rather than an omitted key, for the reason `build_payload` gives.
_NO_PROBE_SECTION: Final[dict[str, Any]] = {
    "ran": False,
    "reason": "the probe half did not run",
    "results": [],
    "skipped": [],
    "heartbeats": [],
}


@dataclass(frozen=True, slots=True)
class SenseOutcome:
    """What the plane said it did with one payload."""

    batch_key: str | None
    counts: Mapping[str, int]
    accepted: bool
    replayed: bool

    @property
    def findings(self) -> int:
        """Classifications the plane recorded for this run."""
        return sum(self.counts.values())


def sense_run_id() -> str:
    """The run's own id, which is also its idempotency key.

    A ULID rather than a hash of the payload: two genuinely identical
    observations posted an hour apart are two runs and should both be recorded
    as liveness, while a *retry* of one run must not double-write. The CI step
    generates this once and reuses it across retries, which is exactly the
    distinction a content hash would lose.
    """
    return new_id("run")


def build_payload(
    *,
    run_id: str,
    scope: Scope,
    sightings: Sequence[PackSightings],
    report: MapReport,
    file_state: Sequence[FileState],
    cadence_seconds: int,
    connector_version: str,
    observed_at: _dt.datetime,
    probes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Render one observation of the tree.

    The URI is **not** built here. Key segments, kind and namespace travel
    instead, and the plane builds canonical URIs from the scope row it holds --
    so a ci-sense running against a stale replica whose scope slugs have since
    been corrected cannot post URIs that miss canon by a slug. The scope path is
    carried so the plane can refuse a payload that describes a different system
    from the one the token and path name.
    """
    observations = [
        {
            "kind": sighting.observation.kind,
            "namespace": sighting.observation.namespace,
            "key": list(sighting.observation.key),
            "digest": sighting.digest,
            "extractor": sighting.extractor,
            "extractor_version": sighting.extractor_version,
            "source_ref": sighting.observation.span.render(),
            "source_path": sighting.observation.span.path,
        }
        for found in sightings
        for sighting in found.sightings
    ]
    extractors = [
        {
            "extractor": outcome.extractor,
            "version": outcome.version,
            "pack": outcome.pack,
            "status": outcome.status,
            "observations": outcome.observations,
            "detail": outcome.detail,
        }
        for found in sightings
        for outcome in found.outcomes
    ]
    return {
        "payload_version": PAYLOAD_VERSION,
        "run_id": run_id,
        "observed_at": format_timestamp(observed_at),
        "scope": scope.path(),
        "sensor": {"kind": SENSOR_KIND, "cadence_seconds": cadence_seconds},
        "connector": {"mode": "outbound_relay", "version": connector_version},
        "packs": list(report.packs),
        "extractors": extractors,
        "tree": {
            "files_walked": report.files_walked,
            "files_oversized": sorted(report.files_oversized),
        },
        # Sorted so the payload is a function of what was found rather than of
        # dictionary iteration order -- the framing fixture is compared byte for
        # byte, and a payload that reorders per run cannot be pinned.
        "observed": sorted(
            observations, key=lambda row: (row["kind"], row["namespace"] or "", tuple(row["key"]))
        ),
        "file_state": {state.path: state.sha256 for state in sorted(file_state, key=_by_path)},
        # **Present on every payload, including one whose probe half did not
        # run.** An absent section and a section saying `ran: false` are
        # different claims -- "this producer has no probe half" versus "it has
        # one and here is why it looked at nothing" -- and only the second is
        # true of any binary this build ships. The plane reads an absent section
        # as the first, which is what keeps an older `adopt ci-sense` readable.
        "probes": dict(probes) if probes is not None else _NO_PROBE_SECTION,
    }


def _by_path(state: FileState) -> str:
    return state.path


def render_payload(payload: Mapping[str, Any]) -> str:
    """The exact bytes posted, and the exact bytes the fixture pins.

    Sorted keys, no spaces -- the rendering rule the exporter already uses, for
    its reason: the fixture is compared byte for byte across two repositories,
    and a formatting difference would read as a payload change.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def post_payload(remote: Remote, payload: Mapping[str, Any]) -> SenseOutcome:
    """POST the payload and read back what the plane concluded.

    Raises:
        AdoptError: whatever the plane refused with, rebuilt by `post_json` --
            `PLANE_CONNECTOR_REVOKED` and `PLANE_SENSE_PAYLOAD_INVALID` reach
            the operator as themselves, which is the point of the pair: a
            revoked relay and a malformed payload have opposite fixes.
    """
    body = post_json(
        remote,
        SENSE_PATH.format(system_id=remote.system_id),
        dict(payload),
        timeout=CI_SENSE_TIMEOUT_SECONDS,
    )
    counts = body.get("counts")
    outcome = SenseOutcome(
        batch_key=_string_or_none(body.get("batch_key")),
        counts={str(k): int(v) for k, v in counts.items()} if isinstance(counts, dict) else {},
        accepted=bool(body.get("accepted", True)),
        replayed=bool(body.get("replayed", False)),
    )
    _log.info(
        "ci_sense.posted",
        batch_key=outcome.batch_key,
        findings=outcome.findings,
        replayed=outcome.replayed,
    )
    return outcome


def _string_or_none(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None
