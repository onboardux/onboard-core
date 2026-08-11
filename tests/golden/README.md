# The G0 golden suite

**G0 is the portability promise as a CI job.** Export → import → export must
produce byte-identical table files, and every identity in the second export must
resolve by URI alone. That is Build 0's definition of done, condition 2, and PRD
diagnostic D1 measures this job's pass rate at `1.0` over the trailing 30 CI days.

**There is no soft-fail mode.** The commercial promise — *stop paying and you keep
the knowledge* — does not have one either. `golden-g0` in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) carries no
`continue-on-error`, and it must never acquire one.

## What is here

| File | Holds |
|---|---|
| `fixture.py` | The fixture store: one populated row in **every** exportable table |
| `conftest.py` | `golden_store`, `golden_clock`, and the session-scoped `manifest` |
| `test_g0.py` | The three assertions, plus the fixture's own coverage check |

`test_fixture_covers_every_exportable_table` reads the table list from
`schema/canonical.yaml` rather than from a checked-in list. Adding a table
without extending the fixture therefore **fails**, rather than silently narrowing
what G0 covers — which is the failure that would make the other three assertions
quietly weaker every sprint.

## Re-cutting the fixture, on every release tag, forever

The fixture is **re-cut at every release tag, starting at v3.** It is not a
committed binary store: it is built from `fixture.py` at test time, which is what
lets it stay valid as the schema grows additively. What changes at a tag is the
*procedure's* output — the bundle a release is proven to produce.

At each release tag:

1. **Regenerate before cutting.** `uv run adopt-schema generate --check` must be
   clean. A fixture cut against a manifest the artifacts have drifted from
   proves the round trip for a schema nobody is shipping.
2. **Extend `fixture.py` for every table added since the last tag.** The coverage
   test names them; do not weaken it to make the suite pass.
3. **Cut the reference bundle with the downloaded, verified release binary.**
   The walkthrough may prepare the populated store from source, but source is
   not the subject of release evidence:

   ```sh
   uv run python scripts/s5_validation_walkthrough.py --store /tmp/g0-vN.db
   RELEASE_ADOPT=/path/to/verified/adopt-linux-x86_64
   ADOPT_STORE_PATH=/tmp/g0-vN.db "$RELEASE_ADOPT" export /tmp/golden-vN --json
   ```

4. **Attach `/tmp/golden-vN` to the release** as the version's reference bundle,
   and record its `manifest.json` digests in the release notes. From `0.3.0` the
   export format is a contract for third-party integrators (contracts §1.6), and
   a published bundle is what they test against.
5. **Never edit a published bundle.** A format change is a new `export_version`
   with its own reference bundle, exactly as a prompt change is a new version id.

## Why the fixture writes models rather than calling the facades

Twenty-nine of the thirty-six exportable tables have no facade — they belong to
build items 8 through 12. A facade-only fixture could not cover every table, and
writing facades to satisfy a fixture would invent the semantics of `escalation`
and `review_item` several builds before the sprint that owns them.

What G0 asserts is a property of the **bundle**: that whatever a store holds
survives export and import unchanged. Facade behaviour is asserted by CUJ-1 and
CUJ-2, which is where it belongs. The scope chain is the one exception and does
go through `ScopeFacade`, because `system_lifecycle_event` exists only as the
side effect of a real state change.
