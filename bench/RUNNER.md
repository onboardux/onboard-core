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

## The label is a pool, not a machine *(N49, 2026-09-24)*

`ubuntu-24.04` names an image and a size, and GitHub fills it from whatever
hardware it has. The thirty nightly `bench` runs from 2026-08-30 to 2026-09-24
landed on **six** CPU models: AMD EPYC 7763, 9V74 and 9V45; Intel Xeon Platinum
8573C and 8370C; and, first seen on 2026-09-24, Intel Xeon 6973P-C. Nobody
recorded a decision to change any of them, so rule 3 cannot hold by itself: the
class it guards moves under the label.

For six of the seven budgets that has not mattered. For **N3 it does**, because
every store open commits a `schema_meta` row and so pays for an `fsync`, and a
disk's `fsync` latency varies far more across hosts than a CPU's speed does:

| Run | Commit | Host CPU | N3 p95 |
|---|---|---|---|
| `35838133142` (09-23) | `e284dc2` | AMD EPYC 7763 | 2.4 ms |
| `35975553123` attempt 1 (09-24) | `e284dc2` | Intel Xeon 6973P-C | **225.8 ms — breached** |
| `35975553123` attempt 2 (09-24) | `e284dc2` | AMD EPYC 7763 | 2.5 ms |
| `34327704754` (09-09) | `fbc3e3a` | Intel Xeon Platinum 8573C | 65.5 ms |

Same commit, 90× apart. Rule 2's first question has a clear answer, and it is
**no, the code did not regress**. The harness could not say so at the time,
because it printed one number.

### OD-17, ruled 2026-09-25: N3 is judged net of the disk

This is a change to what a benchmark measures, which rule 2 reserves for the
owner. The owner handed OD-17 to the implementing session, which chose this
option.

**The open's disk work was counted, not assumed.** Under `strace` on Linux
(SQLite 3.46), every timed open makes **three** syncs: `fdatasync` of the new
WAL's header, of the directory it was just created in, and of the WAL at
commit. Its `close()`, which is outside the timer, makes two more. All five come
from SQLite making one row durable. None is this repository's choice.

**So each open is now paired with a floor sample.** Bare SQLite commits one page
into a fresh WAL in the same directory. That is three syncs as well, checked the
same way, and the floor sample's own `close()` is also untimed. The samples
alternate, so a stall that slows the opens also slows the floors beside them.
**N3 fails when p95(open) − p95(floor) exceeds `STORE_OPEN_P95_MS`.** The budget
itself is unchanged.

| Case | Raw p95 | Floor p95 | Net | Verdict |
|---|---|---|---|---|
| This tree, laptop | 12.3 ms | 7.7 ms | 4.6 ms | PASS |
| **Planted code regression:** +250 ms inside every open | 262.5 ms | 7.7 ms | 254.7 ms | **FAIL** |
| **Planted slow disk:** +250 ms inside every open *and* every floor sample | 257.7 ms | 254.7 ms | 3.1 ms | PASS, and the raw breach is printed |

**Why this option over the other three.**
- *Keep failing and triage* leaves a nightly red that people learn to re-run.
- *Re-measure once* clears a transient stall, but not a host whose disk is slow
  on every sample.
- *Self-hosted hardware* is infrastructure nobody has provisioned.

Net-of-floor handles both the transient case and the persistent one. It loses
nothing the gate could already see: on every host that has never breached, the
floor is a few milliseconds at most. A change that adds durable I/O to an open
still counts, because the floor subtracts one commit's syncs and no more.

**Reversal triggers.**
- If `bench` moves to hardware that is actually fixed, the raw p95 means one
  machine again and can be judged directly.
- If SQLite's sync pattern for this commit changes, re-count both sides. The
  floor is SQLite itself, so it should follow on its own, but that is an
  expectation, not a measurement.

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
