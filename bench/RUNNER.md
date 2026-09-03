# Reference runner — the hardware every performance number means

**Status: the public runner is ratified. PRD Q6 is CLOSED and all twelve Q4
constants were ratified on the public standard runner on 2026-08-12** (run
`31605693290`, recorded at the top of `bench/RATIFICATION.md` under *PUBLIC
RUNNER*). The pin table below still describes the **private** 2 vCPU / 7.8 GiB
machine and is kept as history; the ratified machine is 4 vCPU / 15 GiB and its
own table is in `RATIFICATION.md`.

**This header said "public release capture pending" until 2026-09-03**, six
months after the capture it was waiting for had happened, and the two documents
contradicted each other in the one place a reader goes to ask which numbers are
evidence. Corrected rather than rewritten: nothing below is deleted, and what is
genuinely still open is named in *What is still open*.

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
5. **The CI duration ratchets are judged on the reference runner, and nowhere
   else.** `CI_UNIT_MAX_MINUTES` and `CI_PR_MAX_MINUTES` are enforced by
   `scripts/ci_ratchet.py`, which has no notion of a runner and will happily
   fail on a laptop -- so this rule lives here, beside the machine the numbers
   mean. A developer-machine reading is **diagnostic**: useful for spotting a
   regression early, and by itself neither a gate result nor a plan deviation. A
   red local ratchet with a green CI ratchet is a statement about the laptop.

   Rule 1 already says this for `bench`; the ratchets were outside its scope
   because they are read from `ci.yml` rather than from `bench.yml`, and the
   omission cost three builds a re-litigation each (Build 6 ruled it
   2026-08-26; Builds 8 and 9 each reopened it as an owner call in their §14
   item 0). **Confirmed by the owner 2026-09-03 (OD-10)** and written down here
   so it stops being re-decided. No change to `ci_ratchet.py`: the rule is about
   *whose reading is authoritative*, and the script only ever sees one.

## What is still open

**What is open is a re-measurement, not a ratification.** PRD Q6 is closed and
all twelve Q4 constants are ratified on the public runner (`RATIFICATION.md`,
2026-08-12). What has not happened is a reading of those twelve on the **Builds
1-10 tree**: the nightly `bench` on `main` produces it once that stack merges,
and a threshold that breaches there is an explicit decision on the measured
evidence rather than a re-ratification of everything.

The two readings below are the **private** runner's and remain useful history;
rule 3 is why they are not release evidence:

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
