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

[Unreleased]: https://github.com/onboardux/onboard-core/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/onboardux/onboard-core/releases/tag/v0.3.1
[0.3.0]: https://github.com/onboardux/onboard-core/releases/tag/v0.3.0
