# The adapter conformance suite

**This suite tests the adapter contract, not model quality** (AI spec §7.1). Every
assertion is about shape, ordering, counting or refusal — never about whether an
answer was *good*. That distinction is the whole design: a suite only a frontier
model could pass would quietly recreate the single-vendor dependency the seam
exists to prevent, and the gate would then be measuring the vendor rather than the
contract.

## Running it

```sh
uv run pytest tests/conformance                                   # fake_recorded only
uv run pytest tests/conformance --adapters=local_openai,fake_recorded
uv run pytest tests/conformance --adapters=local_openai,anthropic
```

`--adapters` is comma-separated and defaults to `fake_recorded`, so
`uv run pytest` in this repository runs all thirteen cases without a credential,
a container or a network — the same reason `tests/durability` defaults to
`--backend=inproc`.

A real adapter needs its configuration in the environment:

| Adapter | Needs |
|---|---|
| `fake_recorded` | nothing — replays the case's recorded turns from a fixture file |
| `local_openai` | `ADOPT_MODEL` and `ADOPT_ADAPTER_ENDPOINT` (an OpenAI-compatible endpoint) |
| `openai` / `anthropic` | `ADOPT_MODEL` and the provider credential; constructed **online**, because `04` §2 makes a hosted adapter unconstructible offline |

## Two rules that are not conventions

**A named adapter that cannot be reached FAILS. It never skips.** `conformance-matrix`
counts green adapters, and a skipped adapter is indistinguishable from a passing
one — so a suite that skipped its way to green would report the two-adapter rule
satisfied by a pipeline that exercised one. This is S2's reading of the escape
suite, restated: a skipped isolation test is not evidence of isolation.

**`fake_recorded` alone can never satisfy the gate**, and that is structural rather
than a matter of discipline. `AdapterKind` types it `test`, not `local`, and the
gate requires at least one **local** adapter green. If the fake counted as local, a
pipeline exercising no real model at all would pass the gate whose entire purpose
is to prove one was exercised. That typing is what makes it safe to be the default
here.

## Adding an adapter

1. Add the module under `packages/adopt-agent/src/adopt_agent/adapters/`, exposing
   `build(*, model, endpoint)`. It implements `api.Adapter` — one method wide,
   because everything the seam owns (budget, the schema retry, idempotency,
   tracing) must have no surface an adapter could own instead.
2. Add its row to `REGISTRY` in `adapters/base.py` with its `AdapterKind`. The kind
   decides whether the offline gate refuses it and whether it counts toward the
   gate's "at least one local".
3. **Do not add a vendor SDK.** Under CR-46 the hosted adapters speak HTTP over the
   standard library: the licence gate refused both official SDKs because their
   closure carries MPL-2.0 `certifi` and `tqdm` into the wheel, and `03` §7.3
   permits no copyleft `in-binary`. `no-provider-sdk` fires the moment anyone adds
   one, and that gate is watched failing on every CI run.
4. Add a price row to `prices.json` **only if the model is actually billed**. A
   locally served model is deliberately absent rather than listed at zero: it costs
   the operator's own hardware, and `None` is the honest answer to "what did that
   cost in dollars we can see".
5. Run the suite. Nothing in the thirteen cases should need changing — if a case
   needs a per-adapter branch, the contract has stopped being one contract and that
   is the finding, not the branch.

## What a failure means

| Case | A failure says |
|---|---|
| 1 | The adapter wrapped the provider's text — a JSON envelope, a markdown fence, a role prefix |
| 2 | A schema-validated result did not arrive parsed as a `dict` |
| 3 | The schema retry is not exactly one — zero, or unbounded |
| 4–6 | Tool calling is broken, unvalidated, or a caller's exception can take the process down |
| 7–8 | The budget is not enforced at the seam, or an abort discarded partial output |
| 9 | Cancellation does not stop an in-flight run, or does not record `abort` |
| 10 | Cost disagrees with the token counts, or with the price table |
| 11 | The trace cannot be audited inside a client environment without the provider |
| 12 | A replay called a provider — the idempotency guarantee (PRD F13.5) is gone |
| 13 | Payload text reached the trace or a log line — PRD N11, and a security-review finding |

Cases 4–6 need tool calling. **A candidate local model that fails them is a model
that does not support tools, which makes it not a candidate** — which is how PRD Q5
gets resolved rather than a reason to weaken the case.

## The unsatisfiable schema

Cases 3, 8 and 9 use a schema no JSON value can satisfy — `impossible` required,
`additionalProperties: false`, no properties declared. That is what makes them
deterministic against a real model: the retry is forced by the schema rather than
by hoping a model answers wrongly twice, which no prompt can guarantee and which
would make the cases flaky for a reason that has nothing to do with the contract.
