# Reference runner ratification

Captured automatically by `.github/workflows/bench.yml` on the
`ubuntu-24.04` GitHub-hosted runner pinned in `bench/RUNNER.md`.
Nobody pasted this in by hand, which is the point: PRD Q6 asks what
machine the twelve NFR constants mean, and only the machine can answer.

> **CR-57 transition — 2026-08-11.** This is the truthful private-repository
> capture and is not overwritten. `adopt-core` becomes public before the final
> strict release dry run, which changes the reference runner class and reopens
> Q6. Append the actual public-runner capture and re-ratify all twelve constants
> after that transition; do not infer its values from GitHub's advertised class.

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

## Public release evidence still open — all twelve Q4 constants

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
evidence** (`bench/RUNNER.md` rule 1 — Windows, OneDrive-synced tree, so I/O is
not comparable):

| NFR | Reading | Budget |
|---|---|---|
| N1 schema create | inside | 10 s |
| N3 store open | inside | 200 ms |
| N4 export | inside | 30 s |
| N5 URI build | inside | 50,000/s |
| N6 coverage recompute | inside | 20 s |
| N7 freshness resolve | 0.41 ms | 25 ms |
| CLI cold start | **962 ms p95** | **400 ms** |

**The CLI reading is over budget here and is not a breach.** The empty-interpreter
floor on this machine is 138 ms p95 against roughly 20–30 ms on a Linux runner,
so the measurement is dominated by process start rather than by our imports.
`bench/cli_bench.py` reports that floor beside the figure for exactly this
reason — `RUNNER.md` rule 2 says the first question on a failure is whether the
*code* regressed, and one number cannot answer it. **This is the first constant
to check on the first reference run**, and it is the only one whose developer
reading is outside budget.

**`BINARY_MAX_MB` now has private-diagnostic artefacts but no release
ratification.** The fifth dry run built and smoke-tested all three binaries, but
CR-57 requires the final evidence on the public release runner. The strict
`scripts/assert_release_complete.py` result from that run closes this value.
