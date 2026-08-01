# Reference runner — the hardware every NFR number means

**Status: pinned, provisional.** Ratified at S1 exit (PRD Q6), together with the
twelve NFR gate constants at S9 exit (PRD Q4).

A performance number without a machine attached is not a requirement, it is an
anecdote. Every constant in implementation spec §2.3 — `SCHEMA_CREATE_P95_SECONDS`,
`STORE_OPEN_P95_MS`, `EXPORT_P95_SECONDS`, `URI_BUILD_MIN_PER_SECOND`,
`COVERAGE_RECOMPUTE_P95_SECONDS`, `FRESHNESS_RESOLVE_P95_MS`, `CLI_COLD_START_MS`
— is a claim about *this* machine and no other.

## The pin

| Property | Value |
|---|---|
| Runner label | `ubuntu-24.04` (GitHub-hosted, standard class) |
| Architecture | x86-64 |
| vCPU | 4 |
| Memory | 16 GB |
| Storage | SSD-backed ephemeral workspace |
| Python | 3.12, from `.python-version`, provisioned by `uv` |
| Concurrency | The benchmark job runs alone. No other job shares the runner. |

Larger runners, self-hosted runners and ARM runners are **not** the reference.
A benchmark green on a bigger machine tells us nothing about the constant.

## Rules

1. **Benchmarks assert only on the reference runner.** On any other machine
   `bench` reports and does not gate. A developer's laptop is not evidence.
2. **A constant is retuned, never a benchmark.** If a bench fails, the first
   question is whether the code regressed — not whether the number was
   optimistic. Changing the number is a decision with a date and an owner,
   recorded in `bench/RATIFICATION.md` at S9.
3. **The runner class changes only by a recorded decision.** Changing it
   invalidates every ratified constant simultaneously, so it is a re-ratification
   of all twelve, not a CI tweak.
4. **The benchmark job runs nightly and at release**, not on every pull request.
   Per-PR benchmarking on shared runners measures the neighbours.

## What is still open

The concrete vCPU, memory and storage figures above are the published
specification for the standard GitHub-hosted Linux runner class. **They must be
confirmed against the actual runner**, and that capture is now automated rather
than asked of a person: `.github/workflows/bench.yml` records `lscpu`, `free -h`,
`/etc/os-release` and the runner image identifiers alongside the N1 measurement
and uploads them as the `runner-profile` artifact. Committing that artifact as
`bench/RATIFICATION.md` closes PRD Q6.

Until that workflow has run, this file records an intent, not a measurement —
which is why the status line above still says provisional.

**How a harness knows it is here.** The workflow sets `ADOPT_BENCH_REFERENCE=1`,
and a harness asserts only when it sees it. That is an explicit signal rather
than something inferred from the environment: inferring it wrongly gives either a
gate that never fires or a gate that fires on the wrong hardware, and both are
worse than a switch the workflow sets on purpose.

The first benchmark harness landed with S1 (`bench/schema_bench.py`, asserting
`SCHEMA_CREATE_P95_SECONDS` across both dialects — SQLite in process, Postgres
through `psql` against the ephemeral `postgres:16` service).
