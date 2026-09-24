---
name: adopt-dev-extractors
description: How adopt-map extracts identities and how Build 6 decides what changed — the Extractor protocol over a read-only SourceTree, archetype packs as modules, the key-scheme registry, the per-kind attribute digest and its extractor-version fence, loud extractor failures, move detection, the five-class change cascade, and curated recall lists. Use whenever adding or fixing an extractor, adding a pack for a new archetype or stack (Terraform, Django, dbt, low-code, …), changing what an identity's key or digest contains, or debugging a missed identity, a false SEMANTICS_CHANGED, a bogus BINDING_DEAD or a change storm in adopt-core.
---

# Extractors and the change cascade

`adopt map` turns a repository into identities deterministically. `adopt refresh`
compares a new observation with the store and classifies every difference.
**Nothing here calls a model, executes the tree or writes to it.** The map is only
as useful as it is stable: an identity that changes name with a formatter run, or
a digest that changes with a comment, stales knowledge nobody touched, and a
reviewer who sees that twice stops trusting the queue.

## The shapes

- **`SourceTree`** (`adopt_map/tree.py`): an in-memory, read-only view built on
  `adopt_detect.walk_files`, the programme's single walk. It honours a root
  `.gitignore` and always skips `.git`, `.adopt`, `node_modules`, `.venv` and a few
  others. Files over `MAP_MAX_FILE_BYTES` are skipped **and counted**. It has no
  method that can write.
- **`Extractor`** (`adopt_map/observation.py`): `name`, `version`, and
  `extract(tree) -> Iterator[Observation]`, pure. It yields and never writes.
- **`Observation`**: `kind` (an `identity_kind`), `key` (a **sequence** of
  segments), `namespace`, `attributes` (the digest input), `span` (file and
  lines), and an optional `note`.
- **The runner** (`adopt_map/runner.py`) owns every write, one transaction per
  pack, through `IdentityFacade.observe`. A failing extractor is caught,
  recorded with its exception type, and makes the run exit `1`. A silent
  extractor failure is indistinguishable from a smaller system, which is how one
  once hid for weeks.
- **Packs are modules of one distribution** (`adopt_map/packs/{generic,web,ai}.py`),
  registered in `packs/__init__.py:registry()` and chosen by `select_packs`
  from `system.archetype`, or by `--packs`. They are never separate wheels: the
  previous line shipped a wheel per pack, and `pip install adopt-cli` broke on
  every archetype.

## Adding an extractor

1. **Decide the key scheme first, in `adopt_map/keys.py`.** A kind, namespace and
   key choice becomes a persisted URI the first time someone runs `map` on a
   real engagement, and URIs are never rewritten. Changing a scheme later mints a
   second identity and orphans the first. Reuse the helpers there:
   `endpoint_key(method, path)` is one segment with slash-as-data;
   `module_key(path, *symbol)` is structural segments; there is also
   `dependency_namespace(ecosystem)`. Write a docstring saying why.
2. **Decide the attributes, which are the digest input.** They should be exactly
   what, if changed, means the referent changed: an endpoint's method, path and
   parameter names; a config key's name, type and default; a tool's canonicalised
   schema; a prompt's whitespace-normalised text; a job's schedule and target.
   **Never a raw file hash**, never line numbers, never ordering outside the
   attributes. `adopt_map.digest.attribute_digest` canonicalises with the
   exporter's rendering rule and mixes in the extractor version.
3. **Parse structurally.** Use `ast` for Python (a route in a docstring must not
   match, and a decorator split across lines must), manifests and YAML for their
   formats, and regex only for genuinely textual formats. tree-sitter is allowed
   only where a pack demonstrably needs it, and a new dependency needs a licence
   row (see `adopt-dev-gates-release`).
4. **Register it** in the right pack's tuple in `registry()`.
5. **Bump `version` whenever `attributes` change.** Digests are compared only
   within one extractor version. An upgrade reports "the instrument changed"
   rather than a storm of semantic changes; forgetting the bump reports the storm.
6. **Test it** with a small, purpose-built tree in `tests/unit/test_map_*.py`,
   each test naming its defect. Include one test proving a comment- or
   formatting-only edit leaves the digest unchanged.

## Adding a pack (a new archetype or stack)

v6 R10: packs are **pulled by a real engagement**, not pushed ahead of one. When
an FDE meets a system the three packs map thinly (a high `files_unmapped`), that
is the trigger:

- Add `packs/<name>.py`, register it in `registry()`, and map it from the
  archetype in `select_packs` (the archetypes are fixed by the schema enum:
  `web`, `platform`, `lowcode`, `data`, `ai`).
- Pin a **real reference repository** under `tests/reference/<slug>/` with a
  `repo.json` (URL, commit, archetype, scope, licence) and a curated
  `expected-identities.txt`: the 20–40 URIs an engineer would name after ten
  minutes in it. Never vendor third-party source.
- Extend `map-journey` (`tests/e2e/test_map_journey.py`, the `map-journey` CI job)
  to clone it at the pin and assert the list.

## Moves and the cascade (Build 6)

- `adopt_map.moves` pairs a disappeared referent with an appeared one **by
  digest** and reports three answers, two of which write nothing. An ambiguous
  pairing is reported and never guessed.
- `adopt_map.diff.compute` is **pure**: stored state and observed state in, a
  `DiffOutcome` out. The plane runs the same function server-side. The five
  classes:

  | Step | Condition | Class |
  |---|---|---|
  | 1 | a live identity not seen and not paired | `BINDING_DEAD` |
  | 2 | paired with an appeared referent by digest | `BINDING_MOVED` |
  | 3 | same URI, digest differs **at the same extractor version** | `BINDING_INTACT_SEMANTICS_CHANGED` |
  | 4 | an observed URI with no stored identity | `UNBOUND_NEW` |
  | 4 | a changed file whose identities all kept their digests | `BINDING_INTACT_RENDER_ONLY` |

- **Absence is not death.** A referent is exempt from `BINDING_DEAD` when its
  extractor failed, its pack did not run, or its file was skipped. Each exemption
  is reported, never silently applied. A broken extractor that retired its own
  inventory is the worst outcome available, because retirement is append-only.
- `refresh` exits `4` only on actionable classes. RENDER-ONLY is recorded and
  shown, but exits `0`. A file hash (`adopt_map.filestate`) feeds RENDER-ONLY
  only, and can never stale knowledge.

## The critical invariants this area owns

| # | Invariant | Where it is tested |
|---|---|---|
| 1 | Recall floor: every identity on a reference repo's curated list is extracted; misses named | `map-journey`, `map --check-expected` |
| 4 | Propagation: a load-bearing change stales the bound item; an unrelated change stales nothing | `refresh-journey`, `test_change_*` |
| 6 | Classification matrix: each class from a real tree edit, **including a comment-only edit that is not SEMANTICS_CHANGED** | `test_map_diff.py`, `refresh-journey` |

A miss on a curated list is a finding. **The entry is added before the fix**, and
an entry is never deleted to make the check pass. The glue pass
(`map --glue`, model-assisted) is reserved and flag-gated off until measured
deterministic coverage on the reference repos falls below the standing trigger.
Do not build it ahead of that.
