# Reference runner — the hardware every NFR number means

**Status: measured and ratified (PRD Q6, 2026-08-02).** The figures below are no
longer a published specification copied from documentation — they are what the
runner reported about itself, captured by `.github/workflows/bench.yml` and
recorded in `bench/RATIFICATION.md`. The twelve NFR gate constants themselves
remain **provisional** until S9 exit (PRD Q4).

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
> and is planned to go public at the `0.3.0` tag (`01` §9).
>
> **Publishing therefore doubles the reference machine**, and rule 3 below says
> that invalidates every ratified constant at once. This is not a reason to stay
> private; it is a reason that the S9 ratification must happen on whichever
> visibility the release ships with, and that flipping visibility after S9 is a
> re-ratification rather than a settings change. Recorded here because the
> connection between a repository setting and a performance gate is invisible
> from either end.

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

**Nothing about the machine.** PRD Q6 is closed: `bench/RATIFICATION.md` holds the
capture, and the table above was corrected to match it.

What remains open is PRD Q4 — the twelve NFR gate constants are still provisional
and are ratified at S9 exit against measurements on this machine. The first two
readings, both far inside budget:

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
