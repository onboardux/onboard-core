"""Every tunable for ``adopt-core``. Nothing else lives here.

This module is one half of a two-part contract with
``03-implementation-spec-build0.md`` §2.1-2.3. The specification table is the
source of truth for the *value*; this module is the source of truth for the
*name a caller imports*. ``scripts/constants_sync.py`` fails the build when the
two disagree, when a name appears in both this module and ``plane_const``, or
when a numeric literal in non-test source duplicates a value declared here.

Three rules govern edits:

1. A constant changes here and in `03` §2 in the same change. Never one alone.
2. Nothing is imported into this module except ``typing.Final``, which is what
   the specification requires the declarations to carry. The `const-leaf`
   import contract enforces the absence of every first-party import.
3. No function, no class, no I/O. A constant that needs logic to compute is not
   a constant.

The twelve values in §2.3 are **provisional**. They ship with a measurement
harness and are ratified at S9 exit against results from the reference runner
pinned in ``bench/RUNNER.md`` (PRD Q4). Provisional does not mean advisory: they
gate CI today at the values below.
"""

from typing import Final

# ---------------------------------------------------------------------------
# §2.1 -- schema, format, identity
# ---------------------------------------------------------------------------

#: Canonical schema version. Starts at 3; there is no version 1 or 2 in this
#: line. Consumed by the schema emitters, store open, and `schema_meta`.
SCHEMA_VERSION: Final[int] = 3

#: Export bundle format version. Equal to `SCHEMA_VERSION` at launch by
#: coincidence only, and versioned independently thereafter. Values 1 and 2 are
#: deliberately burned so no bundle from the withdrawn 0.1.x line can be
#: mistaken for this format.
EXPORT_VERSION: Final[int] = 3

#: Store-open version window. A store above the max opens read-only with
#: `SCHEMA_VERSION_TOO_NEW` and is never upgraded, downgraded or repaired.
MIN_SUPPORTED_SCHEMA_VERSION: Final[int] = 3
MAX_SUPPORTED_SCHEMA_VERSION: Final[int] = 3

#: Import version window. Outside it, import refuses and names the range.
MIN_SUPPORTED_EXPORT_VERSION: Final[int] = 3
MAX_SUPPORTED_EXPORT_VERSION: Final[int] = 3

#: Identity URI ceiling. An over-length URI is rejected, never truncated --
#: truncation would silently merge two distinct referents.
URI_MAX_BYTES: Final[int] = 1_024

#: The URI scheme label, and the whole of the URI format's version (CR-06,
#: owner-ratified 2026-08-03). It appears in every identity URI ever emitted, so
#: it is here rather than at the builder, the parser and the validator -- three
#: literals nobody could reliably grep for on the day `onboard-v2` arrives.
#:
#: `v1` versions **the URI grammar and identity interpretation only**. It is
#: independent of SCHEMA_VERSION, EXPORT_VERSION, the package version and the
#: release version; those four already move for unrelated reasons, and coupling
#: this to any of them would change the URI format every time a column was added.
#:
#: A future `onboard-v2` is permitted **only** for an incompatible grammar or
#: identity-semantics change. A compatible addition -- a new identity kind --
#: stays on `onboard-v1`, because bumping the label for an additive change
#: invalidates every URI already emitted and buys nothing.
URI_SCHEME: Final[str] = "onboard-v1"

#: Scope slug grammar. Slugs are what URIs are built from, so this pattern is
#: load-bearing for identity stability, not cosmetic.
SLUG_PATTERN: Final[str] = r"^[a-z0-9]([a-z0-9-]{0,46}[a-z0-9])?$"
SLUG_MIN_CHARS: Final[int] = 2
SLUG_MAX_CHARS: Final[int] = 48

#: Opaque idempotency key ceiling, shared by the agent seam and the workflow
#: client.
IDEMPOTENCY_KEY_MAX_CHARS: Final[int] = 128

#: Export NDJSON line ceiling, enforced by both the writer and the reader.
EXPORT_NDJSON_MAX_LINE_BYTES: Final[int] = 1_048_576

# ---------------------------------------------------------------------------
# §2.2 -- runtime behavior
# ---------------------------------------------------------------------------

#: SQLite busy timeout. One writer per store; readers use WAL.
STORE_BUSY_TIMEOUT_MS: Final[int] = 5_000

#: `store doctor` emits a finding above this item count.
STORE_MAX_ITEMS_WARN: Final[int] = 500_000

#: Archetype detection below this confidence is ambiguous. Ambiguity escalates
#: to a reasoning pass behind a default-off flag -- never to a guess.
DETECT_CONFIDENCE_MIN: Final[float] = 0.70

#: Bounds on the detection tree walk. Detection reads file trees it does not
#: control, so every dimension of the walk is bounded.
DETECT_MAX_SNIFF_BYTES: Final[int] = 8_192
DETECT_MAX_FILES: Final[int] = 200_000
DETECT_MAX_DEPTH: Final[int] = 24

#: Missed heartbeats beyond this multiple of a sensor's expected cadence
#: resolve STALE. Connector silence is never read as stability.
SENSOR_MISSED_CADENCE_MULTIPLIER: Final[int] = 3

#: How many identity ids the coverage-cache alarm carries. The event always
#: reports the full `disagreement_count`; the ids are a **sample**, because a
#: cold cache over a 50k-identity store disagrees on every row and an uncapped
#: field would put a megabyte of ULIDs on one log line -- which is how an alarm
#: takes down the sink that was meant to carry it. `store doctor` enumerates
#: every affected identity, so nothing is lost: the alarm says how bad, the
#: doctor says which.
COVERAGE_ALARM_SAMPLE_MAX: Final[int] = 20

#: Probe diff-ladder similarity threshold. Declared now; item 8 consumes it, so
#: that the value has one home from the day the first caller appears.
PROBE_DIFF_SIM_THRESHOLD: Final[float] = 0.92

#: `Budget` defaults for the agent seam.
AGENT_DEFAULT_MAX_USD: Final[float] = 0.50
AGENT_DEFAULT_MAX_WALL_SECONDS: Final[int] = 120

#: Deadline within which a budget-crossing run must abort and return partial
#: output with accurate cost.
AGENT_ABORT_GRACE_MS: Final[int] = 2_000

#: Output-schema retries the seam performs before returning the raw output.
AGENT_OUTPUT_SCHEMA_RETRIES: Final[int] = 1

#: Per-request adapter timeout. Adapters own transient retry within it; the
#: seam does not retry, because that would double-count cost.
AGENT_ADAPTER_TIMEOUT_S: Final[int] = 60

#: Workflow step retry policy, shared by both backends.
WORKFLOW_STEP_MAX_ATTEMPTS: Final[int] = 5
WORKFLOW_STEP_BACKOFF_BASE_MS: Final[int] = 250
WORKFLOW_STEP_BACKOFF_MAX_MS: Final[int] = 30_000

#: Retention window for workflow run records.
WORKFLOW_RUN_RETENTION_DAYS: Final[int] = 90

# ---------------------------------------------------------------------------
# §2.3 -- NFR gate constants (PROVISIONAL; ratified at S9 exit, PRD Q4)
# ---------------------------------------------------------------------------

#: N1 -- schema v3 creates cleanly in both dialects within this budget.
SCHEMA_CREATE_P95_SECONDS: Final[int] = 10

#: N3 -- store open p95 at <= 50k items, including the `schema_meta` read.
STORE_OPEN_P95_MS: Final[int] = 200

#: N4 -- export of 50k items.
EXPORT_P95_SECONDS: Final[int] = 30

#: N5 -- `build_uri` throughput floor.
URI_BUILD_MIN_PER_SECOND: Final[int] = 50_000

#: N6 -- coverage recompute p95 at 50k identities.
COVERAGE_RECOMPUTE_P95_SECONDS: Final[int] = 20

#: N7 -- freshness resolve p95 per item.
FRESHNESS_RESOLVE_P95_MS: Final[int] = 25

#: CLI cold start ceiling.
CLI_COLD_START_MS: Final[int] = 400

#: Release gate -- single-file binary size ceiling per platform.
BINARY_MAX_MB: Final[int] = 120

#: N12 -- adapter conformance matrix runtime ceiling.
CONFORMANCE_CI_MAX_MINUTES: Final[int] = 8

#: N13 -- the suite runtime ratchet. Adding runtime past these requires
#: removing equivalent runtime in the same change.
CI_UNIT_MAX_MINUTES: Final[int] = 2
CI_PR_MAX_MINUTES: Final[int] = 10

#: A floor **alarm** on core packages, never a target. Banned as a target
#: alongside test count: a rise is not a goal, a drop is a signal.
COVERAGE_FLOOR_CORE: Final[float] = 0.80
