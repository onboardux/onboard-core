---
name: adopt-dev-substrate
description: The Build 0 substrate of adopt-core and the rules its code enforces — canonical identity URIs, the firm/engagement/system/environment scope, append-only revision families, facades and the *Records port pattern, coverage as a function with an alarming cache, freshness resolution, byte-stable export/import, the runtime annex, and store doctor. Use whenever changing or re-implementing adopt-identity, adopt-scope, adopt-store, adopt-coverage, adopt-freshness or adopt-export, adding a storage-engine-specific capability, touching revisions or bindings at the store level, or debugging a coverage disagreement, a stale item, a REVISION_CHAIN_FORK or a round-trip that is not byte-identical.
---

# The substrate

Every build after Build 0 fills tables and serves them; none of them designs a
schema, an id scheme, a revision model, a coverage or freshness definition, a
serialisation or a tenancy model. Those were made structural here. The rules
below are what the code enforces. The module docstrings carry the reasoning;
read them before changing a line.

## Identity: one canonical URI per referent

`onboard-v1://firm/engagement/system/environment/kind/namespace/key…` —
`adopt_identity.build_uri / parse_uri / validate_uri`, pure and deterministic.

- Built from **immutable slugs, never ids**, so a bundle stays resolvable after
  it leaves our hands. The environment is mandatory. An empty namespace renders
  `-`. The scheme label is the `URI_SCHEME` constant, never a literal.
- **The key is a sequence.** `POST /v1/orders` is one segment whose slash is data;
  `billing/charges/refund` is three segments. One string cannot carry the
  difference.
- **NFC, then exactly one pass of percent-encoding.** The builder refuses
  pre-encoded input; the parser refuses a segment whose single decode still
  yields an escape. That makes them exact complements (`URI_DOUBLE_ENCODED`).
- **Byte-exact comparison, no case folding.** `validate_uri` re-renders and
  compares, because `identity.uri` is UNIQUE and a URI that parses but renders
  differently would let one referent occupy two rows.
- **A URI is never rewritten.** A move appends an `identity_revision` with
  `status='moved'` and `alias_of_identity_id`, and the old URI resolves forever.
  `IdentityFacade.observe` is idempotent, keyed on the URI: seeing the same
  referent again writes nothing.

## Scope

`adopt_scope`: four levels, each with an immutable slug unique within its parent
(`SLUG_PATTERN`). A rename raises `SCOPE_SLUG_IMMUTABLE`. A slug is never
reissued after `ARCHIVED`/`DISCONNECTED`, because reuse would re-point every
historical URI. Every `lifecycle_state` change writes a `system_lifecycle_event`
in the same transaction; `lifecycle.transition` is the only caller of the setter.
`resolve()` returns ids and slugs, because the URI builder needs slugs and the
store needs ids.

## Nothing is updated in place

Four revision families (`identity`, `knowledge_item`, `binding`,
`probe_definition`) are append-only chains, held as **data**: `FAMILIES` in
`adopt_store/revisions.py` differs per family in exactly three fields (terminal
status, head pointer, parent column). Never add a branch per family.

- `append_revision` is the **only** mutation. It enforces `expected_head_id`
  (`REVISION_CHAIN_FORK` on mismatch), and the new revision and the advanced
  head pointer commit in one transaction.
- Retirement appends a terminal-status revision. There is no delete path, and no
  update method on any facade for a `*_revision` table (`no-revision-update`, in
  both repositories).
- `current_revision_id` is a pointer with **no FK**, because
  parent → revision → parent is cyclic. Helpers and `store doctor` validate it.
- A `Draft` never carries an id, parent id, `supersedes_revision_id` or
  `created_at`. Those are the writer's; a caller that could set them could forge
  history.

## Facades and ports: how a package touches storage

The pattern, which F1 and every later build reuse:

1. The **consuming package declares a structural protocol** (a `*Records` port)
   in its own `records.py` or `ports.py`, with only the methods it needs. A port
   with no write method is how "this function writes nothing" is guaranteed
   rather than remembered (`adopt_freshness.records`, `adopt_coverage.records`).
2. The **realization** lives in `adopt_store.sqlite…` (and, privately, in the
   plane's Postgres package). `no-raw-sqlite` allows `sqlite3` in
   `adopt_store.sqlite` only, and import-linter follows indirect chains. So a
   domain package importing `adopt_store` to reach a helper breaks the contract
   transitively. Declare the port instead.
3. The CLI (the composition root) wires realization to port. No facade returns a
   connection, cursor or SQL. Ids are generated inside the facade and scope is
   injected by it; neither comes from a caller.

The pattern is also why the Postgres realization can reuse every domain rule
unchanged: one set of rules, two realizations, one escape suite.

## Public names are a contract with consumers you cannot see

The operated plane is built from the **released** `adopt_*` packages, and it
imports names that nothing in this repository uses. For example,
`adopt_freshness.sensor_effective_health` is imported only by the plane (its
sensor view and console health tiles) and is named here only in a comment in
`adopt_const`. A change that is green on every gate in this repository can still
break the paid layer. So:

- **Never remove or rename a name in a package's `__all__`.** Treat it as
  additive-only, like the schema. When rebuilding or refactoring a package, keep
  every exported name, whether or not you can find a caller.
- **Follow the siblings' error conventions.** An id that names no row is
  `SCOPE_VIOLATION`, as the facades already raise, not whichever registered code
  sounds closest. Grep the facades for the same condition before choosing a code.

## Coverage is a function; the cache must alarm

`adopt_coverage.recompute_coverage(records, system_id)` is the authority: six
named inputs, evaluated in Python rather than one clever query, so a property
test can compare it with an independent derivation. It **computes and never
writes**. `adopt_coverage.cache.rebuild_cache` is the only writer of
`identity.covered_cache` in either repository (`no-covered-cache-write`). The
cache is rebuilt *from* the recompute, never the reverse. A disagreement
**alarms** (`COVERAGE_CACHE_DISAGREEMENT`) and never self-heals, because a quietly
self-healing cache is the invisible decay the rebuild exists to delete.
`store doctor` calls the recompute and not the rebuild, so looking never destroys
the evidence of who caused a drift.

Honesty lives in the inputs. Only confirmed knowledge (`verification='verified'`)
with a live binding and an audience tag counts. An unverified revision bound
structurally is still a gap.

## Freshness

`adopt_freshness.resolve_freshness` writes nothing, and its port has no write
method. Its rules:

- **Load-bearing propagation:** a changed identity stales an item only through a
  binding with `is_load_bearing = 1`, which defaults to 1 so a writer that
  forgets errs toward staleness.
- **Sensor override:** a sensor outside `HEALTHY` forces `observation_stale` on
  an item that would otherwise be fresh. Silence past
  `SENSOR_MISSED_CADENCE_MULTIPLIER × cadence`, or no heartbeat ever, counts as
  `STALE` health.
- **A `moved` binding is skipped, not consulted.** Its writer is
  `BindingFacade.supersede` (Build 6's rebind), and without the skip a rebind
  could never return an item to service. `retired` bindings still stale.
- **Precedence:** retired is terminal; a genuinely stale item stays `stale`,
  which beats `observation_stale`.
- The result names the **deciding rule**, because "stale" without a cause cannot
  be acted on.

## Export and import are byte-stable

`adopt export` → `adopt import` → `adopt export` produces **byte-identical table
files** (gate `golden-g0`, with no soft-fail). All three reasons live in
`adopt_export`:

- **The writer owns row order**: it sorts by the manifest's primary key over
  rendered values, never through a store's `ORDER BY` and its collation.
- **One rendering rule**: sorted keys, no spaces, no ASCII escaping, timestamps
  by `format_timestamp`.
- **No dialect**: `ExportRecords`/`ImportRecords` are declared ports.

Import verifies every digest **before any row**, restores rows verbatim with ids
included, is all-or-nothing, and needs an empty target
(`EXPORT_TARGET_NOT_EMPTY`). A bundle names one firm and one engagement
(`EXPORT_SCOPE_AMBIGUOUS`). Runtime-annex tables are excluded by construction,
because the writer iterates the manifest and the annex is not in it.

## The runtime annex

`.adopt/runtime.db` holds derived, rebuildable state: the FTS index, the question
log, refresh file-state snapshots. Its DDL lives in `schema/annex/`, never as a
string literal in `packages/`. It is never exported and never canon (R9: one
canon, one writer).

## Assets and paths: the trap that crashed the first binary

`schema/` ships inside the `adopt-schema` wheel and is located once by
`adopt_schema.assets` (override, bundled, checkout, else raise
`SCHEMA_ASSETS_MISSING`). **Never locate a file with `Path(__file__).parents[N]`.**
It is right in a checkout and in the editable install every test uses, and wrong
in an installed wheel or a one-file binary, where it raised `IndexError` at
import. A planted-violation test forbids deep `parents[N]` under `packages/`
outside `checkout_root`.

## Tests that guard this layer

`tests/unit/test_revisions.py`, the coverage and freshness unit and property
suites, `tests/golden/test_g0.py` (the round trip), `append-only` and
`durability` in CI, and `plant_violation.py --kind revision-update|drop-column|covered-cache-write`.
A change here re-runs all of them. Never weaken one to make a change fit.
