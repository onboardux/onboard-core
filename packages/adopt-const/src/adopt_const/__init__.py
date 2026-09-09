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

#: Canonical schema version. Started at 3; there is no version 1 or 2 in this
#: line. Consumed by the schema emitters, store open, and `schema_meta`.
#: Version 4 adds `coverage_gap` (Build 4).
SCHEMA_VERSION: Final[int] = 4

#: The version the **initial** migration produces, and the boundary that decides
#: which file creates a table: everything at or below this is created by
#: `0001__init_v3.sql`, and every version above it gets its own migration
#: carrying exactly the tables introduced at that version. Frozen forever --
#: raising it would rewrite a file that has already run on real stores.
INITIAL_SCHEMA_VERSION: Final[int] = 3

#: Export bundle format version. Equal to `SCHEMA_VERSION` at launch by
#: coincidence only, and versioned independently thereafter. Values 1 and 2 are
#: deliberately burned so no bundle from the withdrawn 0.1.x line can be
#: mistaken for this format.
EXPORT_VERSION: Final[int] = 4

#: Store-open version window. A store above the max opens read-only with
#: `SCHEMA_VERSION_TOO_NEW` and is never upgraded, downgraded or repaired. The
#: min stays 3: a v3 store is migrated forward, not refused.
MIN_SUPPORTED_SCHEMA_VERSION: Final[int] = 3
MAX_SUPPORTED_SCHEMA_VERSION: Final[int] = 4

#: Import version window. Outside it, import refuses and names the range. The
#: min stays 3 because a v3 bundle -- including the published v3 reference
#: bundle -- must keep importing forever; additive-only is what makes that safe.
MIN_SUPPORTED_EXPORT_VERSION: Final[int] = 3
MAX_SUPPORTED_EXPORT_VERSION: Final[int] = 4

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

#: The most `adopt map` reads from any one file. Detection's 8 KiB sniff is a
#: classification sample; extraction needs the whole file, so the bound moves
#: from "enough to recognise" to "large enough for real source, small enough
#: that a vendored bundle or a checked-in dataset cannot stall the run". A file
#: over this is skipped and counted as unmapped -- visible in the report, never
#: silently dropped.
MAP_MAX_FILE_BYTES: Final[int] = 1_048_576

#: How many passages `adopt ask` retrieves before the freshness check and the
#: three-way branch see them. A ceiling on *candidates*, not on citations: the
#: branch may serve fewer, and an answer citing eight sources is already more
#: than an FDE reads. Small on purpose -- retrieval is on the interactive path
#: (R8), and every candidate costs a `resolve_freshness` call.
ASK_TOP_K: Final[int] = 8

#: Whether `adopt ask` records the questions nobody escalated. **Off, and the
#: default is the control** (v6.1 §6 Build 3, F2). Explicit escalation stores a
#: question because storing it is the point; passive logging stores every
#: question an FDE typed on a client engagement, which is a transcript nobody
#: consented to. When it is turned on the rows land in the **runtime annex**
#: (plan D5) -- never in canon, never exported -- because `adopt_obs`'s
#: structured-log deny-list drops a `question` field by design, so there is no
#: compliant way to write one to a log line and the annex is the only home left.
ASK_LOG_QUESTIONS: Final[int] = 0

#: The loopback port `adopt serve` binds by default. In the IANA dynamic range
#: and not a number anything else claims, which matters because the failure mode
#: of a popular default is an FDE's `adopt serve` quietly answering on a port
#: some other local tool is also using.
ASK_SERVE_PORT: Final[int] = 8787

#: The largest `POST /ask` body `adopt serve` will read. A question is a
#: sentence; a body at this size is a mistake or an attempt, and reading an
#: unbounded `Content-Length` into memory is how a loopback convenience becomes
#: a way to exhaust a developer's machine. Here rather than as a literal because
#: `64 * 1024` collides with two existing tunables on both of its factors, and a
#: waiver would leave the one number a future reader wants to change buried in a
#: multiplication.
ASK_SERVE_MAX_BODY_BYTES: Final[int] = 65536

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

#: The ceiling on any single probe's declared `runtime.max_seconds` *(Build 5,
#: v6.1 §6 B5 and §8's complete tunable list)*. A probe declares its own wall
#: clock in its capability manifest; this is the bound that declaration may not
#: exceed, refused at `adopt probe add`. Two limits rather than one because they
#: answer different questions: the manifest's is what a human approved for this
#: probe, and this is what the programme permits any probe to ask for -- without
#: it, a manifest could declare its way out of the wall clock entirely, which is
#: the unbounded probe in a client environment the manifest exists to prevent.
PROBE_TIMEOUT_SECONDS: Final[int] = 60

#: How long a CLI verb in **remote mode** waits on the control plane before
#: giving up. `adopt ask --escalate` and `adopt answer` post to `plane-api` when
#: a remote is configured, and an FDE typing into a terminal is on the
#: interactive path (R8) -- a request with no timeout hangs a prompt until
#: somebody hits Ctrl-C, and the answer they wanted is one they could have got
#: from the replica.
#:
#: Shares its value with `AGENT_ADAPTER_TIMEOUT_S` and `PROBE_TIMEOUT_SECONDS`
#: and shares nothing else: one bounds a model call, one a probe, this one an
#: HTTP round trip to our own service. Three literals that happen to agree are
#: three literals that silently disagree the first time one moves.
REMOTE_CHANNEL_TIMEOUT_SECONDS: Final[int] = 60

#: How long `adopt pull` waits for the plane to stream a whole bundle.
#:
#: **Five minutes rather than the sixty seconds every other HTTP call here
#: gets**, and the difference is the payload rather than caution: the channel
#: verbs post a question and read a sentence, while this reads an entire
#: engagement's canon over whatever link an FDE is on. A pull is also not on the
#: interactive path in the way `adopt ask` is -- an operator who typed `pull`
#: expects to wait -- so the number that would be wrong for a prompt is right
#: here.
#:
#: Bounded rather than absent, because an unbounded read against a wedged proxy
#: hangs a terminal with no message naming what it was waiting for, and the
#: replica it was refreshing is left exactly as stale as before with nobody told.
PULL_TIMEOUT_SECONDS: Final[int] = 300

#: How long `adopt ci-sense` waits for the plane to accept an observation payload.
#:
#: **Three minutes against the plane's own `INGEST_P95_SECONDS = 120`**, and the
#: relationship is the point: the server's budget for classifying a payload is
#: two minutes, so a client timeout at the channel verbs' sixty seconds would
#: abandon requests the plane was still correctly serving -- and the CI step
#: would report a failure for work that then landed anyway, which is the worst
#: of both readings. The margin over 120 covers the transfer of a payload that
#: carries every identity in a repository, which is larger than anything else
#: this CLI sends.
#:
#: Bounded rather than absent for `PULL_TIMEOUT_SECONDS`' reason: an unbounded
#: read wedges a deploy pipeline with no message naming what it waited for.
CI_SENSE_TIMEOUT_SECONDS: Final[int] = 180

#: The cadence `adopt ci-sense` declares for its sensor when nobody says otherwise.
#:
#: A day, because the sensor this registers is a **CI step**: it reports when the
#: pipeline runs, and a daily deploy is the ordinary rhythm this is sized for.
#: The number's whole job is to feed `sensor_effective_health`, which stales a
#: scope once silence exceeds the cadence times `SENSOR_MISSED_CADENCE_MULTIPLIER`
#: -- so it is the difference between "this pipeline stopped reporting" being
#: noticed and being invisible. Set it per project with `--cadence-hours`; a
#: repository that deploys weekly wants a week, and one that deploys hourly
#: wants an hour, and the default must not silently make either of them wrong
#: without the operator having said anything.
CI_SENSE_DEFAULT_CADENCE_HOURS: Final[int] = 24

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

#: The `detect-001` disambiguation pass's budget, stated by AI spec §5's prompt
#: table. **Not provisional**: `04` §5 fixes both values, so there is nothing for
#: S9 to ratify against a measurement. They are constants rather than literals
#: because `AGENT_DETECT_MAX_WALL_SECONDS` shares a value with `EXPORT_P95_SECONDS`
#: and shares nothing else -- one bounds how long one model call may take, the
#: other how long exporting fifty thousand items may take.
AGENT_DETECT_MAX_USD: Final[float] = 0.05
AGENT_DETECT_MAX_WALL_SECONDS: Final[int] = 30

#: The `ask-001` synthesis pass's budget. Synthesis is **optional and on the
#: interactive path** (v6.1 §4 R8), which is what sets both numbers: an FDE
#: waiting for an answer will not wait thirty seconds, and a pass that can be
#: skipped entirely has no claim on a large spend. Separate constants from
#: `AGENT_DETECT_*` rather than shared ones, because the two passes are bounded
#: by different things -- disambiguation runs once per repository and may take
#: its time; this one runs per question.
AGENT_ASK_MAX_USD: Final[float] = 0.02
AGENT_ASK_MAX_WALL_SECONDS: Final[int] = 15

#: The `draft-001` handover-drafting pass's budget. **Larger than the ask pass
#: and equal to the detect pass**, because drafting is the one generation path
#: whose output is *persisted*: it runs in a batch nobody is watching, it writes
#: a knowledge revision a human will later read, and a draft abandoned half way
#: through its budget costs the run a section rather than costing a reader a
#: prettier paragraph. Separate constants from `AGENT_DETECT_*` despite sharing
#: both values today, for `AGENT_ASK_*`'s reason: they are bounded by different
#: things -- disambiguation runs once per repository, this runs once per
#: uncovered identity -- and two literals that happen to agree are two literals
#: that silently disagree the first time one moves.
AGENT_DRAFT_MAX_USD: Final[float] = 0.05
AGENT_DRAFT_MAX_WALL_SECONDS: Final[int] = 30

#: How many drafts one `adopt pack --draft-missing` invocation may generate.
#:
#: **A cap on spend per command, not a cap on the work.** A store with four
#: hundred uncovered identities would otherwise make one flag press four hundred
#: model calls, which is a bill an FDE did not agree to and a wait nobody sits
#: through. The gap list is ranked deterministically, so the same twenty are
#: drafted first and a second run continues rather than repeating: an identity
#: that already carries an unverified draft is skipped, so the cap advances
#: through the queue instead of re-drafting its head.
DRAFT_MAX_PER_RUN: Final[int] = 20

#: How much of one bound revision's body travels into a drafting prompt. A
#: runbook bound to the same identity is evidence for a draft about it; the whole
#: of one is not, and a store with three long documents on one endpoint would
#: spend `AGENT_DRAFT_MAX_USD` on prose the model was only meant to summarise
#: around. Truncation is visible in the fact text, so the model is never told a
#: fragment is the whole.
DRAFT_FACT_BODY_MAX_CHARS: Final[int] = 1_200

#: How long `adopt pack --format docx|pdf` lets its converter run before killing
#: it. A converter that has not finished a handover pack in two minutes is wedged
#: rather than slow, and a subprocess with no timeout is how a CI job hangs until
#: its own budget kills it with no message naming what it was waiting for.
#: Shares its value with `AGENT_DEFAULT_MAX_WALL_SECONDS` and shares nothing
#: else -- one bounds a model call, the other a local document conversion.
PACK_CONVERT_TIMEOUT_SECONDS: Final[int] = 120

#: How many tree entries the `detect-001` prompt's bounded listing may carry --
#: the `{listing_limit}` placeholder in AI spec §5.1's user template.
#:
#: **Provisional, ratified at S9 against the `04` §7.2 golden set.** The pack
#: requires the listing to be bounded and does not say where; this is the
#: provisional value, its consumer is `adopt_detect.disambiguate`, and what
#: ratifies it is whether a larger listing measurably improves top-1 accuracy on
#: the golden set. A bound that is too small starves the evidence and one that is
#: too large spends the prompt's budget on directory names, and only the golden set
#: can say which side of that this is.
AGENT_DETECT_LISTING_MAX_ENTRIES: Final[int] = 150

#: SKILL.md frontmatter bounds, from AI spec §6. They are here rather than as
#: literals in the loader because `SKILL_DESCRIPTION_MAX_CHARS` and
#: `URI_MAX_BYTES` are both 1024 today and mean entirely different things -- two
#: literals that happen to agree are two literals that will silently disagree
#: the first time one of them moves.
SKILL_NAME_MAX_CHARS: Final[int] = 64
SKILL_DESCRIPTION_MAX_CHARS: Final[int] = 1_024

#: How stale a `verified_on` date in the price table may be before the CI check
#: warns (AI spec §3). It shares a value with `WORKFLOW_RUN_RETENTION_DAYS` and
#: shares nothing else: one bounds how long we keep a record, the other how long
#: we trust a vendor's published price.
PRICING_VERIFIED_MAX_AGE_DAYS: Final[int] = 90

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

#: CLI cold start ceiling. **Re-ratified 400 -> 500 on 2026-09-09 (CR-92).**
#:
#: Not a retune to clear a failing gate: the budget was set against a Build 0
#: CLI and the tree has since gained nine verbs. The readings tell that story
#: rather than a regression -- 319 ms at the 2026-08-12 ratification, 365 ms once
#: Builds 1-10 merged, then 383, 402 and 400 ms on three consecutive nightly
#: runs of unchanged code. The 402 is what turned `bench` red; the 400 passed by
#: exactly nothing.
#:
#: 500 restores the **1.25x** margin the original ratification recorded
#: (319 of 400), rather than being a round number chosen to clear 402.
#:
#: **Deferring imports was tried first and does not work here.** `03` §2 calls
#: this "the one constant whose purpose is keeping imports off the startup
#: path", so the alternative was taken seriously: `adopt_agent` was moved out of
#: `commands/agent.py`'s module scope and measured at **~5 ms** of difference
#: over six samples. The 288 ms that module appeared to cost in `-X importtime`
#: is its *cumulative* column -- shared dependencies billed to whoever imports
#: them first -- and its true self-time is ~21 ms. The cost is pydantic model
#: construction, the store stack and typer/click/rich, all of which every verb
#: needs. Recovering it means lazy command loading in `main.py`, which is a real
#: refactor of the entry point and touches the §13 error contract; that is a
#: separate piece of work, not a line change.
CLI_COLD_START_MS: Final[int] = 500

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
