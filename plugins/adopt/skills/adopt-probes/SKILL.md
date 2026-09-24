---
name: adopt-probes
description: Record and watch a system's behaviour with adopt probes — author a declarative probe manifest (safe path, declared hosts, budgets, invariants, secrets by reference), validate it, run it, set a versioned baseline, and diff later runs to catch behaviour that changed with no commit behind it (a model provider update, a flipped flag, an upstream contract). Use whenever someone wants a behavioural baseline, suspects an AI or API system changed without a deploy, asks to "probe", "smoke-test" or "baseline" a client system, or hits PROBE_HOST_UNDECLARED, PROBE_BASELINE_MISSING or `probe_changed`. Load adopt-cli first.
---

# Behaviour baselines with probes

**Why:** everything else in `adopt` reads the repository. Some systems, AI
systems above all, change behaviour with no commit: the provider updates a
model, a flag flips, an upstream contract moves. A probe is a recorded, safe,
repeatable interaction; its baseline says "this is normal", and a later diff
says what changed.

**Probes are data, not code.** A manifest carries no executable content. Exactly
one module in `adopt` can open a probe's socket, and it checks the host
allow-list before connecting. That is the whole safety argument, so never
weaken it: never widen an allow-list, or add a step, to get a probe past a
refusal.

Read `adopt-cli` first. Probes need tier `T3` or better (a safe interaction path
exists); `init` reported the tier.

## 1. Agree the target with a person

Before writing anything, ask:

- **Which environment is safe to hit?** `safe_path` is `mock`, `sandbox` or
  `shadow`, and it is recorded on every revision. Production is not a safe path
  unless the person says a shadow path exists.
- **Which hosts exactly?** Host and port as the steps will address them. They
  become `network.allow`.
- **Which methods?** Default to read-only `GET`. A write-performing step needs an
  explicit reason, and `adopt` does not yet verify cleanup after writes.
- **Which secrets, and under which environment variable names?** A probe names a
  secret; it never contains one.
- **Which identities does this exercise?** `exercises` URIs are what turn a drift
  into a *conflict* against the knowledge bound to them. Without them the probe
  observes a change nobody said was about anything.

## 2. Write the manifest (in the workspace)

Start from `assets/probe-http.template.yaml` (and `assets/probe-prompt.template.yaml`
for a model interaction). Save it under `../<system>-adopt/probes/`, never inside
the repository. The rules the validator and runner enforce:

| Field | Rule |
|---|---|
| `probe_id` | Short and stable. `add` is idempotent on (scope, probe_id); changed content appends a revision. |
| `safe_path` | `mock`, `sandbox` or `shadow`. Required. |
| `network` | `deny_by_default: true` (required), and `allow:` listing every host a step reaches. |
| `http_methods.allow` | The methods steps may use. |
| `side_effect_policy` | `prohibited` (default), `compensating` or `declared`. Required. |
| `runtime` | `max_seconds`, `max_memory_mb` and `max_requests`. All required; an absent limit is an unbounded one. |
| `cost` | `max_model_calls` and `max_tokens`. Both required, even at zero. |
| `cleanup.required` | Must be `true`. |
| `output` | `retain_raw` and `redaction_policy`. |
| `exercises` | Identity URIs; build them with `adopt identity build`. |
| `diff_method` | `exact`, the only method built today. |
| `secret_refs` | `env:NAME` entries; steps use them as `{{secret.NAME}}`. Only declared refs may be interpolated, and secrets never appear in an observation. |
| `steps` | `kind: http` (`method`, `url`, optional `headers`, `body`, `expect`) or `kind: prompt` (`input`, `expect`). |
| `expect` | Only `status`, `json_fields`, `latency_under_ms` and `min_similarity`. Anything else is refused, because a mistyped key would be an invariant nobody checks. Anything absent is not checked. |

**Unknown keys are refused inside `steps` and `expect`, but not at the top level.**
A typo such as `exercise:` for `exercises:` is accepted silently, and the conflict
link is lost. After `probe add`, check that the envelope echoes every URI you put
under `exercises`.

## 3. The authoring loop — stores nothing

```shell
adopt probe manifest validate ../orders-api-adopt/probes/checkout.yaml --json
adopt probe run ../orders-api-adopt/probes/checkout.yaml --json
```

`manifest validate` checks the capability manifest only: safe path, allow-list,
limits and cleanup. It does **not** parse `steps`, so it passes a malformed step,
and a step aimed at an undeclared host. `probe run FILE` parses everything and
executes without storing anything. It is the authoring loop, and the negative
control. Running
a probe sends real requests to the client's environment, so get a yes first
(`adopt-cli`, section 5).

- **`PROBE_HOST_UNDECLARED`, exit `3`:** a step targets a host outside
  `network.allow`. This is the product working. Report it; change the allow-list
  only if the person confirms that host is meant to be reached.
- **`prompt` steps** go through the model seam under the probe's own `cost`
  budget and need a configured adapter and `--allow-network` on
  `probe run`/`refresh`, which is egress and needs a yes. An `http` step needs no
  such flag; its declared hosts are the permission.

## 4. Store, run, baseline

```shell
adopt probe add ../orders-api-adopt/probes/checkout.yaml --json
adopt probe run --all --json
adopt probe baseline --set --json     # a person decides this behaviour is "normal"
```

`probe run` exits `1` only on `failure` or `blocked_by_manifest`. A run that
reached the system and observed something different still exits `0`: observing
change is the product doing its job. `baseline --set` versions the latest run
that observed something, **including a drifted one**. Check that the run's
outcome is `success` and that its response is right, and have the person agree,
before declaring it normal. A failed run (the service is down) can never become a
baseline. Say so and stop, rather than starting the service yourself: a baseline
of a process you launched describes your process, not theirs.

**Freeze the file once it is baselined.** Any edit, even to a comment, appends a
new probe revision. After that, `diff` reports `probe_changed` until the probe is
re-baselined. The first stored run also creates the probe's sensor, which has no
cadence. `adopt store doctor` then reports `FRESHNESS_SENSOR_DEGRADED`, and no
command sets a cadence in `0.4.1`. Report it as a known gap.

## 5. Later: what changed?

```shell
adopt probe run --all --json
adopt probe diff --json               # exit 0 clean, 4 on drift
```

- **Exit `4`** is drift against a named baseline: the command worked and found
  something. Report each step's verdict and similarity.
- **`probe_changed`** is not drift: the probe file was edited, so the question
  changed. `diff` compares only same-revision runs and gives both revision ids.
  Re-baseline after an edit, with a yes, rather than reading it as a change in
  the client's system.
- Where a drifted probe `exercises` an identity with confirmed knowledge, a
  `conflict` is written, and it appears in `adopt gaps` and in the pack. A
  conflict between what the docs say and what the system does is a deliverable,
  not noise.
- `adopt refresh` re-runs stored probes too, and turns drift into a `provider`
  change event. Use `--no-probes` when probe runs are not approved (see
  `adopt-watch`).

## When something refuses

<!-- BEGIN GENERATED: errors PROBE_,MANIFEST_ -->
| Code | Category | Exit |
|---|---|---|
| `MANIFEST_INVALID` | usage | `2` |
| `MANIFEST_MISSING_SAFE_PATH` | policy | `3` |
| `MANIFEST_UNDECLARED_HOST` | policy | `3` |
| `PROBE_BASELINE_MISSING` | usage | `2` |
| `PROBE_BUDGET_EXCEEDED` | policy | `3` |
| `PROBE_HOST_UNDECLARED` | policy | `3` |
<!-- END GENERATED -->
