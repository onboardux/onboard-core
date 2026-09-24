---
name: adopt-dev-probe
description: How adopt-probe works and what must stay true when changing it — probes as declarative data, the strict manifest parser, the single module allowed to open a probe socket (the probe-io import contract), the host allow-list checked at connection time, secrets by reference, budgets through the agent seam's meter, versioned baselines, same-revision-only diff, conflict rows against bound knowledge, and prompt steps recorded verbatim. Use whenever changing adopt_probe (manifest, runner, baseline, diff, conflict), adding a step kind or expectation, touching probe networking or secrets, or debugging PROBE_HOST_UNDECLARED, probe_changed or a baseline that drifts on its own.
---

# Probes

A probe is the mystery shopper for a delivered system: a safe, repeatable,
recorded interaction whose baseline says "this is normal". It exists for systems
whose behaviour changes without a commit, AI systems foremost. **There is no
sandbox, and that is deliberate** (v6 D2, upheld by audit). The safety argument
is structural, and every change here must keep it true:

1. **A probe carries no executable content.** Two step kinds exist: `http`
   (method, declared host, path, body template, secret refs) and `prompt` (input
   text through the agent seam). There is no expression language, template
   logic or script hook, and none may be added without the design trigger
   ("two real baselines inexpressible as data").
2. **Only `adopt_probe.runner` opens a probe socket.** The `probe-io` import
   contract enforces it, proven by `scripts/plant_violation.py --kind probe-io`.
   The forbidden modules are named **top-level** (`urllib`, not
   `urllib.request`), because grimp collapses stdlib imports to their top package.
   A contract naming a submodule matches nothing and reports `KEPT` forever. The
   plant goes into a **submodule** (`adopt_probe/manifest.py`), never the
   package root, so the self-test also proves discovery.
3. **The allow-list is enforced at connection time**, not only at `add`.
   `manifest validate` checks shape and passes a step aimed at an undeclared
   host. The runner refuses it (`PROBE_HOST_UNDECLARED`, policy, exit 3) before
   opening anything, because a revision can arrive without passing through `add`.
4. **Secrets resolve by reference** (`secret_refs: ["env:NAME"]`, used as
   `{{secret.NAME}}`), and only declared refs may be interpolated. **No secret may
   reach a `probe_observation`**: it is an exportable column, so a leaked secret
   would travel in the client's bundle forever.
5. **A probe declares its budgets and the runner enforces all of them**: wall
   clock, request count, and model spend through the existing agent-seam meter,
   never a second meter. `PROBE_TIMEOUT_SECONDS` caps the run.

## The strict manifest

`adopt_probe.manifest` refuses unknown keys **inside steps and `expect`**,
because a mistyped `expect` key is an invariant nobody checks, reporting success
forever. **The top level is not held to that yet.** `_no_unknown_keys` is never
applied to the document root, so a typo such as `exercise:` is dropped
silently, and with it the conflict link. That is a known gap: closing it is
additive, but existing manifests carrying stray keys would begin to refuse, so
decide it deliberately. Also note that `probe manifest validate` runs only the
capability check, not the step parser. Any edit to a probe file, even a comment,
appends a revision, because the revision digest covers the file. `expect` accepts `status`, `json_fields`, `latency_under_ms` and
`min_similarity`; anything absent is not checked. `diff_method` accepts only
`exact` today: the other enum values are representable in the schema and none is
built. The capability-manifest validator (`adopt_policy.capability_manifest`) is
Build 0's shape check: `safe_path`, `deny_by_default: true`, a side-effect
policy, every runtime and cost limit, and `cleanup.required: true`.
`probe_definition_revision.safe_path` is NOT NULL with a CHECK constraint, so a
revision without a safe path cannot be represented at all.

## Baselines, diff and conflicts

- `probe add` is idempotent on (scope, `probe_id`); changed content appends a
  revision. Every observation carries its probe revision id.
- `baseline --set` versions the latest run that observed something.
- **Diff compares same-revision runs only.** An edited probe reports
  `probe_changed` with both revision ids and computes no similarity. Reporting a
  question change as a system change is the one mistake that trains an FDE to
  ignore the command. `refresh` inherits this through the shared
  `compare_in_scope`: drift becomes a `provider` change event, and
  `probe_changed` becomes nothing.
- `run` exits `1` only for `failure` or `blocked_by_manifest`; an observed change
  still exits `0`. `diff` exits `4` on drift.
- Where a drifted probe `exercises` an identity with confirmed knowledge, a
  `conflict` row is written and surfaces in gaps and packs (design bet 4: a
  conflict between intent and behaviour is a deliverable).

## Prompt steps

`prompts/probe-001/v1` is deliberately the thinnest prompt: "answer the supplied
text directly, add nothing", because our own instructions would otherwise be
recorded as part of the client's behaviour. **It declares no output schema.** The
seam validates a schema but never sends it, so a prompt telling the model to
answer in its own words failed its own `{"reply": …}` schema against a real
model. A recorded fake replays its script whatever it is sent, so CI could not
see it. The reply is recorded verbatim and never becomes knowledge.

## Stated residual

v1 does not verify cleanup after write-performing steps on sandbox paths. The
trigger for building it is the first real probe class whose safe path performs
writes. Until then, keep write methods out of shipped templates and examples.

## Tests

`probe-journey` (the design's G1 demo, using a recorded fake adapter in CI),
`test_probe_*` (runner, manifest, baseline and diff), the rogue-probe negative
control, and the `probe-io` plant:

```shell
uv run python scripts/plant_violation.py --kind probe-io
uv run lint-imports --config importlinter.ini      # must now fail, naming probe-io
uv run python scripts/plant_violation.py --revert
```
