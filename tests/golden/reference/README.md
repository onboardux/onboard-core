# The v3 reference bundle

**`adopt-golden-v3.tar.gz`, attached to the
[`v0.3.1` release](https://github.com/onboardux/onboard-core/releases/tag/v0.3.1).**
From `0.3.0` the export format is a contract for third-party integrators
(`02` §1.6), and a published bundle is what they test against. This directory
holds the record of which bundle that is; the bundle itself lives on the release
because it is an artefact, not source.

`v3-reference.json` records the archive's digest, the per-table row counts and
digests, and the exact binary that cut it — release, filename and `build_id`.

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
