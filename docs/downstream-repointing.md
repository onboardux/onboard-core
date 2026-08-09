# Downstream re-pointing — what items 1, 2 and 3 must change

**A notice, not a specification.** PRD §9 and handoff index §9 both say so, and
the distinction is load-bearing: this file tells three already-built items *that*
they must change and *what* changed underneath them. It does not tell them how,
because each owns its own write path and none of the three is in scope for
Build 0's pack.

**Status:** Build 0 shipped schema version 3 as a rebuild. There is no migration
from the withdrawn `0.1.x` line, no dual-read window and no compatibility shim —
that deletion is why the rebuild was worth doing, and nothing here reintroduces
it. Any store created by `0.1.x` is discarded.

> **Do not begin any of the three before G0 is green.** Re-pointing against a
> moving schema is the failure this sequencing exists to prevent.
> G0 is green: `golden-g0` runs on every pull request with **no soft-fail**.

---

## What changed underneath you

Five things, and each one breaks a specific assumption that was reasonable
before.

| Was | Is now | What it breaks |
|---|---|---|
| Bare-string identity keys, `adopt:<system_id>/<kind>/<path>`, built from a ULID | `onboard-v1://firm/engagement/system/environment/kind/namespace/key`, built from **immutable slugs** | Every stored key. A ULID-derived key cannot be reconstructed from a bundle, which is what made the degraded-mode promise unkeepable |
| `identity_registry` | `identity` + `identity_revision`, written through the revision helpers | Direct writes. There is no facade update method and no delete path |
| `knowledge_item` mutated in place via `updated_at` | **Append-only revisions** across four families | Every UPDATE. `append_revision` is the only mutation path and enforces `expected_head_id` |
| `identity_registry.covered` read as truth | `identity.covered_cache` is a **cache**; `recompute_coverage()` is the authority | Reading `covered` to decide anything. Disagreement **alarms**; it never self-heals |
| The v2 boundary shape | Plane-location, permitted-outbound and observation-recency columns | The old boundary row shape |

---

## Item 1 — System surface map

**Reads what changed:** bare-string identity keys; `identity_registry`.

**The re-point:**

1. Emit URIs via `build_uri()` rather than composing a string. The scheme label
   is the `URI_SCHEME` constant, never a literal.
2. Write `identity` + `identity_revision` through the revision helpers.
3. **Re-derive every identity.** This is not a data migration — the key changes
   shape, so it is a fresh derivation from the source system.

**The two subtleties that will bite, both of them deliberate:**

- **The key is a sequence, not a string.** `POST /v1/orders` is *one* segment
  whose slash is data; `billing/charges/refund` is *three* segments whose slashes
  are structure. One opaque string cannot carry that difference, so `build_uri`
  takes a sequence and an extractor that flattens too early produces a different
  referent.
- **Double encoding is refused at the builder.** A key whose literal text is
  `a%20b` and a double-encoding of `a b` are the same string, so no parser can
  tell them apart. The builder therefore refuses pre-encoded input. The cost is
  accepted and stated: a referent whose name literally contains `%20` is
  unaddressable.

---

## Item 3 — Binding index

**Reads what changed:** direct `binding` writes; `covered` as truth.

**The re-point:**

1. Append `binding_revision`; never update a binding row.
2. **Set `is_load_bearing` explicitly.** It defaults to `1` so an unset writer
   errs toward staleness, which is the safe direction — but a binding index that
   marks everything load-bearing stales two hundred items on a shared-utility
   change, and that is CUJ-4's whole point.
3. Read coverage from `recompute_coverage()`. Never from `covered_cache`, which
   only `adopt-coverage` may write — enforced by the `no-covered-cache-write`
   import contract, so this is a build failure rather than a review comment.

---

## Item 2 — Observability boundary

**Reads what changed:** the v2 boundary shape.

**The re-point:** populate the new plane-location, permitted-outbound and
observation-recency columns.

**The gate you now sit behind:** the outbound envelope validator rejects content
under `metadata_only`, and the content deny-list is matched **by field name at
any depth, including inside lists** (CR-39) — so `{"items":[{"body_md": …}]}` is
caught. The list is `03` §4.2's eight names plus any field whose manifest column
type is `md` or `text`, derived at validation time rather than hand-kept, because
a hand-kept list is correct until the next `md` column is added by someone with
no reason to look, and that failure is silent in the permissive direction.

---

## What you can rely on from here

- **The schema is additive-only from the `0.3.0` tag, forever.** A downstream
  item adds a column by touching `schema/canonical.yaml` and the generated
  artifacts, and nothing else — that is CUJ-1, and it is Build 0 definition-of-
  done condition 5.
- **No later build item writes a migration.** All 37 tables exist at version 3,
  including the ones nothing writes to yet.
- **A bundle round-trips byte-identically.** `adopt export` → `adopt import` →
  `adopt export` produces identical table files, asserted by `golden-g0`.
- **`export_version` moves independently of `schema_version`.** Integrate against
  the export, never against the tables; `schema/export_compat.json` records which
  `schema_version` each export version implies (CR-37).

## What is not settled yet

- **Build DoD conditions 1, 3, 4 and 6 are open** at the time of writing. See the
  sprint plan's definition of done and `BACKLOG.md`.
- **`BACKLOG.md` B-03** — `adopt-plane` realizes one of the twelve `*Records`
  ports `adopt-core` declares. This does not affect the OSS store, which realizes
  all twelve; it affects the Postgres realization only.
