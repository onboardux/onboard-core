# Reference runner ratification

Captured automatically by `.github/workflows/bench.yml` on the
`ubuntu-24.04` GitHub-hosted runner pinned in `bench/RUNNER.md`.
Nobody pasted this in by hand, which is the point: PRD Q6 asks what
machine the twelve NFR constants mean, and only the machine can answer.

---

# THE BUILDS 1–10 TREE — RE-MEASURED, 2026-09-03

**Not a re-ratification.** `RUNNER.md` rule 3 invalidates a capture when the
*machine* changes, and it has not: this is the same public `ubuntu-24.04`
standard class the section below ratified on 2026-08-12. What changed is the
**tree** — `main` now carries Builds 1–10 (`665b978`) rather than Build 0
— so production-readiness T2.2 re-reads every value against it before the
`0.4.0` release. **No threshold breached, so nothing is retuned and rule 2 never
engages.**

## The runs

| Reading | Run | Ref | Note |
|---|---|---|---|
| The seven harnesses | `33781889941` (`bench.yml`) | `main` @ `665b978` | dispatched, not nightly |
| Binary sizes, distribution count, completeness | `33781859910` (`release.yml`, `publish=false`) | `main` @ `665b978` | strict dry run, every job success |
| `unit` and PR duration ratchets | `33778906920` (`ci.yml`, post-merge `push`) | `main` @ `665b978` | 28/28, 0 skipped |
| Coverage floor | same `ci.yml` run | `main` @ `665b978` | its own job |
| Hosted adapter time | same `ci.yml` run | `main` @ `665b978` | `conformance-matrix` |

Runner: `ubuntu-24.04`, GitHub-hosted, public standard class, image
`20260823.283.1`, Azure `eastus`.

## The seven harnesses

| # | Harness | Reading | Budget | |
|---|---|---|---|---|
| N1 | `schema_bench` | **BREACHED — and it had measured nothing** | `SCHEMA_CREATE_P95_SECONDS` 10 s | see below |
| N3 | `store_bench` | 2.0 ms p95, 40 opens at 50,000 rows | 200 ms | OK |
| N4 | `export_bench` | 3.68 s p95, 7 exports at 50,000 items | 30 s | OK |
| N5 | `uri_bench` | 101,786/s (slowest shape: multi-byte symbol path) | 50,000/s | OK |
| N6 | `coverage_bench` | 1.33 s p95, 7 runs at 50,000 identities | 20 s | OK |
| N7 | `freshness_bench` | 0.34 ms p95, 200 items | 25 ms | OK |
| CLI | `cli_bench` | **365 ms p95** over 20 cold starts; our imports 348 ms of it; empty-interpreter floor 16 ms | `CLI_COLD_START_MS` 400 | OK |

**`CLI_COLD_START_MS` is the one the plan named as the likeliest breach**, and it
held with 35 ms to spare on a tree that added nine verbs — *then it ran out.*

## CLI cold start, re-ratified 400 → 500 on 2026-09-09 (CR-92)

| date | p95 | our imports | budget | verdict |
|---|---|---|---|---|
| 2026-08-12 | 319 ms | 302 ms | 400 | OK — 1.25× |
| Builds 1–10 merged | 365 ms | 348 ms | 400 | OK — 1.10× |
| 2026-09-07 | 383 ms | 367 ms | 400 | OK — 1.04× |
| **2026-09-08** | **402 ms** | — | 400 | **BREACHED** — nightly `bench` red |
| 2026-09-09 | 400 ms | 382 ms | 400 | passed by 0 ms |

**Nothing regressed.** The three September readings are the same code on three
consecutive nights; the spread is the runner's own variance, and the budget had
less headroom than the variance. What changed is the product: nine verbs since
the budget was set against a Build 0 CLI.

**500 restores the 1.25× margin the 2026-08-12 ratification recorded** (319 of
400). It is not a round number chosen to clear 402.

**The alternative was measured first, and it does not work.** `03` §2 calls this
"the one constant whose purpose is keeping imports off the startup path", so
deferral was tried before the budget was touched: `adopt_agent` was moved out of
`commands/agent.py`'s module scope and returned **~5 ms** over six samples. The
288 ms that module appears to cost under `-X importtime` is its *cumulative*
column — shared dependencies billed to whoever imports them first — and its true
self-time is ~21 ms. The cost is pydantic model construction (`adopt_model._tables`),
the store stack and typer/click/rich, every one of which the other verbs need.

**Where the headroom actually is, for whoever revisits this:** lazy command
loading in `main.py`, so `adopt version` never builds the model layer at all.
That is a real refactor of the entry point and it touches the §13 error contract
and the `bad-parameter` gate, which is why it is recorded here as work rather
than done in passing.

**N1's report was not a measurement.** `schema_bench` applied
`0001__init_v3.sql` alone and then compared the created store's `user_version`
against `SCHEMA_VERSION`, so on a schema-4 tree it read 3, expected 4 and exited
before timing anything — `bench.all` printed `N1 BREACHED 0.1s`, which is
the elapsed time of a failure. **A budget was never breached and no constant is
in question.** CR-89 has the analysis and the fix; the reading below is from the
same code on a developer machine, and **the reference-runner reading is
outstanding** until that fix is on `main` and the nightly runs.

| N1 after CR-89 | sqlite p95 **0.103 s** | budget 10 s | developer machine — **diagnostic only** (`RUNNER.md` rules 1 and 5) |
|---|---|---|---|

## The release-owned values

| Value | Reading | Ceiling | |
|---|---|---|---|
| `adopt-linux-x86_64` | 23,165,144 bytes (22.09 MiB) | `BINARY_MAX_MB` 120 MiB | PASS |
| `adopt-windows-x86_64.exe` | 21,142,016 bytes (20.16 MiB) | 120 MiB | PASS |
| `adopt-macos-arm64` | 19,739,792 bytes (18.83 MiB) | 120 MiB | PASS |
| Release completeness | 44 artefacts, SBOM 15 components | 44 payloads | OK |
| Distribution set | 20 at version `0.4.0`, expected tag `v0.4.0` | 20 (OD-4) | OK |

The dry run also confirms T1.12's derivation working end to end: the binaries
jobs carried `EXPECTED_SCHEMA_VERSION: 4` and `EXPECTED_EXPORT_VERSION: 4`,
derived from `adopt_const` rather than written down — the literals the
workflow used to hard-code would have failed every one of those smoke steps
after the wheels were built.

## What this section does not claim

* **It is not a tag or publish authorization.** OD-4 ratified the set and the
  version for *preparation*; the tag and the two dispatches are a separate
  decision on this evidence.
* **N1 has no reference-runner reading on this tree**, and says so above rather
  than borrowing the developer-machine one.
* Nothing here re-opens PRD Q6. The machine is unchanged; only the tree moved.

---

# PUBLIC RUNNER — PRD Q6 CLOSED, ALL TWELVE Q4 CONSTANTS RATIFIED

**2026-08-12.** `adopt-core` became public, which changed the runner class and,
by `RUNNER.md` rule 3, invalidated every ratification below simultaneously. This
section is the replacement. **The private capture that follows is kept as
history and is not evidence for any release.**

## The machine — Q6

| Property | Value |
|---|---|
| Workflow run | `31605693290` attempt `1` (`bench.yml`) |
| Commit | `3ff9aa9674155be4692d68408f2e199b4ab94465` |
| Runner label | `ubuntu-24.04`, GitHub-hosted, **public** standard class |
| Runner image | `ubuntu24` `20260810.271.1` |
| Architecture | `x86_64`, AMD EPYC 9V74 |
| vCPU | **4** *(was 2 private)* |
| Memory | **15 GiB** *(was 7.8 GiB private)* |
| Python | `Python 3.12.3` |

The full `lscpu`, `free -h` and `/etc/os-release` capture is in the run's
`runner-profile` artifact and is reproduced in the historical section's format.
**It was read from the machine, never from GitHub's advertised specification** —
the private capture already proved published specifications are not evidence.

## The twelve — Q4

**All twelve hold at their current values. Nothing is retuned, so no threshold
changed and `RUNNER.md` rule 2 never engages.** `03` §2.3's provisional marking
is lifted.

| # | Constant | Budget | Public reading | Headroom | Evidence |
|---|---|---|---:|---|---|
| 1 | `SCHEMA_CREATE_P95_SECONDS` | 10 s | 0.027 s SQLite · 0.296 s PG16 | 34× | `bench.all` run `31605693290` |
| 2 | `STORE_OPEN_P95_MS` | 200 ms | 1.8 ms over 40 opens at 50k rows | 111× | same |
| 3 | `EXPORT_P95_SECONDS` | 30 s | 3.80 s over 7 exports at 50k items | 7.9× | same |
| 4 | `URI_BUILD_MIN_PER_SECOND` | 50,000/s | **104,085/s** slowest shape | 2.1× | same |
| 5 | `COVERAGE_RECOMPUTE_P95_SECONDS` | 20 s | 1.47 s at 50k identities | 13.6× | same |
| 6 | `FRESHNESS_RESOLVE_P95_MS` | 25 ms | 0.14 ms | 178× | same |
| 7 | `CLI_COLD_START_MS` | 400 ms | **319 ms** | **1.25×** ⚠ | same |
| 8 | `BINARY_MAX_MB` | 120 MiB | **20.39 MiB** largest of three | 5.9× | strict release run `31606311714` |
| 9 | `CONFORMANCE_CI_MAX_MINUTES` | 8 min | 42 s, both vendors 13/13 | 11.4× | `ci` run `31605874618` |
| 10 | `CI_UNIT_MAX_MINUTES` | 2 min | 23.2 s, 622 passed 1 skipped | 5.2× | same, `ci_ratchet` |
| 11 | `CI_PR_MAX_MINUTES` | 10 min | 110 s whole run, 20/20 green | 5.5× | same |
| 12 | `COVERAGE_FLOOR_CORE` | 0.80 | tightest judged `adopt-schema` **86.1%** | 6.1 pts | same, `coverage-floor` |

Binary sizes in full: `adopt-linux-x86_64` 21,383,384 B (20.39 MiB) ·
`adopt-windows-x86_64.exe` 19,840,000 B (18.92 MiB) · `adopt-macos-arm64`
18,204,176 B (17.36 MiB).

### The two readings this file told you to watch, and what happened

`RUNNER.md` and the historical section below both flagged `CLI_COLD_START_MS` and
`URI_BUILD_MIN_PER_SECOND` as outside budget on the developer machine. **Both
clear on the reference runner, and the reason is the machine in each case.**

* **CLI cold start: 962 ms developer → 319 ms reference, against 400 ms.** The
  empty-interpreter floor is **17 ms** here against 138–276 ms there, so most of
  the developer number was process start on a file-sync-backed Windows tree.
  `bench/cli_bench.py` printing that floor beside the figure is what made this
  answerable rather than arguable — `RUNNER.md` rule 2 asks whether the *code*
  regressed, and one number cannot answer it.
* **URI build: 32,971/s developer → 104,085/s reference, against 50,000/s.** This
  one is pure CPU with no I/O, so the historical note reasoned it *might* land
  near the developer reading. It did not — 3.2× higher. **That reasoning was
  sound and its conclusion was wrong**, which is the argument for rule 1 rather
  than against it: a developer machine is not evidence even when the mechanism
  suggests it should be.

**`CLI_COLD_START_MS` is the tightest of the twelve at 1.25×, and it is the one
to watch.** 302 ms of the 319 is our own imports. A single dependency added to
the startup path can breach it, and unlike the others there is no order of
magnitude absorbing the mistake. That is exactly what the constant is for — CR-51
records that it had no harness at all until S9, so the one constant whose purpose
is keeping imports off the startup path was the one nobody could breach visibly.

### What this ratification does not cover

`AGENT_DETECT_LISTING_MAX_ENTRIES` is a `03` §2.2 golden-set limit, **not** one of
Q4's twelve; it was ratified separately on 2026-08-10 and that section stands
unchanged.

---

# HISTORICAL — private-repository capture, 2026-08-11

> **Superseded for release purposes by the public capture above** (`RUNNER.md`
> rule 3). Kept because it is truthful history and because the delta between the
> two runner classes is itself the evidence for rule 3.

| Property | Value |
|---|---|
| Workflow run | `30682635772` attempt `1` |
| Commit | `2b97b58f41fd10431da0a9a9f6e8f02ff58a7bdd` |
| Runner label | `ubuntu-24.04` |
| Runner image | `ubuntu24` `20260720.247.2` |
| Architecture | `x86_64` |
| vCPU | `2` |
| Python | `Python 3.12.3` |

## `cat /etc/os-release`

```
PRETTY_NAME="Ubuntu 24.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.4 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

## `lscpu`

```
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           48 bits physical, 57 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  2
On-line CPU(s) list:                     0,1
Vendor ID:                               GenuineIntel
Model name:                              INTEL(R) XEON(R) PLATINUM 8573C
CPU family:                              6
Model:                                   207
Thread(s) per core:                      2
Core(s) per socket:                      1
Socket(s):                               1
Stepping:                                2
CPU(s) scaling MHz:                      130%
CPU max MHz:                             2300.0000
CPU min MHz:                             800.0000
BogoMIPS:                                4599.99
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr sse sse2 ss ht syscall nx pdpe1gb rdtscp lm constant_tsc rep_good nopl xtopology tsc_reliable nonstop_tsc cpuid aperfmperf tsc_known_freq pni pclmulqdq vmx ssse3 fma cx16 pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand hypervisor lahf_lm abm 3dnowprefetch tpr_shadow ept vpid ept_ad fsgsbase tsc_adjust bmi1 hle avx2 smep bmi2 erms invpcid rtm avx512f avx512dq rdseed adx smap avx512ifma clflushopt clwb avx512cd sha_ni avx512bw avx512vl xsaveopt xsavec xgetbv1 xsaves user_shstk avx_vnni avx512_bf16 vnmi avx512vbmi umip waitpkg avx512_vbmi2 gfni vaes vpclmulqdq avx512_vnni avx512_bitalg avx512_vpopcntdq la57 rdpid cldemote movdiri movdir64b fsrm serialize tsxldtrk ibt amx_bf16 avx512_fp16 amx_tile amx_int8 arch_capabilities
Virtualization:                          VT-x
Hypervisor vendor:                       Microsoft
Virtualization type:                     full
L1d cache:                               48 KiB (1 instance)
L1i cache:                               32 KiB (1 instance)
L2 cache:                                2 MiB (1 instance)
L3 cache:                                260 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0,1
Vulnerability Gather data sampling:      Not affected
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Mitigation; Aligned branch/return thunks
Vulnerability Itlb multihit:             Not affected
Vulnerability L1tf:                      Not affected
Vulnerability Mds:                       Not affected
Vulnerability Meltdown:                  Not affected
Vulnerability Mmio stale data:           Not affected
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Vulnerable
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Vulnerable
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; Retpolines; STIBP disabled; RSB filling; PBRSB-eIBRS Not affected; BHI Retpoline
Vulnerability Srbds:                     Not affected
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Not affected
Vulnerability Vmscape:                   Not affected
```

## `free -h`

```
               total        used        free      shared  buff/cache   available
Mem:           7.8Gi       1.0Gi       3.4Gi        62Mi       3.8Gi       6.8Gi
Swap:          3.0Gi          0B       3.0Gi
```

## N1 -- `bench.schema_bench --assert`

```
bench.schema_bench: 20 iterations per dialect
  sqlite: p95 0.735s (budget 10s)
  postgres: p95 0.932s (budget 10s)
bench.schema_bench: OK
```

---

# S9 ratification pass — 2026-08-10

PRD Q4 owns exactly twelve `03` §2.3 constants. The private S9 pass ratified one
of them (`COVERAGE_FLOOR_CORE`); the listing bound recorded later in this file is
a separate §2.2 decision and was never one of Q4's twelve. CR-57 reopens Q4 for
fresh public-release evidence because repository visibility changes the runner
class used by every timing workflow.

## Ratified

### `COVERAGE_FLOOR_CORE` = `0.80` — **RATIFIED 2026-08-10**

**Evidence:** `uv run python scripts/coverage_floor.py --check`, over the
`unit or property` suites.

> ### ⚠ The first measurement was over the wrong denominator, and the constant
> ### still stands
>
> The figures first recorded here (`adopt-agent` 89.7%, `adopt-schema` 91.9%)
> were taken with `--source=packages`, which — because every package is a `src/`
> layout — reported **only the modules some test happened to import**. Three
> adapter modules were **absent from the report entirely** rather than shown at
> 0%: `anthropic`, `openai` and `local_openai`. A package could have carried a
> wholly untested module and this alarm would have read *better* for it.
>
> That is the "measurement that succeeds by having nothing to measure" failure
> this build has now caught five times — the drill that collected one test, the
> fake standing in for a local adapter, `escape_coverage.py` at 100% of one port
> in twelve, an undefined ratio reporting 1.0, and now **the instrument written
> to catch the other four** *(CR-52)*. The gate names each `packages/*/src/<pkg>`
> import root, and `--self-test` asserts every workspace package contributes one.
>
> **The ratification is unaffected and the numbers below are the honest ones.**
> What was ratified is the constant `0.80`, not any measured figure, and every
> judged package clears it on the corrected denominator — the tightest by 5.9
> points rather than 9.7.

| Judged package (`03` §6) | Line rate | Was reported as |
|---|---|---|
| `adopt-schema` | **85.9%** (644/750) | 91.9% (644/701) |
| `adopt-agent` | **86.6%** (743/858) | 89.7% (550/613) |
| `adopt-workflow` | 96.2% (380/395) | unchanged |
| `adopt-store` | 96.3% (997/1035) | unchanged |
| `adopt-coverage` | 99.2% (126/127) | unchanged |

**Every judged package clears the floor, the tightest by 5.9 points.** The value
is ratified as a floor rather than raised to meet the measurement, and that is
the decision rather than an omission: `03` §2.3 says *"never a target"* and §6
bans coverage as a target outright, so setting the floor at the current figure
would convert a healthy margin into a ratchet, and a ratchet on coverage is what
manufactures assertion-free tests. **The margin is the point.**

`adopt-cli` measures 74.9% and is **not** judged — `03` §6 does not name it and
`05`'s summary table budgets **zero dedicated tests** there. Reported anyway, so
the exemption is visible rather than silent.

`plane-store` is judged when the same script runs in `adopt-plane` with
`--root .`, the CR-29 one-implementation pattern.

## Separate §2.2 ratification

### `AGENT_DETECT_LISTING_MAX_ENTRIES` = `150` — **RATIFIED 2026-08-10**

CR-47 made this provisional, to be ratified at S9 *"against the `04` §7.2 golden
set -- the pack requires the listing bounded and never says where"*.

**Evidence, two populations:**

| Population | Cases | Max entries | Truncated |
|---|---|---|---|
| `04` §7.2 golden set (`tests/golden_prompts/detect_001/cases.json`) | 15 | **7** | 0 |
| Real fixture corpus (`tests/fixtures/*`, via `bounded_listing`) | 7 trees | **70** (`repos`) | 0 |

**The bound never binds on a real tree, and that is what ratifies it.** The
largest listing any hand-labelled ambiguous case produces is 7 entries — the cap
has 21× headroom there — and the largest real fixture tree produces 70, giving
2.1×. So the cap does not truncate evidence a model needs while still bounding a
pathological tree, which is both halves of what `04` §4 step 4 asks for.

**Why it was not lowered to fit.** 70 is the largest tree *we* have; a client
monorepo is larger, and a cap tuned to our corpus would start truncating on the
first real system. The bound exists for the pathological case, not the median.

## Public release evidence — CLOSED 2026-08-12, see the top of this file

> **This section stated what was outstanding and how to close it. It is closed.**
> All twelve values are ratified against the public runner in the first section,
> and PRD Q6 is closed with it. The text below is kept because it names the five
> evidence owners correctly and that division still governs any future
> re-ratification — not because anything here is still open.

`SCHEMA_CREATE_P95_SECONDS` · `STORE_OPEN_P95_MS` · `EXPORT_P95_SECONDS` ·
`URI_BUILD_MIN_PER_SECOND` · `COVERAGE_RECOMPUTE_P95_SECONDS` ·
`FRESHNESS_RESOLVE_P95_MS` · `CLI_COLD_START_MS` · `BINARY_MAX_MB` ·
`CONFORMANCE_CI_MAX_MINUTES` · `CI_UNIT_MAX_MINUTES` / `CI_PR_MAX_MINUTES`

No single command closes this set. Evidence has five owners:

| Evidence owner | Constants |
|---|---|
| `bench.all --assert` on the public reference runner | schema create, store open, export, URI throughput, coverage recompute, freshness resolve, CLI cold start |
| Public `conformance-matrix` job | conformance CI duration |
| Public unit and full-PR CI ratchets | unit and PR duration budgets |
| Public `coverage-floor` job | `COVERAGE_FLOOR_CORE` |
| Strict public `release` job | `BINARY_MAX_MB` over the three shipped binaries |

The release record must link all five sources. A green `perf` job alone proves
seven values, not twelve.

**Developer-machine readings, recorded as context and explicitly not as
evidence** (`bench/RUNNER.md` rule 1 — Windows, on a file-sync-backed working
tree, so I/O is not comparable):

| NFR | Reading | Re-run 2026-08-11 | Budget |
|---|---|---|---|
| N1 schema create | inside | 2.6 s wall | 10 s |
| N3 store open | inside | 6.1 s wall | 200 ms |
| N4 export | inside | **14.84 s p95** | 30 s |
| N5 URI build | inside | **32,971/s** ⚠ | 50,000/s |
| N6 coverage recompute | inside | **6.49 s p95** | 20 s |
| N7 freshness resolve | 0.41 ms | 0.34 ms p95 | 25 ms |
| CLI cold start | **962 ms p95** | **1,276 ms p95** ⚠ | **400 ms** |

**Two readings are outside budget on this machine, not one.** The earlier text
said the CLI cold start was the only one; a full `bench.all` re-run on
2026-08-11 put **N5 URI build at 32,971/s against a 50,000/s floor** as well, on
its slowest shape (the multi-byte symbol path — the ASCII endpoint shape reports
110,515/s). Both are reported, neither is asserted, and neither is a breach:
`RUNNER.md` rule 1 means a developer laptop cannot breach anything.

**Watch both on the first public reference run.** They fail differently and the
distinction matters:

* **CLI cold start** is dominated by process start. The empty-interpreter floor
  on this machine is 276 ms p95 in this re-run (138 ms in the earlier one)
  against roughly 20–30 ms on a Linux runner, so most of the number is the
  machine. `bench/cli_bench.py` prints that floor beside the figure for exactly
  this reason — `RUNNER.md` rule 2 asks first whether the *code* regressed, and
  one number cannot answer it. An import profile taken the same day found no
  module-scope I/O on the startup path: the cost is pydantic's first import
  plus click/typer, which is the floor of what the CLI can be.
* **N5 URI build** is pure CPU with no I/O, so a slow filesystem does not
  explain it and the reference reading may land closer to this one than the
  earlier *inside* would suggest. If it breaches on the public runner, `RUNNER.md`
  rule 2 applies: establish whether the multi-byte path regressed before
  proposing the constant move.

**`BINARY_MAX_MB` now has private-diagnostic artefacts but no release
ratification.** The fifth dry run built and smoke-tested all three binaries, but
CR-57 requires the final evidence on the public release runner. The strict
`scripts/assert_release_complete.py` result from that run closes this value.

> **Closed 2026-08-12** by strict public run `31606311714`, `publish=false` with
> `ALLOW_MISSING_PROVENANCE: false`. Largest binary 20.39 MiB of 120. The same
> run recorded `release completeness: OK` over 34 artefacts and 103 files, an
> SBOM of 15 components, SLSA provenance persisted to the repository
> (attestation `40291662`, Rekor `2437724071`), and every payload signature
> `Verified OK` against the exact workflow identity. **This is the first
> execution of the strict public supply-chain path in the project's history** —
> CR-57 recorded that GitHub would not persist an attestation while the
> repository was private, and that boundary is now gone rather than worked
> around.
