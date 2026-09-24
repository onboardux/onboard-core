---
name: adopt-dev
description: Entry point for changing adopt-core's own source — which document or artifact is authoritative, where a change is allowed to live, the build-by-build map of packages, verbs, journeys and invariants, the per-change loop, and which adopt-dev-* skill owns the change. Use whenever implementing a feature, fixing a bug, adding a verb, flag, extractor pack, table, constant or error code, or reviewing a change in the adopt-core repository — "add a Terraform extractor", "make ask cite X", "why does this gate fail". Not for operating the CLI on a client engagement (that is the adopt plugin's adopt-cli skill).
---

# Changing adopt-core

`adopt-core` is the Apache-2.0 half of the Adoption-Phase Platform: twenty
workspace distributions behind one CLI, one canonical schema, and roughly thirty
CI gates. It is not a codebase to reason about from first principles. Nearly
every rule that looks arbitrary here is the fix for a defect that shipped, or
nearly shipped, and the reason is written in the module docstring where the rule
lives. **Read the package's `__init__.py` docstring before writing into it.**

## What wins when two sources disagree

1. **The machine-gated artifacts.** `schema/canonical.yaml` and its four
   generated targets; `adopt_const`; the `adopt_obs.errors` registry; the import
   contracts in `importlinter.ini`. CI enforces all of them.
2. **The design authority**, `architecture-design-docs-v6.md` §6 (v6.2) in the
   private design pack, if you have it. Code comments cite it as "v6.1 §N". Its
   section numbers did not move.
3. **The code's own docstrings and tests.** They record the as-built decision and
   why it was made.
4. Everything else, this skill included.

A conflict is a defect. Fix the losing artifact **in the same change**, and never
settle a disagreement silently in code.

## Placement is irreversible

A file pushed here is Apache-2.0 for the world from that moment. Closed
capabilities (the operated services, tenancy, the console) live in the private
plane repository and never here. `core-independent` forbids any `adopt_*` import
of `plane_*`. When nothing assigns a new file to a place, stop and ask before
creating it. Never place something by convenience.

## The build map

| Build | Delivers | Package(s) | Verbs | Journey (CI job) | Critical invariants |
|---|---|---|---|---|---|
| 0 | Substrate: schema, identity, store, revisions, coverage, freshness, export, policy, agent seam | `adopt-{schema,model,const,obs,identity,scope,store,coverage,freshness,export,detect,policy,agent,workflow,cli}` | `init detect boundary identity store coverage freshness export import doctor version agent envelope` | `golden-g0`, `append-only`, `durability` | — |
| 1 | System map | `adopt-map` | `map`, `map --report/--check-expected` | `map-journey` | #1 recall floor |
| 2 | Knowledge and links | `adopt-knowledge` | `ingest harvest bind gaps review` | `knowledge-journey` | #2 binding honesty, #3 move survival |
| 3 | Ask | `adopt-ask` | `ask answer serve` | `ask-journey` | #5 serve-path freshness, #7 grounding |
| 4 | Handover pack | `adopt-handover`, `adopt-knowledge.drafting` | `pack draft` | `pack-journey` | #7 grounding |
| 5 | Probes | `adopt-probe` | `probe add/run/baseline/diff` | `probe-journey` | `probe-io` contract |
| 6 | Watch | `adopt-map.diff`, `adopt-knowledge.changes` | `refresh`, `review --resolve` | `refresh-journey` | #4 propagation, #6 classification matrix |
| 7–8 client | Operated-system client verbs | `adopt-cli` (`remote`, `replica`) | `pull ci-sense` | — (plane-side journeys) | one writer (R9) |
| 9 | Verified handover | `adopt-handover` | `handover *` | `handover-journey` | — |

v6.2 §4 R6 closes the list of critical semantic invariants at eight. Each has
exactly one precise test. Extending the list means amending the design, not
adding a test.

## The loop for any change

1. **Locate the owner:** the package, and the adopt-dev-* skill below. Read the
   package docstring and the module you are touching.
2. **Contract first.** A new table, column, enum value, constant, error code,
   flag or JSON key starts in its gated artifact (`adopt-dev-contracts`).
3. **Test first, with its defect sentence:** *fails when ___ breaks; matters
   because ___; no other instrument catches it because ___.* Test count and line
   coverage are never targets. Give glue code no test of its own.
4. **Implement** the smallest change. Keep the rules in the domain package and
   the CLI thin.
5. **Watch the test fail against a planted defect** (revert the fix, or plant the
   violation), then pass.
6. **Run the gate sweep** (`adopt-dev-gates-release`), including
   `scripts/gen_skills.py --check` when a CLI command, flag or error code
   changed. The FDE plugin's skills are generated from the CLI and linted
   against it.
7. **Record it:** a `CHANGELOG.md` entry under `[Unreleased]`, and the
   regenerated artifacts in the same change.

## Which skill owns the change

| Change | Skill |
|---|---|
| A table, column, enum, constant, error code, id prefix, CLI flag or JSON key, or a prompt | `adopt-dev-contracts` |
| Identity URIs, scope, revisions, facades and ports, coverage, freshness, export/import | `adopt-dev-substrate` |
| An extractor, a pack, key schemes, digests, moves, the change cascade, recall lists | `adopt-dev-extractors` |
| Ingest, harvest, binding, the review queue, ask, capture, drafting, packs, handover | `adopt-dev-knowledge-serve` |
| Probe manifests, the runner, baselines, diff, conflicts | `adopt-dev-probe` |
| A gate, a planted violation, dependencies and licences, the release | `adopt-dev-gates-release` |

## Conventions that are gates, not preferences

- Every tunable lives in `adopt_const`; `constants-sync` fails on a duplicated
  literal anywhere under `packages/ scripts/ tools/ bench/`. `0`, `1`, `2` and
  `-1` are exempt, and an inline `# const-sync: ok -- <reason>` waiver prints on
  every run.
- There is no free-text log message: `get_logger()` takes snake_case events and
  structured fields. Content fields (`body`, `prompt`, `answer` …) are dropped
  and counted.
- Typed errors only: `AdoptError(ErrorCode.X, message, hint)`, registered.
  Never raise a bare exception across a package boundary, and never swallow one.
- Ids come from `adopt_obs.ids.new_id(prefix)` with a registered prefix.
- No sleeps in tests; use the injectable clock.
- OSS mode is offline by default with zero telemetry, permanently.
- Nothing is updated in place. There is no delete path, and no update method on
  any `*_revision` table.
