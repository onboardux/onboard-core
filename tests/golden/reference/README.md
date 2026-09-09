# The reference bundles

**Current: `adopt-golden-v4.tar.gz`, attached to the
[`v0.4.0` release](https://github.com/onboardux/onboard-core/releases/tag/v0.4.0).**
**Superseded but never edited: `adopt-golden-v3.tar.gz` on the
[`v0.3.1` release](https://github.com/onboardux/onboard-core/releases/tag/v0.3.1).**
One record per export version, and a published bundle is never re-cut.
From `0.3.0` the export format is a contract for third-party integrators
(`02` §1.6), and a published bundle is what they test against. This directory
holds the record of which bundle that is; the bundle itself lives on the release
because it is an artefact, not source.

`v3-reference.json` and `v4-reference.json` each record their archive's digest,
the per-table row counts and digests, and the exact binary that cut it — release,
filename and `build_id`.

**v4 covers 37 tables, v3 covered 36.** Schema 4 added `coverage_gap` (Build 4),
and the fixture's own coverage test is what makes that number follow the manifest
rather than a checked-in list.

## Two things about this record that are easy to get wrong

**It is not a test oracle, and it cannot become one.** Ids are ULIDs and
timestamps advance, so re-running the cut procedure produces a completely
different bundle: two cuts taken minutes apart shared **0 of 36** table digests.
Anyone comparing a fresh cut against these values will find every one different
and should not conclude the export format broke. What is reproducible — and what
`golden-g0` asserts on every pull request — is that **export → import → export
over one store is byte-identical**, which is a different claim about the writer
rather than about a particular set of rows.

**It pins the published artefact, not the procedure.** The digests answer *"is
the bundle on the release still the bundle we cut?"* That question is worth
answering because `tests/golden/README.md` step 5 says a published bundle is
never edited, and a rule with no record is a rule nobody can check.

## Why the binary that cut it is recorded

The `0.3.0` binary wrote `written_by: adopt-core/0.0.0+unknown` into every bundle
manifest and every store it created, while its own `version --json` correctly
said `0.3.0` — it carried `importlib.metadata` for `adopt-cli` only, and the
provenance stamp resolves `adopt-store`. Cutting the reference bundle from that
binary would have enshrined the wrong provenance in the one artefact integrators
read, permanently, under a rule that forbids editing it.

That is why `0.3.1` exists and why this bundle is cut from it. `written_by` here
reads `adopt-core/0.3.1`, verified against the downloaded, attestation-verified
binary rather than a local build.

## The v4 cut

`adopt-golden-v4.tar.gz` was cut on 2026-09-07 from **`adopt-linux-x86_64`**,
downloaded from the `v0.4.0` release and verified with `gh attestation verify`
before it was run: the attestation binds it to `refs/tags/v0.4.0`, commit
`a1b32208f0a296bc708e283d860023ce7fdf4609`, built by `release.yml` at that tag.
`written_by` reads `adopt-core/0.4.0`, checked before attaching for exactly the
reason the section above gives.

The v3 cut used the Windows binary and this one used the Linux binary. That is
not a rule changing — the requirement is a *verified release* binary, and which
platform it is belongs in this record rather than in the procedure.
