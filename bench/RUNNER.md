# Reference runner — the hardware every performance number means

**Status: private-runner capture retained; public release capture pending (CR-57,
2026-08-11).** The figures below are what the private repository's runner reported
about itself, captured by `.github/workflows/bench.yml` and recorded in
`bench/RATIFICATION.md`. They remain truthful history, but Q6 is reopened because
`adopt-core` becomes public before the final strict dry run. CR-57 requires a
fresh public evidence set for all twelve Q4 values; seven of those values are
owned by this benchmark runner and five are owned by other workflows.

A performance number without a machine attached is not a requirement, it is an
anecdote. Every constant in implementation spec §2.3 — `SCHEMA_CREATE_P95_SECONDS`,
`STORE_OPEN_P95_MS`, `EXPORT_P95_SECONDS`, `URI_BUILD_MIN_PER_SECOND`,
`COVERAGE_RECOMPUTE_P95_SECONDS`, `FRESHNESS_RESOLVE_P95_MS`, `CLI_COLD_START_MS`
— is a claim about *this* machine and no other.

## The pin

| Property | Value |
|---|---|
| Runner label | `ubuntu-24.04` (GitHub-hosted, standard class) |
| Runner image | `ubuntu24` `20260720.247.2` |
| Architecture | x86-64 (`INTEL(R) XEON(R) PLATINUM 8573C`) |
| vCPU | **2** |
| Memory | **7.8 GiB** |
| Storage | SSD-backed ephemeral workspace |
| Python | 3.12.3, from `.python-version`, provisioned by `uv` |
| Concurrency | The benchmark job runs alone. No other job shares the runner. |

**The first capture corrected this table.** It previously read 4 vCPU and 16 GB,
taken from the published description of the standard runner class. The machine
reports 2 vCPU and 7.8 GiB. That is the entire reason PRD Q6 existed, and it is
why "confirm against the actual runner" was a gate rather than a formality: every
constant would otherwise have been ratified against a machine twice the size of
the one that runs them.

> ### ⚠ The runner class is a function of repository visibility
>
> GitHub gives a **private** repository a 2-core / 7 GB standard runner and a
> **public** one a 4-core / 16 GB standard runner. `adopt-core` is private today
> and becomes public before the final strict `0.3.0` dry run (`01` §9, CR-57).
>
> **The visibility transition changes the reference machine**, and rule 3 below
> invalidates every runner-dependent performance ratification. After the
> repository is public, capture what the runner actually reports and collect
> fresh evidence for all twelve Q4 values before the tag. Do not pre-fill this
> file from GitHub's advertised 4-core / 16-GB class;
> the first private capture already proved why published specifications are not
> evidence. The connection between a repository setting and a performance gate
> is recorded here because it is invisible from either end.

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
   invalidates every runner-dependent ratification simultaneously; it is a
   measurement event, not a CI tweak.
4. **The benchmark job runs nightly and at release**, not on every pull request.
   Per-PR benchmarking on shared runners measures the neighbours.

## What is still open

**The public release machine is open.** PRD Q6 was closed against the private
capture and is reopened by CR-57. Keep the table above as historical evidence;
after visibility changes, append the actual public capture to
`bench/RATIFICATION.md` and then update this pin.

PRD Q4 is open for all twelve constants. The two readings below remain useful
history, but rule 3 prevents them from being release ratifications after the
runner-class transition:

`bench.all` supplies seven of those readings. Conformance duration, unit/PR CI
duration, the coverage floor, and binary size come from their owning workflows;
they must be linked in the same ratification record rather than attributed to
this benchmark job.

| Measurement | p95 | Budget |
|---|---|---|
| N1 schema create, SQLite | 0.735 s | `SCHEMA_CREATE_P95_SECONDS` = 10 s |
| N1 schema create, Postgres 16 | 0.932 s | same |

**How a harness knows it is here.** The workflow sets `ADOPT_BENCH_REFERENCE=1`,
and a harness asserts only when it sees it. That is an explicit signal rather
than something inferred from the environment: inferring it wrongly gives either a
gate that never fires or a gate that fires on the wrong hardware, and both are
worse than a switch the workflow sets on purpose.

The first benchmark harness landed with S1 (`bench/schema_bench.py`, asserting
`SCHEMA_CREATE_P95_SECONDS` across both dialects — SQLite in process, Postgres
through `psql` against the ephemeral `postgres:16` service).
