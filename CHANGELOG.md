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

Nothing yet. The first release will be `0.3.0`.

## [0.3.0] — not yet released

The first published release. Everything below is new; there is no upgrade path
from an earlier version because none was published.

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

[Unreleased]: https://github.com/onboardux/onboard-core/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/onboardux/onboard-core/releases/tag/v0.3.0
