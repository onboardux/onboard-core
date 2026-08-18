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

#: The `detect-001` disambiguation pass's budget, stated by AI spec §5's prompt
#: table. **Not provisional**: `04` §5 fixes both values, so there is nothing for
#: S9 to ratify against a measurement. They are constants rather than literals
#: because `AGENT_DETECT_MAX_WALL_SECONDS` shares a value with `EXPORT_P95_SECONDS`
#: and shares nothing else -- one bounds how long one model call may take, the
#: other how long exporting fifty thousand items may take.
AGENT_DETECT_MAX_USD: Final[float] = 0.05
AGENT_DETECT_MAX_WALL_SECONDS: Final[int] = 30

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

# ---------------------------------------------------------------------------
# Build 1 (`adopt map`) -- `builds/build_1/03-implementation-spec.md` §3
#
# One constants home, many declaring documents (B1-CR-12). These land here
# rather than in a `map_const` module because a second home is a second place
# for a value to drift, and `constants_sync` reads both documents against this
# one module.
#
# `MAP_CONF_*`, the two ADR-0.1 ratios and `MAP_STAGE1_REQUIRED_FAMILIES` are
# **provisional**: S1.8 ratifies or revises them against the labeled corpus, and
# any revision is a logged clarification row, never a silent edit. Provisional
# does not mean advisory -- they gate CI today at the values below.
# ---------------------------------------------------------------------------

#: G3 stage-1 deadline. A usable `surface.md` exists by here or the north-star
#: metric is not met, whatever the full run later produces.
MAP_STAGE1_BUDGET_S: Final[int] = 900

#: G3 total deadline. Exhaustion is exit 3 -- a successful run with less output.
MAP_TOTAL_BUDGET_S: Final[int] = 3600

#: An unchanged re-run must finish inside this. CUJ-2's whole claim.
MAP_INCREMENTAL_BUDGET_S: Final[int] = 300

#: Per-extractor watchdog, and the variant for a `heavy = true` manifest. A
#: timeout degrades to the declared fallback and is recorded; it never fails the
#: run (PRD F7.3).
MAP_EXTRACTOR_TIMEOUT_S: Final[int] = 120
MAP_EXTRACTOR_TIMEOUT_LARGE_S: Final[int] = 300

#: Process-pool ceiling. **The tunable is the ceiling, not the pool size**: the
#: call site takes `min(MAP_MAX_WORKERS_CEILING, os.cpu_count() or 1)`, because
#: the machine's core count is discovered rather than chosen and a computed
#: value cannot be compared against the specification table at all.
MAP_MAX_WORKERS_CEILING: Final[int] = 8

#: File-index skip threshold, and the tree size above which sampling engages.
#: Sampling is always disclosed -- a sampled map that does not say so is a map
#: claiming completeness it does not have.
MAP_MAX_FILE_BYTES: Final[int] = 2_000_000
MAP_MAX_TREE_FILES: Final[int] = 250_000
MAP_SAMPLING_MODE_RATIO: Final[float] = 0.25

#: Confidence by evidence method (PRD F9.1). The **framework** assigns these; an
#: extractor that sets its own confidence is rejected by the plugin audit, which
#: is what keeps the ladder honest.
MAP_CONF_GRAMMAR: Final[float] = 0.95
MAP_CONF_REFLECTION: Final[float] = 0.92
MAP_CONF_DECLARED: Final[float] = 0.80
MAP_CONF_CTAGS: Final[float] = 0.70
MAP_CONF_REGEX: Final[float] = 0.45
MAP_CONF_AGENT_REVIEWED: Final[float] = 0.90

#: The writer's floor. Below this a fact becomes a recorded **gap**, not
#: knowledge -- silence beats guessing (PRD §1.6).
MAP_MIN_EMIT_CONFIDENCE: Final[float] = 0.40

#: The `source_version` projection digest. Named rather than inlined because
#: both projections and every stored composite depend on one answer.
MAP_DIGEST_ALGO: Final[str] = "blake2b-128"

#: Above this a diagram collapses into kind-level clusters, with a notice.
MAP_DIAGRAM_MAX_NODES: Final[int] = 300

#: What "usable map" means for the north-star metric: stage-1 covers every one
#: of these families that is actually present. Order is part of the value.
MAP_STAGE1_REQUIRED_FAMILIES: Final[tuple[str, ...]] = (
    "endpoint",
    "db_field",
    "job",
    "config_key",
    "prompt",
)

#: The two arms of ADR-0.1's reversal trigger. Deterministic share below the
#: floor, or glue rewrite rate above the alert, and the bet is failing.
MAP_PLUGIN_COVERAGE_FLOOR: Final[float] = 0.60
MAP_GLUE_REWRITE_ALERT: Final[float] = 0.40

#: `01` §6 **M8** -- outside-VCS recall against a pack's labeled identity set --
#: and `01` §9's flip trigger for `extractors.ai.enabled`, which are the same
#: number stated in two documents. Consumed by `scripts/label_eval.py` as the
#: default for `--min-outside-vcs-recall`, so the S1.5 exit gate is reproducible
#: without anyone retyping it. Provisional; S1.8 ratifies it with the other
#: bands.
MAP_OUTSIDE_VCS_RECALL_FLOOR: Final[float] = 0.90

#: Glue-pass budgets, per run. Exhausting any one aborts the pass at exit 6 with
#: the deterministic map intact (04 §7).
MAP_AGENT_MAX_COST_USD: Final[float] = 2.00
MAP_AGENT_MAX_WALL_S: Final[int] = 600
MAP_AGENT_MAX_FILES_SAMPLED: Final[int] = 40
MAP_AGENT_MAX_FILE_BYTES: Final[int] = 120_000

#: What the quarantine sandbox gives one agent-authored module before it is
#: killed, and how much address space it may claim (04 §6 step 3). **These two
#: were numbers in `04` §6's prose and in no module** -- the same defect S1.5
#: found in `MAP_OUTSIDE_VCS_RECALL_FLOOR` and S1.6 in `MAP_XML_MAX_DEPTH`: a
#: bound a document states and a program restates is a bound that measures
#: whatever the program's author typed (B1-CR-83). Both are retunable against
#: evidence, so both are tunables rather than inline waivers.
#: **Provisional** -- S1.8 ratifies them against the glue golden set, whose
#: reversal trigger is the first authored module that is correct and killed.
MAP_AGENT_SANDBOX_TIMEOUT_S: Final[int] = 60
MAP_AGENT_SANDBOX_MAX_BYTES: Final[int] = 536_870_912

#: SQLite busy timeout for a map run. Distinct from `STORE_BUSY_TIMEOUT_MS`:
#: that is the store adapter's, this is how long one run waits for another to
#: release the store before raising `MAP_STORE_LOCKED`.
MAP_RUN_LOCK_TIMEOUT_S: Final[int] = 30

#: How much of a file is read to decide whether it is binary. A NUL byte in the
#: first block is the heuristic git uses. Bounded on purpose: the alternative
#: reads a two-megabyte file to answer a question about its first line.
#: **Provisional** -- S1.8 ratifies it against the reference corpus.
MAP_BINARY_SNIFF_BYTES: Final[int] = 8192

#: How long an allowlisted analysis binary gets before the exec seam gives up and
#: the ladder degrades. Kept well below `MAP_EXTRACTOR_TIMEOUT_S` so a hung tool
#: costs one rung rather than the extractor's whole watchdog budget.
#: **Provisional** -- S1.8 ratifies it against the reference corpus.
MAP_TOOL_TIMEOUT_S: Final[int] = 30

#: The longest configuration **default** `common.config` will record. Anything
#: longer is omitted rather than stored: a value that size is more likely a token
#: than a setting, and `01` N9's asymmetry is that a missing default costs a
#: reader one lookup while a recorded secret is a breach.
#: **Provisional** -- S1.8 ratifies it against the labeled corpus.
MAP_CONFIG_VALUE_MAX_CHARS: Final[int] = 64

#: How many URIs a first-screen list may name before it stops being a headline.
#: `02` §9.1 makes the first screen the honest summary; a run with 5,000
#: outside-VCS settings would otherwise bury its own count under its own list.
#: **Provisional** -- S1.8 ratifies it against the cold-FDE exercise.
MAP_FIRST_SCREEN_LIST_MAX: Final[int] = 20

#: How deep `adopt_map.xmlsafe` walks an export bundle's XML. A Salesforce
#: retrieve, an update set and a Power Platform solution all nest a handful of
#: levels; a document nesting sixty is not one this build can say anything true
#: about, and an unbounded walk over a hostile one is the amplification the seam
#: refuses at the parser, arriving through our own traversal instead.
#: **Provisional** -- S1.8 ratifies it against the reference corpus, and the
#: reversal trigger is the first real bundle that loses a component to the bound.
MAP_XML_MAX_DEPTH: Final[int] = 64

#: Build 1's run-artifact and front-matter format versions. Both start at 1 and
#: move independently of `SCHEMA_VERSION` and `EXPORT_VERSION`; `surface.json`
#: is a run artifact and **not** an interchange contract (02 §9.2).
SURFACE_REPORT_VERSION: Final[int] = 1
SURFACE_ATTRS_VERSION: Final[int] = 1
