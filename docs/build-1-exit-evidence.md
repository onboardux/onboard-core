# Build 1 exit evidence — the numbers, their method, and what they do not license

**Date:** 2026-08-18 · **Sprint:** S1.8 · **Branch:** `build1/s1.1-write-path`
**Regenerate with:** `uv run python scripts/exit_evidence.py --json`,
`uv run python -m bench.map_soak --archive perf/soak/<date> --assert`,
`uv run python scripts/ratify_constants.py`

`03` §11 item 6 asks for three numbers: deterministic-plugin coverage per
archetype, glue rewrite rate, and move precision on the labelled rename corpus.
Two of the three are measured below. **The third is not, and the reason is a
missing credential rather than a missing implementation.**

Every figure here is produced by a command in this repository over a corpus in this
repository, and each section names the command. A number in this document that no
command produces is a defect in this document.

---

## 1. M2 — deterministic-plugin coverage per archetype

`uv run python scripts/exit_evidence.py --json`

| Archetype | Facts | **M2** as `01` §6 defines it | Non-model share | Labelled-set recall | Precision |
|---|---:|---:|---:|---:|---:|
| `web` | 354 | **0.791** | 0.992 | 1.000 | 1.000 |
| `ai` | 42 | **0.619** | 1.000 | 1.000 | 1.000 |
| `platform` | 17 | **1.000** | 1.000 | 1.000 | 1.000 |
| `lowcode` | 12 | **1.000** | 1.000 | 1.000 | 1.000 |
| `data` | 9 | **0.000** | 1.000 | 1.000 | 1.000 |

`MAP_PLUGIN_COVERAGE_FLOOR` is `0.60`.

### The ADR-0.1 reversal trigger did not fire, and saying so takes a paragraph

ADR-0.1's floor arm reads *"plugin coverage <60% on a new archetype"*. Read
literally against `01` §6 M2's predicate — `facts[method ∈ {grammar, reflection}] /
facts[*]` — it **fires on `data`**, which scored 0.000 while recovering 9 of 9
labelled identities, emitting zero spurious facts, running zero heuristics and
making **zero model calls**. A dbt project is YAML and SQL a person wrote; the
evidence rung is `declared`.

That is B1-CR-78, raised by S1.6 and deferred here rather than fixed by retuning.
**Ruled at S1.8 as B1-CR-90 (`docs/pack/OPEN-DECISIONS.md` OD-19):** ADR-0.1 asks
whether *deterministic plugins* are carrying an archetype or whether the agent is,
and `declared` evidence reaches no model. The trigger is read against the non-model
share — **1.000 on every archetype** — and `01` §6 M2's predicate is left exactly as
written. A metric edited to make a flag stop flipping is a metric nobody can trust
afterwards.

**One number here deserves watching rather than celebrating.** `ai` sits at 0.619,
**0.019 above the floor**. One additional declarative extractor in that pack would
put it below, on a pack whose extraction quality nobody had changed. That is a
property of the predicate, not of the pack, and it is the second piece of evidence
for OD-19's reading.

---

## 2. M4 — move precision on the labelled rename corpus

`uv run python scripts/move_eval.py --check`

| Measure | Result | Target |
|---|---:|---|
| Move precision | **1.000** | ≥ 0.95 (`01` §6 M4) |
| Recall over cases that should move | **1.000** | — |
| Declination accuracy | **1.000** | ambiguity must be declined, never guessed |

Ten hand-labelled cases in `fixtures/labeled/renames.json`: seven are behaviours
`01` F5 and `02` §10 C11 specify, three replay rename shapes taken from real commit
history in the soak corpus. **Precision and declination are scored separately and
never blended**, because a scorer that counted emitted moves alone would reward a
build that resolved every ambiguity by coin toss.

**Two things the corpus taught while it was being built**, both worth more than the
1.000:

- **A case was mislabelled, and the evaluator caught it.** *"Rename plus edit is not
  a move"* was labelled `no-move`, and the run emitted two moves — correctly. `01`
  F5's rule is per **referent**, not per file: a module that moved and gained a
  function still contains the same two functions at a new path. The corpus now
  carries both cases separately, and the mislabelled one is kept under a name that
  says so.
- **In 300 commits of `saleor`, git finds 11 renames and exactly one is a pure move
  (`R100`).** The other ten changed content in the same commit, so ten of eleven
  real renames are correctly *not* moves. **M4's numerator is a minority event in
  real code**, and a low move count is not by itself evidence of a broken mover.

---

## 3. M5 — glue rewrite rate

**Undefined. Not zero.**

`01` §6 M5 is `reviews[outcome='rewritten'] / reviews[outcome ≠ 'pending']` over
`.adopt/review_ledger.jsonl`. No ledger exists, because **the glue pass has never
called a model**: no `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or
`ADOPT_ADAPTER_ENDPOINT` exists in this environment. No module has been authored, so
none has been quarantined, so none has been reviewed.

`m5_rewrite_rate` returns `null` and never `0.0`. A build with no reviews has not
achieved a perfect rewrite rate; it has measured nothing, and the two are
distinguishable only if the instrument refuses to blend them.

**ADR-0.1's other arm is therefore unevaluated.** The floor arm is answered above;
the rewrite arm is open, and it stays open until somebody supplies a credential and
runs `05` S1.7's three remaining validation lines.

---

## 4. NFRs measured on real repositories — the G3 soak

`uv run python -m bench.map_soak --archive perf/soak/2026-08-18 --assert` ·
archived at [`perf/soak/2026-08-18/`](../perf/soak/2026-08-18/)

| Repository | Archetype | LoC | Files | Facts | Stage-1 | Total | Unchanged re-run | Peak RSS |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `saleor` | web | 930 095 | 4 552 | 27 269 | **51.0 s** | **238.8 s** | **195.6 s** | **301 MiB** |
| `langchain` | ai | 413 497 | 2 896 | 9 208 | **29.4 s** | **114.9 s** | **85.5 s** | **146 MiB** |
| `jaffle-shop-classic` | data | 440 | 18 | 5 | **4.4 s** | **7.8 s** | **6.4 s** | **56 MiB** |

| NFR | Budget | Worst observed | Verdict |
|---|---|---|---|
| **N1** stage-1 | `MAP_STAGE1_BUDGET_S` = 900 s | 51.0 s | **met**, 17× margin |
| **N2** total | `MAP_TOTAL_BUDGET_S` = 3600 s | 238.8 s | **met**, 15× margin |
| **N3** unchanged re-run | `MAP_INCREMENTAL_BUDGET_S` = 300 s, **zero revisions** | 195.6 s, **0 revisions on all three** | **met**, 1.5× margin |
| **N11** memory | `MAP_MAX_RSS_BYTES` = 2048 MiB | 301 MiB | **met**, 6.8× margin |

**Zero breaches.** `revisions_written` was `{identity: 0, knowledge: 0, binding: 0}`
on the second run of all three repositories — idempotence holding on an unmodified
930k-LoC tree, which is the strongest evidence this build has produced for its own
central claim.

### Four things that must be said beside those numbers

1. **This is a developer machine, not `bench/RUNNER.md`'s pinned runner** (Windows,
   12 cores, 8 workers). Every archived report states it. `01` N1–N3 and N11 are
   claims about a client repository on an engineer's own machine, which is what G3
   measures — but a different machine will produce different numbers, and the
   margins above are why that is currently tolerable rather than why it does not
   matter.
2. **N3's margin is the thin one.** 195.6 s against a 300 s ceiling on the largest
   repository — 1.5×, where the others are 15× and 17×. The re-run is not much
   cheaper than the first run because extraction re-runs in full and only the
   *write* is skipped. A repository three times the size of `saleor` would breach
   N3 while meeting N1 and N2 comfortably.
3. **The corpus is PRD Q4's stated fallback, not the founder's answer.** Q4 was due
   at S1.4 start and never ruled; `docs/pack/OPEN-DECISIONS.md` **OD-18** records
   the three permissive OSS repositories taken under it. `01` §11's standing claims
   discipline applies with full force — see §7 below.
4. **N4 is not proved by this soak; it is questioned by it.** See §5.

---

## 5. What the soak found — one defect, and the instrument that was missing

**`common.secrets` failed on the second of two runs over an unchanged `saleor`
checkout.** The first run minted a `config_key/secret:env/SECRET_KEY` identity; the
second reported `status: failed`, 0 facts, and the map came back one identity
smaller. Exit code **0** both times.

Three consequences, in order of how long each will outlive this sprint:

- **`01` N4 — *"identical URI set across repeated runs at fixed tool versions"* — is
  a CI hard gate that runs over fixtures.** This is the only instrument that has
  ever run the command twice over a 930k-LoC tree, and it disagreed. Not
  reproduced in two further attempts. Recorded as [`BACKLOG.md` **B-08**](../../BACKLOG.md).
- **The failure was undiagnosable from the only artefact that outlives the run.**
  `run_extractor` classifies every failure with a cause and `extractor_timings()`
  dropped it. Fixed: `detail` now reaches `run_report.json` (B1-CR-97, `02` §9.3).
- **A failed extractor was invisible everywhere a human looks.** The degradations
  block is the *ladder's* — a family that dropped a rung, not a plugin that raised.
  A run whose extractor threw printed a clean first screen and exited 0 while
  holding fewer identities than the run before it. Fixed: a third unnumbered
  callout on the first screen (`02` §9.1), emitted only when there is a failure.

**The build is exiting with a known intermittent defect, stated rather than
absorbed.** It is one identity in 27 269, and it is in the class of defect that
looks like nothing until somebody depends on the count.

---

## 6. Constants — 14 ratified, 7 not ratifiable here

`uv run python scripts/ratify_constants.py` ·
[`docs/pack/CONSTANT-RATIFICATION.md`](pack/CONSTANT-RATIFICATION.md)

`03` §3 puts twenty-one provisional values under an S1.8 ratification gate. **No
value was changed by the tool, and none was changed by hand**: a revision is a
clarification-register row with a decision and a date (`00` §9 rule 6).

The seven that could not be ratified here each name what would settle them:

| Constant | Blocked on |
|---|---|
| `MAP_CONF_AGENT_REVIEWED` | an adapter credential — no agent output has ever been reviewed |
| `MAP_GLUE_REWRITE_ALERT` | the same — M5 is undefined |
| `MAP_AGENT_SANDBOX_TIMEOUT_S` / `_MAX_BYTES` | `04` §8's E2 — the sandbox has never run an authored module |
| `MAP_STAGE1_REQUIRED_FAMILIES` | the **cold-FDE exercise** — a timed human |
| `MAP_FIRST_SCREEN_LIST_MAX` | the same |
| `MAP_TOOL_TIMEOUT_S` | a machine with `universal-ctags` installed |

**A note on the confidence bands.** `MAP_CONF_*` are ratified on their **ordering**
and on the fact that `MAP_CONF_REGEX` stays above `MAP_MIN_EMIT_CONFIDENCE` — so no
rung is silently unemittable. Their **magnitudes** are not evidenced by anything in
this build: no labelled set carries a confidence, so the corpus can say a method
recovered what it should have and cannot say that 0.95 rather than 0.93 is right
for a grammar hit. That is stated here rather than dressed as a measurement.

---

## 7. What none of this licenses anyone to claim

`01` §11 carries a standing claims discipline, and this section is it, applied.

**These numbers may not appear in a deck, a proposal or a security-review answer.**
Not yet, and not with a caveat. Specifically:

- **"`adopt map` maps a 930k-LoC repository in four minutes"** — on one machine, one
  repository, one archetype, once. `bench/RUNNER.md` rule 3's discipline applies:
  a number without a machine attached is an anecdote.
- **"Extraction recall is 100%"** — recall is **1.000 against hand-labelled sets
  this project wrote for fixtures this project built**. It says the extractors find
  what we told them to look for. It says nothing about what a client's system
  contains that nobody labelled.
- **"Idempotence is proven"** — proven on three repositories, and **contradicted
  once** by B-08 on the largest of them. The write path did not write; an extractor
  did not run. Both are true and the second is not yet understood.
- **"The agent pass is safe"** — the pass has **never called a model**. The gate,
  the audit, the sandbox and the quarantine are built, gated and tested against a
  stub. Every safety claim about behaviour under a real model is unevidenced.
- **"M2 clears the ADR-0.1 floor"** — under the reading ruled in OD-19, which is a
  decision taken by this build under a flagged default and **not yet ratified by
  the architect**. Under `01` §6's literal predicate, one archetype does not clear it.
- **"G3 is met"** — G3 is *a cold engineer reaches something useful in ≤ 1 hour*.
  The soak measures the clock. **Nobody has measured "useful"**, because that is
  the cold-FDE exercise and it requires a person: `docs/COLD-FDE-EXERCISE.md`.

**What the evidence does support**, stated as narrowly as it deserves: on three
permissive open-source repositories, on one developer machine, `adopt map` completed
inside every documented budget with a wide margin on three of four, wrote zero
revisions on an unchanged re-run, recovered every identity in five hand-labelled
sets, and refused all six attacks in the adversarial pass.

---

## 7a. The suite, and what the ratchet actually measured

| Selector | Tests | Time | Budget | Verdict |
|---|---:|---:|---:|---|
| `-m "not bench"` | 1636 | 720.2 s | 600 s | green, **over budget** |
| `-m "not bench and not e2e"` | 1589 | **494.6 s** | 600 s | green, **within budget** |

The 226-second difference is the six E2E journeys, and `03` §7's Gate column already
puts E2E journeys on `main` and integration per-PR. So the over-budget subject was
the gate's, not the suite's (B1-CR-99).

**No test was deleted.** `05` S1.8 asks for a pruning pass and the audit found
nothing redundant to remove: the journeys assert composition and artifacts, the
integration suite asserts seams, and neither re-asserts the other's arithmetic. The
suite grew from 1609 to 1636. A pruning pass that deleted good tests to make a
number is the failure that checkbox exists to prevent, so the honest outcome is an
audit with an empty result and the reason written down.

---

## 8. The build-level Definition of Done, item by item

`00` §11 binds by reference to `03` §11, eight items. **Checked sprint boxes do not
prove any of the eight**, so each is answered here against evidence rather than
arithmetic.

| # | Condition | Verdict |
|---|---|---|
| 1 | All thirteen features implemented behind their flags, acceptance signals demonstrated | **MET** — F1–F13 implemented; every acceptance signal has a named test, and the six journeys exercise them end to end |
| 2 | All seventeen NFRs **measured on the reference set and archived**, not asserted | **NOT MET** — N1, N2, N3, N4, N5, N6, N7, N8, N9, N11, N12, N16, N17 measured and archived. **N13** (deterministic-plugin coverage) is measured and its *reading* is an open decision (OD-19). **N14** (glue rewrite rate) is **undefined** — no review has ever been decided. **N10** is **measured as of 2026-08-19** -- `tests/durability/test_map_store_atomicity.py` kills a child with `os._exit` at thirteen statement boundaries inside `write_run` and asserts the store reopens, passes `PRAGMA integrity_check` and is byte-identical to its pre-write fingerprint; **it holds at every point**, with a positive control proving the uninterrupted write does change the store and a planted pre-kill commit turning 13 of 13 red. **N15** (release-artefact licence conformance) is still unmeasured, and is now *possible* since Build 0 DoD condition 6 closed and `v0.3.1` shipped |
| 3 | CI green including append-only, idempotence, environment-isolation, determinism and both lints | **MET 2026-08-19** — the three jobs B1-CR-98 added have now executed. Run [`32237536569`](https://github.com/onboardux/onboard-core/actions/runs/32237536569) on `build1/s1.1-write-path`: **23 green, 0 failed**, `build1-gates` running the four markers as four separate steps (13 · 15 · 7 · 5 tests). **Reaching it took five dispatches and surfaced seven defects that had never been exposed**, because the branch had never been pushed in eight sprints — among them a broken `pip install adopt-cli`, a vacuous end-to-end secret assertion, and this job list having no `timeout-minutes` at all. Two caveats stand: `constants-sync` and `error-registry-sync` **skip** on branch dispatches (they need `vars.ADOPT_PACK_REPOSITORY` and a token), so condition 8 is proven on `main` and locally rather than here; and `conformance-matrix` skips without credentials |
| 4 | The six E2E journeys pass | **MET** — `tests/e2e/test_map_cuj{1..6}.py`, 16 tests, green |
| 5 | The add-on §5 exit criteria all met | **MET, with one stated limit** — nine of nine ticked with a named test or archived report; *"the G3 hour holds on ≥3 real repos"* is ticked for the **clock** and explicitly not for *useful*, which is the cold-FDE exercise |
| 6 | Exit evidence produced: coverage per archetype, glue rewrite rate, move precision | **NOT MET** — M2 and M4 measured (§1, §2). **M5 is `null`** and stays so until a credential exists |
| 7 | Every clarification is a row in `00` §6 | **MET** — 95 rows; S1.8 added B1-CR-89 … B1-CR-99 |
| 8 | **Both sync gates enforce completeness, reporting zero pending** | **MET** — `constants_sync` and `error_registry_sync` both carry Build 1's documents with `enforce_completeness=True` and both report **zero pending**. This is the item that converts *specified* into *shipped* for both tables, and it is the last one deliberately |

**Five met, three not**, as of 2026-08-19 — conditions 1, 3, 4, 5, 7 and 8 are met,
which is six by the table's own rows, and **condition 5 is counted here as met with
its limit stated rather than as a sixth clean pass**: G3's *"useful"* half has not
been measured and cannot be by anything in this repository. Read the rows, not this
sentence — an earlier revision of this document said *"four met, four not"* while
the table above showed five, and a summary that disagrees with its own table is the
defect this build has spent eight sprints naming in other people's instruments.

The three that are not met name an **input** rather than a task:

| # | Blocked on | Who |
|---|---|---|
| 2 | **N10 has no instrument at all** — it must be written · N14 needs an adapter credential · N15 needs verifying against the shipped `v0.3.1` artefacts · N13's *reading* is OD-19 | engineering + owner |
| 6 | one adapter credential; M5 is `null` until a review is decided | owner |
| 2, 6 | — | — |

**A correction to the standing claim that "no implementation work is
outstanding": it was wrong, and the work has now been done.** `01` N10 —
*"kill at any statement boundary leaves the store openable and unchanged"* — had
no test, no drill and no harness anywhere in this build, while sitting inside a
**checked** feature box: `01` F3's acceptance signal ends *"a kill at any
statement boundary leaves the store clean"*. The only kill test in the repository
was Build 0's workflow drill, which names neither `adopt_map` nor `SurfaceWriter`.
The instrument now exists and N10 passes.

**What building it exposed about instruments generally.** The drill failed at
*every* kill point on its first run, including statement 1 where nothing had been
written — because `schema_meta` gains a row on **every `open_store`**, so the
parent changed the fingerprint by opening the file to measure it. The store was
correct and the instrument was not. That is the same shape as the six earlier
findings, arriving in the tool built to close the seventh.
