# Changelog

Notable changes to `adopt-core`. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

**Versioning, pre-1.0:** the **minor version tracks the schema version**, so
`0.3.x` ships schema version 3. That is why the first published release is
`0.3.0` and not `0.1.0`. Patch releases never change the schema.

**From `0.3.0`, store and wire evolution is additive-only** and enforced in CI
by `adopt-schema lint`. Before that tag the manifest was free to change; after
it, a removed or retyped column is a rejected pull request.

## [Unreleased]

Nothing yet.

## [0.4.0] — 2026-09-07

**The first release carrying Builds 1–10.** Nine new verbs, five new
distributions, and **schema version 4**. The minor version tracks the schema
version, which is why this is `0.4.0` and not `0.3.2`.

**Schema 4 is additive and a version-3 store is not stranded.** Build 4 adds one
table, `coverage_gap`, as a per-version migration tranche
(`0002__coverage_gap.sql`, both dialects), so an existing `0.3.x` store upgrades
under `adopt store migrate` rather than being replaced, and a version-3 export
bundle still imports — asserted by `tests/unit/test_schema_v4_compat.py`.
`export_version` moves to `4` with it.

**Five new distributions**, bringing the canonical release set to twenty:
`adopt-map`, `adopt-knowledge`, `adopt-ask`, `adopt-handover`, `adopt-probe`.
Each is a v6.1 §2.1 **A2** ratification recorded in
`scripts/release_context.py`. Nineteen of the twenty are reachable by installing
`adopt-cli`; `adopt-workflow` is a library the CLI does not depend on.

### Added

- **`adopt map`** (Build 1) — the inventory. Three archetype packs (generic, web,
  ai) walk a repository once and record identities with a per-kind attribute
  digest, so a later run can tell a moved referent from a new one.
  `--check-expected` measures recall against a curated list rather than a
  coverage ratio.
- **`adopt ingest`, `adopt harvest`, `adopt bind`, `adopt review`** (Build 2) —
  documents and local git history become knowledge, bound to identities. A
  structural match binds; a name match is a suggestion a human confirms, which
  is the whole of critical invariant #2.
- **`adopt ask`, `adopt answer`, `adopt serve`** (Build 3) — the store answers,
  says how much to trust the answer, and escalates what it cannot. An
  escalation's answer is captured as confirmed knowledge, so the next asker gets
  it.
- **`adopt pack`, `adopt draft`, `adopt gaps`** (Build 4) — audience-scoped
  handover packs, byte-stable given the same revisions, with a gap appendix. The
  new `coverage_gap` table carries a disposition per gap.
- **`adopt probe`** (Build 5) — declarative probes with a capability manifest,
  a versioned baseline and a diff that names what changed. A probe carries no
  executable content and exactly one module can open a socket, which is the
  whole safety argument for having no sandbox.
- **`adopt refresh`, `adopt review --resolve`** (Build 6) — re-map, re-probe,
  classify what changed, stale the bound knowledge and open one review session.
- **`adopt pull`, `adopt ci-sense`** (Builds 7–8) — the client halves of the
  control plane: a verified read replica, and a CI relay that senses a change
  and posts it for the plane to classify.
- **`adopt handover`** (Build 9) — six recorded steps and an acceptance record
  the client keeps, with a digest they can reproduce.

### Fixed

- **Every parser-level usage error escaped the CLI as exit `1` with a
  traceback.** The installed typer vendors its own click, so nothing `main`
  caught matched what typer raised: `adopt pack --no-such-flag --json` and
  `adopt identity build --json` exited `1` with a rich panel and an empty
  stdout, where §13 says `2`. Three `adopt pack` refusals rode the same hole.
  Now exit `2` (or `3` for the policy refusal) with the message on stderr, and a
  gate refuses any click or typer parser exception raised in a command.
- **Eleven canon-writing verbs could write into a read replica.** `refresh` and
  `handover` each carried their own check; `init`, `map`, `ingest`, `harvest`,
  `bind`, `gaps`, `review`, `answer`, `draft`, `pack --draft-missing` and
  `probe add/run/baseline` carried none, and every write succeeded — into a file
  the next `adopt pull` replaces wholesale. One guard now sits at the one door
  they all open through (`STORE_TARGET_IS_REPLICA`). `adopt ask` still answers
  on a replica, which is what a replica is for.
- **An ingest under one environment appended to another environment's item.**
  Build 2's reads matched on `system_id` alone, so a staging ingest of the same
  relative path recognised the **prod** item as already stored and appended the
  staging text to its revision chain — exit `0`, no staging item, and the chain
  reading as an ordinary edit.
- **`adopt bind` joined two firms' data.** An item and an identity from
  different firms produced a binding row whose ends resolve to different
  tenants; now refused with `SCOPE_VIOLATION` before the insert.
- **Ingest, harvest and the review actions were not atomic.** Each chained
  facade calls that commit separately, and idempotence keys on the provenance
  row — so a document whose item and provenance committed and whose audience tag
  did not was reported `unchanged` by every retry, permanently. A confirmation
  whose binding write failed left the queue saying a human had confirmed an
  action that never occurred, and `resolve` refused the retry. Each logical
  write is now one transaction.
- **A stale queue entry could bind what the current document no longer says.** A
  queue entry keys on `(item_id, revision_id)` and survives the edit that
  invalidates it; suggestions now derive from the item's head, while the body
  the reviewer reads is unchanged.
- **`adopt map --check-expected` passed over an emptied list.** A file of only
  comments exited `0` with no `expected` payload at all — a perfect recall floor
  over nothing. Now refused.
- **Move detection could alias two unrelated referents for ever.** Pairing was
  by attribute digest alone, so a `config_key` and an `endpoint` whose
  attributes rendered identically collided and the alias was written through
  `IdentityFacade.move()`. Pairing is now within one identity kind and one
  extractor.
- **The `config_key` namespace was the file's basename**, so
  `services/a/config.json` and `services/b/config.json` collapsed to one
  namespace and the second observation was absorbed by URI-keyed idempotence.
  It is now the repository-relative path stem. **This changes persisted
  `config_key` URIs** and is done deliberately before the first release carrying
  Build 1, when no store depends on the old scheme.

### Security

- **Directory ingest followed symlinks out of the tree.**
  `adopt ingest docs/` used an unbounded `rglob` with no containment rule, no
  symlink rule, no depth bound and no count bound, so a link committed into a
  client repository copied out-of-tree material into the knowledge store and
  cited it by absolute path. Ingest now enumerates through the one bounded walk
  the rest of the programme uses. An explicitly named file is unchanged: naming
  a path is a choice.
- **Oversized files bypassed the map's file-count bound.** Only readable files
  counted toward `MAP_TREE_TOO_LARGE`, so a tree of files each over
  `MAP_MAX_FILE_BYTES` walked to the end and refused nothing. Every candidate
  now counts before it is classified.

## [0.3.1] — 2026-08-19

A patch release fixing two defects in `0.3.0`. **No schema change** — the store
and wire formats are identical, so a `0.3.0` store opens under `0.3.1` and a
`0.3.0` bundle imports into it unchanged. `export_version` and `schema_version`
both remain `3`.

### Fixed

- **The single-file binary stamped false provenance into everything it wrote.**
  It reported `"version": "0.3.0"` correctly but wrote
  `written_by: adopt-core/0.0.0+unknown` into every exported bundle manifest and
  into `schema_meta` of every store it created. Three modules resolve their
  version through `importlib.metadata` and they do not all ask about the same
  distribution — `version --json` asks for `adopt-cli`, the provenance stamp asks
  for `adopt-store` — and the binary carried metadata for the first only, so the
  second took a silent fallback. **Nothing reads `written_by` for a
  compatibility decision**, so no `0.3.0` bundle fails to import and no `0.3.0`
  store fails to open; what is wrong is the audit record, permanently, in
  artefacts clients keep. **Wheel installs were never affected.**
- **`adopt-store` and `adopt-cli` each imported a first-party module they did not
  declare**, so `pip install adopt-store` alone raised `ModuleNotFoundError` on
  its first facade import. `adopt-store` now declares `adopt-identity` and
  `adopt-cli` declares `adopt-model`. `pip install adopt-cli` was never affected.

### Added

- **`first-party-deps` CI gate.** Nothing could observe the dependency defect:
  every test runs under `uv sync --all-packages`, where all fifteen
  distributions are present whatever any one of them declares, and
  `packaged-artifact` installs `adopt-cli`, which supplied the missing name
  transitively. The gate judges each manifest against the imports in its own
  `src/`, discovers the module-to-distribution map rather than listing it, and
  refuses to pass a run that discovered nothing.
- **A release smoke step that reads back what the binary wrote.** Every existing
  smoke test asked the binary what it was; none opened a file it had produced,
  which is why four release runs and a full three-platform matrix never saw the
  provenance defect.

## [0.3.0] — 2026-08-16

The first published release. Everything below is new; there is no upgrade path
from an earlier version because none was published.

**Superseded by `0.3.1`, which fixes two defects in this release.** The
single-file binary writes `adopt-core/0.0.0+unknown` as provenance into every
bundle and store it creates, and `adopt-store` and `adopt-cli` are each missing
one dependency declaration. Neither breaks a `pip install adopt-cli` journey.
See `0.3.1` above.

### Added

- **`adopt` CLI** — `init`, `detect`, `boundary`, `export`, `import`,
  `identity`, `coverage`, `freshness`, `store`, `probe`, `envelope`, `agent`,
  `doctor`, `version`. Offline by default; network access is an explicit
  per-invocation choice.
- **Schema version 3**, generated from one manifest into SQLite DDL, Postgres
  DDL, a JSON Schema and Pydantic models. The manifest is the only authority and
  no target is hand-edited.
- **Portable knowledge store** on SQLite, with append-only revisions for
  identities, knowledge items, bindings and probe definitions. There is no
  update-in-place path and no delete path.
- **Canonical identity URIs** — `onboard-v1://firm/engagement/system/environment/kind/namespace/key`
  — built from immutable slugs.
- **Byte-stable export and import.** `export` → `import` → `export` produces
  byte-identical table files, asserted in CI over a fixture covering all 36
  exportable tables.
- **Archetype detection** across five archetypes, which refuses and ranks rather
  than guessing when confidence is low.
- **One model seam.** Every model call goes through `adopt_agent.Runner`; no
  adapter imports a vendor SDK, and that is enforced by an import contract with
  a planted-violation test.
- **Supply chain**: 15 wheels, 15 source distributions, three single-file
  binaries, a CycloneDX SBOM spanning all three release platforms, SLSA
  provenance, and a keyless cosign signature and certificate for every payload
  including the SBOM. A release missing any of the three is not a release.

### Security

- **No telemetry in OSS mode, permanently.** There is no opt-in switch and none
  will be added.
- **Client content is structurally excluded from logs** — deny-listed fields are
  dropped at the sink and counted, at any nesting depth.
- **Secrets are never persisted.** `adopt doctor` reports presence and source,
  never value.
- Every distributed dependency is licence-verified; the in-binary policy is
  permissive-only.

### Notes

- `adopt-core` is the repository's product name and the prefix of all fifteen
  distributions. The repository is `onboardux/onboard-core` and the URI scheme
  is `onboard-v1://`. Three names, each naming a different thing: where the code
  lives, what you run, and what the URI addresses.
- A store written by a newer binary opens **read-only** under an older binary and
  is never upgraded, downgraded or repaired in place. That is what makes rolling
  a binary back safe.

[Unreleased]: https://github.com/onboardux/onboard-core/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/onboardux/onboard-core/releases/tag/v0.4.0
[0.3.1]: https://github.com/onboardux/onboard-core/releases/tag/v0.3.1
[0.3.0]: https://github.com/onboardux/onboard-core/releases/tag/v0.3.0
