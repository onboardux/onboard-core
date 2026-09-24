<!-- GENERATED FILE -- do not edit by hand.
     Regenerate: cd adopt-core && uv run python scripts/gen_skills.py
     Verify:     cd adopt-core && uv run python scripts/gen_skills.py --check -->

# Command reference — adopt 0.4.1

Every command of the CLI these skills were generated against: **44 runnable commands**. Command names, flags, JSON keys and exit codes are additive-only from `0.3.0`, so a newer CLI still has everything here. Root options go **before** the command: `--allow-network`.

## Index

| Command | What it does |
|---|---|
| `adopt agent adapters` | List every registered adapter and why each unusable one is unusable. Exits `0` even when nothing is available: the report is the answer. |
| `adopt agent check` | Construct one adapter, or refuse with the reason. Raises `AGENT_ADAPTER_UNKNOWN` (usage, exit `2`) for an unregistered id or when none is configured, and `AGENT_OFFLINE_ADAPTER_DENIED` (policy, exit `3`) for a hosted adapter offline. The envelope and the exit code are `adopt_cli.main`'s doing, so this command does not restate the mapping. |
| `adopt answer` | Bank a human's answer as confirmed knowledge and resolve the question. |
| `adopt ask` | Answer from the store: KNOWN with citations, STALE with the cause, or UNKNOWN. |
| `adopt bind` | Bind a knowledge item to an identity by hand. For the links no heuristic finds. A binding made here is human-justified by construction, which is the same standing a confirmed suggestion has. |
| `adopt boundary` | Negotiate the observability boundary and report what it permits. |
| `adopt ci-sense` | Observe this repository and post the observation to the control plane. |
| `adopt coverage recompute` | Evaluate the six inputs of contracts §6 for every identity in scope. **`--scope` exists because this command is the remedy for an alarm, and the remedy has to be reachable.** `COVERAGE_CACHE_DISAGREEMENT` fires on every `adopt gaps` and `adopt pack` once a binding is confirmed, and clearing it needs this verb -- which took a system **id** that no verb printed. `store info` reports counts, `store doctor` reports findings, and the only command that ever surfaced a system id was `adopt handover start`, which opens a handover event as a side effect. An operator was left reading the SQLite file by hand to silence an alarm the product raised at them, so the scope string they typed into `adopt init` is now accepted here too. |
| `adopt detect` | Classify a file tree into one archetype, or refuse and rank. Exits `2` with `DETECT_AMBIGUOUS` when confidence is below the threshold -- including when a disambiguation proposal was obtained, because a proposal is not a decision. |
| `adopt doctor` | Report every configuration key with its resolved value and source. Exits `4` when there are findings: degraded success, not failure. `doctor` never repairs what it reports. |
| `adopt draft` | Draft one handover section from what the store knows about an identity. Grounded strictly on store facts: the identity's attributes, where it was observed, and any knowledge already bound to it. A draft that cites none of them is discarded and nothing is written. What survives lands as **unverified** knowledge for `adopt review`. |
| `adopt envelope validate` | Validate an outbound envelope against the boundary. Exits `3` on a violation. |
| `adopt export` | Write a portable bundle of every exportable table. |
| `adopt freshness resolve` | Resolve one item's freshness and report the rule that decided it. |
| `adopt gaps` | Identities minus covered knowledge, ranked -- the elicitation queue. With no flag it lists. With one disposition flag it records what a human decided about a single gap and lists the result. **Open conflicts are listed alongside the gaps**, and they are a different thing: a gap is knowledge nobody has written, a conflict is knowledge somebody confirmed that a probe has since seen contradicted. Both belong in the same queue because both are work, and separating them would let a contradiction sit unread behind a heading nobody opened. **Existence stays derived, always.** `recompute_coverage` is the authority on whether an identity is uncovered, and nothing here writes its cache. A disposition row says only what someone decided to do; the report is the join of the two, so a gap that recompute no longer derives disappears from the listing whatever its disposition says (v6.1 §6 Build 4). |
| `adopt handover close` | Step 6 -- transfer ownership, and transfer what is still open with it. Refuses to leave the system unowned, and the refusal rolls the whole close back. Nothing here resolves a gap, answers a question or clears a conflict: unresolved items transfer with named owners rather than being closed to look complete. |
| `adopt handover elicit` | Step 2 -- the open gaps, as questions, grouped by the owner who answers them. This is where the saving lives: the agenda replaces the broad knowledge-transfer interview. An event that runs a general interview anyway has not used the product. |
| `adopt handover pack` | Step 3 -- one pack per audience, every section stamped and dated. The same assembler `adopt pack` runs, through the same shared functions, so the document a client receives is the one the FDE reviewed. Drafting is deliberately **not** here: `adopt pack --draft-missing` and `adopt review` run before this step, so what the handover emits is what a human has already confirmed or explicitly left stamped UNVERIFIED. |
| `adopt handover snapshot` | Step 5 -- the acceptance snapshot: an export bundle and a recorded digest. Both parties hold it, and the client can **check** theirs: `adopt import` then `adopt export` on their own machine reproduces the same digest, because it is computed over the bundle's byte-stable table digests and nothing that varies between exports. |
| `adopt handover start` | Step 1 -- freeze the scope and record the opening position. Refuses a system that already has an un-closed handover: continuing the open one is almost always what was meant, and two records of one transfer is the fork a resumable checklist must not silently make. |
| `adopt handover status` | Every step recorded, what comes next, and what is still open. Reads only, and answers for a closed handover as readily as an open one -- the record is what both parties keep, and it must still describe itself a year later. |
| `adopt handover verify` | Step 4 -- record what the receiving team could and could not do. Every failed task becomes an **open question** carrying its text, answerable with `adopt answer` in the room. Failures are findings about the pack, not marks against people, and nothing here records who failed anything. Exits `4` when any task failed: the command worked and found something. |
| `adopt harvest` | Mine local git history into decision candidates -- unverified, with evidence. |
| `adopt identity build` | Build the canonical URI for a referent. |
| `adopt identity parse` | Parse a URI into its seven segments. |
| `adopt identity validate` | Check a URI against the grammar and canonical form. Exits 2 if it is not. |
| `adopt import` | Verify a bundle whole, then restore it into an empty store. |
| `adopt ingest` | Turn documents into knowledge, bound to the identities they refer to. |
| `adopt init` | Create a store, resolve a scope, detect the archetype and declare the boundary. |
| `adopt map` | Extract identities from a repository, deterministically and offline. |
| `adopt pack` | Assemble an audience-scoped handover pack from the store. Sections select **confirmed** knowledge by audience and kind; the map contributes the inventory; the coverage join contributes the gap appendix, and any open conflict a probe recorded is listed inside it; Build 0's observability boundary is embedded so the pack states its own limits. Every section carries its verification status and date. The Markdown is byte-stable given the same revisions -- no clock reaches it, so running this twice over an unchanged store produces identical files. With `--draft-missing`, uncovered identities are drafted first and render under UNVERIFIED banners until a human confirms them. |
| `adopt probe add` | Validate a probe file and store it as a definition plus a revision. |
| `adopt probe baseline` | Version how each probe's system behaves today. `--set` takes each active probe's latest run that observed something and writes it as a `baseline_version`. A run that failed or was refused by the manifest is never eligible: versioning a fault as *how the system behaves* would make the next clean run read as drift away from a bug. A run whose outcome was `diff` **is** eligible, and the report says so. That is what re-baselining after drift is -- a human accepting a change -- and it is deliberately not silent. |
| `adopt probe diff` | Name what changed since the baseline -- and who changed it. Exits `4` when any probe drifted: degraded-with-findings, the same contract `adopt doctor` and `adopt map --check-expected` already use. The command **worked**; it found something a human must see. A probe whose latest run is of a different revision than its baseline reports `probe_changed` and never drift. The probe file was edited, so the question changed -- and calling that a change in the client's system is the one mistake that would train an FDE to ignore this command. |
| `adopt probe manifest validate` | Validate a probe capability manifest. Exits `3` on a violation. |
| `adopt probe run` | Execute probes, recording what each one observed. Exits `1` when any probe failed or was blocked, so a CI step running probes fails the build rather than reporting green over a refusal. |
| `adopt pull` | Refresh this store from the control plane it replicates. |
| `adopt refresh` | Re-map the repository, classify what changed, and queue the review. |
| `adopt review` | The one review queue: harvest candidates, suggested bindings and changes. With no flag it lists. With one it resolves. What confirming *does* depends on which population the item belongs to -- bindings for a suggestion, a verified revision for a candidate -- and `adopt_knowledge.review` is where that rule lives and is documented. **`--resolve --action` is the change population's separate door, and the separation is deliberate.** `--confirm` on a suggestion answers "is this document about this identity?"; the three change actions answer "the referent moved -- what should this note do about it?". One flag meaning both is how a reviewer presses a button for one reason and gets a second thing they never looked at, which is the failure the two-populations rule in `adopt_knowledge.review` exists to prevent. |
| `adopt serve` | Answer questions over loopback HTTP: `POST /ask`, `GET /healthz`. |
| `adopt store doctor` | Report every finding in the store, and change nothing. Exit `4` with findings -- degraded success, not failure. The store opened, the checks ran, and their answers are trustworthy; what needs a human is what they found. |
| `adopt store info` | Schema version and row counts. Reads; never writes. |
| `adopt store migrate` | Apply pending forward migrations, then report the resulting version. Forward-only, always: implementation spec §7.4 states the schema has no rollback, and recovery is older code against a newer store. |
| `adopt version` | Report the binary, schema and export versions and the build provenance. |

## `adopt agent adapters`

List every registered adapter and why each unusable one is unusable. Exits `0` even when nothing is available: the report is the answer.

```shell
adopt agent adapters [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt agent check`

Construct one adapter, or refuse with the reason. Raises `AGENT_ADAPTER_UNKNOWN` (usage, exit `2`) for an unregistered id or when none is configured, and `AGENT_OFFLINE_ADAPTER_DENIED` (policy, exit `3`) for a hosted adapter offline. The envelope and the exit code are `adopt_cli.main`'s doing, so this command does not restate the mapping.

```shell
adopt agent check [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--adapter` | str | — | Adapter id to check. Defaults to the resolved ADOPT_ADAPTER. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt answer`

Bank a human's answer as confirmed knowledge and resolve the question.

```shell
adopt answer ESCALATION_ID [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `ESCALATION_ID` | str | **required** | The question id `adopt ask --escalate` printed. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--text` | str | **required** | The answer, in plain language. |
| `--uri` | str | — | Bind the answer to this identity. Repeatable. |
| `--title` | str | — | Item title. Defaults to the question that was asked. |
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--store` | path | — | Store path override. |
| `--audience` | str | — | Who the answer is for. Repeatable. Defaults to the same audience `adopt ingest` gives an untagged document. |
| `--actor` | str | — | Who answered. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt ask`

Answer from the store: KNOWN with citations, STALE with the cause, or UNKNOWN.

```shell
adopt ask QUESTION [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `QUESTION` | str | **required** | The question, in plain language. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--store` | path | — | Store path override. |
| `--reindex` | boolean | `false` | Rebuild the retrieval index before answering, even if it looks current. |
| `--escalate` | boolean | `false` | Record an unanswered or stale question as open, storing its text. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt bind`

Bind a knowledge item to an identity by hand. For the links no heuristic finds. A binding made here is human-justified by construction, which is the same standing a confirmed suggestion has.

```shell
adopt bind KNOWLEDGE_ID URI [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `KNOWLEDGE_ID` | str | **required** | The knowledge item id. |
| `URI` | str | **required** | The identity URI to bind to. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--not-load-bearing` | boolean | `false` | A change to this identity does not stale the item. The default is load-bearing, so a caller who says nothing errs toward staleness. |
| `--actor` | str | — | Who is running this. Recorded on every revision written. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt boundary`

Negotiate the observability boundary and report what it permits.

```shell
adopt boundary [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--answers` | path | **required** | JSON file carrying the three qualification answers (artifact_access, deploy_signal, safe_interaction). |
| `--scope` | str | — | firm/engagement/system, optionally with /environment. Given, the boundary is written to the store; omitted, it is computed and reported without writing. |
| `--archetype` | str | — | The detected archetype, for the ai-below-T3 floor check. |
| `--store` | path | — | Store path override. |
| `--write-statement` | path | — | Also write the human-readable statement here. Same row, same facts. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt ci-sense`

Observe this repository and post the observation to the control plane.

```shell
adopt ci-sense [PATH] [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `PATH` | path | `.` | Repository root to observe. Defaults to the working directory. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--packs` | str | — | Comma-separated pack names, overriding archetype selection. |
| `--cadence-hours` | int | — | How often this pipeline is expected to report. Silence beyond it degrades freshness for the scope. Defaults to CI_SENSE_DEFAULT_CADENCE_HOURS. |
| `--strict` | boolean | `false` | Exit 4 when the plane records findings. Off by default: a sensing step that fails a deploy over stale knowledge is a step somebody deletes. |
| `--run-id` | str | — | Idempotency key. Reuse it across retries of one pipeline run so a retry records liveness without double-writing. Generated when absent. |
| `--no-probes` | boolean | `false` | Skip the probe half. The artifact observation is still posted; nothing opens a socket toward the client's system. |
| `--allow-network` | boolean | `false` | Permit the model adapter for a probe's `prompt` steps. An `http` step needs no such flag -- the manifest's host allow-list is the invariant there. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt coverage recompute`

Evaluate the six inputs of contracts §6 for every identity in scope. **`--scope` exists because this command is the remedy for an alarm, and the remedy has to be reachable.** `COVERAGE_CACHE_DISAGREEMENT` fires on every `adopt gaps` and `adopt pack` once a binding is confirmed, and clearing it needs this verb -- which took a system **id** that no verb printed. `store info` reports counts, `store doctor` reports findings, and the only command that ever surfaced a system id was `adopt handover start`, which opens a handover event as a side effect. An operator was left reading the SQLite file by hand to silence an alarm the product raised at them, so the scope string they typed into `adopt init` is now accepted here too.

```shell
adopt coverage recompute [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--system` | str | — | System id or slug to evaluate. |
| `--scope` | str | — | firm/engagement/system, optionally with /environment. The alternative to --system, and the string `adopt init` already took. |
| `--environment` | str | — | One environment id. Omit for every environment, or name one in --scope. |
| `--store` | path | — | Store path. Defaults to the resolved ADOPT_STORE_PATH. |
| `--rebuild` | boolean | `false` | Write the result to covered_cache. Off by default: looking must not repair. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt detect`

Classify a file tree into one archetype, or refuse and rank. Exits `2` with `DETECT_AMBIGUOUS` when confidence is below the threshold -- including when a disambiguation proposal was obtained, because a proposal is not a decision.

```shell
adopt detect [PATH] [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `PATH` | path | `.` | The tree to classify. Read, never executed. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt doctor`

Report every configuration key with its resolved value and source. Exits `4` when there are findings: degraded success, not failure. `doctor` never repairs what it reports.

```shell
adopt doctor [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt draft`

Draft one handover section from what the store knows about an identity. Grounded strictly on store facts: the identity's attributes, where it was observed, and any knowledge already bound to it. A draft that cites none of them is discarded and nothing is written. What survives lands as **unverified** knowledge for `adopt review`.

```shell
adopt draft URI [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `URI` | str | **required** | The canonical identity URI to draft a section about. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--audience` | str | `technical` | Which audience the drafted section is written for. |
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--store` | path | — | Path to the store. |
| `--json` | boolean | `false` | Machine-readable output. |

## `adopt envelope validate`

Validate an outbound envelope against the boundary. Exits `3` on a violation.

```shell
adopt envelope validate FILE [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `FILE` | path | **required** | The document to validate. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | str | — | firm/engagement/system, optionally with /environment, to validate against the boundary declared for that scope. Omitted, the strictest default boundary is used. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt export`

Write a portable bundle of every exportable table.

```shell
adopt export DIRECTORY [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `DIRECTORY` | path | **required** | The bundle directory. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--store` | path | — | Store path. Defaults to the resolved ADOPT_STORE_PATH. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt freshness resolve`

Resolve one item's freshness and report the rule that decided it.

```shell
adopt freshness resolve [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--item` | str | **required** | The knowledge item id. |
| `--store` | path | — | Store path. Defaults to the resolved ADOPT_STORE_PATH. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt gaps`

Identities minus covered knowledge, ranked -- the elicitation queue. With no flag it lists. With one disposition flag it records what a human decided about a single gap and lists the result. **Open conflicts are listed alongside the gaps**, and they are a different thing: a gap is knowledge nobody has written, a conflict is knowledge somebody confirmed that a probe has since seen contradicted. Both belong in the same queue because both are work, and separating them would let a contradiction sit unread behind a heading nobody opened. **Existence stays derived, always.** `recompute_coverage` is the authority on whether an identity is uncovered, and nothing here writes its cache. A disposition row says only what someone decided to do; the report is the join of the two, so a gap that recompute no longer derives disappears from the listing whatever its disposition says (v6.1 §6 Build 4).

```shell
adopt gaps [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--ack` | str | — | Acknowledge one gap by its gap-key: someone owns closing it. |
| `--resolve` | str | — | Mark one gap resolved by its gap-key. |
| `--waive` | str | — | Waive one gap by its gap-key. Requires --until. |
| `--until` | str | — | Expiry for --waive, as YYYY-MM-DD. Mandatory on a waiver. |
| `--owner` | str | — | Who owns closing the gap being dispositioned. |
| `--note` | str | — | Why, in the reviewer's words. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt handover close`

Step 6 -- transfer ownership, and transfer what is still open with it. Refuses to leave the system unowned, and the refusal rolls the whole close back. Nothing here resolves a gap, answers a question or clears a conflict: unresolved items transfer with named owners rather than being closed to look complete.

```shell
adopt handover close [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--accepted-by` | str | **required** | Who accepted the handover for the receiving team. |
| `--system` | str | — | System id or slug. Freezes the whole system. |
| `--scope` | str | — | firm/engagement/system, optionally with /environment. Narrows to one environment. |
| `--out` | path | — | — |
| `--actor` | str | — | Who is performing this step. |
| `--store` | path | — | Path to the store. |
| `--json` | boolean | `false` | Machine-readable output. |

## `adopt handover elicit`

Step 2 -- the open gaps, as questions, grouped by the owner who answers them. This is where the saving lives: the agenda replaces the broad knowledge-transfer interview. An event that runs a general interview anyway has not used the product.

```shell
adopt handover elicit [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--system` | str | — | System id or slug. Freezes the whole system. |
| `--scope` | str | — | firm/engagement/system, optionally with /environment. Narrows to one environment. |
| `--out` | path | `handover` | Directory to write into. Created if absent. |
| `--actor` | str | — | Who is performing this step. |
| `--store` | path | — | Path to the store. |
| `--json` | boolean | `false` | Machine-readable output. |

## `adopt handover pack`

Step 3 -- one pack per audience, every section stamped and dated. The same assembler `adopt pack` runs, through the same shared functions, so the document a client receives is the one the FDE reviewed. Drafting is deliberately **not** here: `adopt pack --draft-missing` and `adopt review` run before this step, so what the handover emits is what a human has already confirmed or explicitly left stamped UNVERIFIED.

```shell
adopt handover pack [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--system` | str | — | System id or slug. Freezes the whole system. |
| `--scope` | str | — | firm/engagement/system, optionally with /environment. Narrows to one environment. |
| `--audience` | str | — | Emit this audience. Repeatable; defaults to all four. |
| `--out` | path | `handover` | Directory to write into. Created if absent. |
| `--format` | str | `md` | md (canonical), docx or pdf for the derived copy. |
| `--actor` | str | — | Who is performing this step. |
| `--store` | path | — | Path to the store. |
| `--json` | boolean | `false` | Machine-readable output. |

## `adopt handover snapshot`

Step 5 -- the acceptance snapshot: an export bundle and a recorded digest. Both parties hold it, and the client can **check** theirs: `adopt import` then `adopt export` on their own machine reproduces the same digest, because it is computed over the bundle's byte-stable table digests and nothing that varies between exports.

```shell
adopt handover snapshot [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--system` | str | — | System id or slug. Freezes the whole system. |
| `--scope` | str | — | firm/engagement/system, optionally with /environment. Narrows to one environment. |
| `--out` | path | `handover/acceptance` | Directory to write into. Created if absent. |
| `--actor` | str | — | Who is performing this step. |
| `--store` | path | — | Path to the store. |
| `--json` | boolean | `false` | Machine-readable output. |

## `adopt handover start`

Step 1 -- freeze the scope and record the opening position. Refuses a system that already has an un-closed handover: continuing the open one is almost always what was meant, and two records of one transfer is the fork a resumable checklist must not silently make.

```shell
adopt handover start [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--receiving-owner` | str | **required** | The group taking ownership of the system. |
| `--system` | str | — | System id or slug. Freezes the whole system. |
| `--scope` | str | — | firm/engagement/system, optionally with /environment. Narrows to one environment. |
| `--individual` | boolean | `false` | The receiving owner is a person, not a group. |
| `--actor` | str | — | Who is performing this step. |
| `--store` | path | — | Path to the store. |
| `--json` | boolean | `false` | Machine-readable output. |

## `adopt handover status`

Every step recorded, what comes next, and what is still open. Reads only, and answers for a closed handover as readily as an open one -- the record is what both parties keep, and it must still describe itself a year later.

```shell
adopt handover status [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--system` | str | — | System id or slug. Freezes the whole system. |
| `--scope` | str | — | firm/engagement/system, optionally with /environment. Narrows to one environment. |
| `--store` | path | — | Path to the store. |
| `--json` | boolean | `false` | Machine-readable output. |

## `adopt handover verify`

Step 4 -- record what the receiving team could and could not do. Every failed task becomes an **open question** carrying its text, answerable with `adopt answer` in the room. Failures are findings about the pack, not marks against people, and nothing here records who failed anything. Exits `4` when any task failed: the command worked and found something.

```shell
adopt handover verify [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--checklist` | path | **required** | The filled-in verification checklist (YAML). |
| `--system` | str | — | System id or slug. Freezes the whole system. |
| `--scope` | str | — | firm/engagement/system, optionally with /environment. Narrows to one environment. |
| `--actor` | str | — | Who is performing this step. |
| `--store` | path | — | Path to the store. |
| `--json` | boolean | `false` | Machine-readable output. |

## `adopt harvest`

Mine local git history into decision candidates -- unverified, with evidence.

```shell
adopt harvest [PATH] [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `PATH` | path | `.` | The checkout whose history to mine. Defaults to the working directory. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--since` | str | **required** | Mine commits reachable from HEAD but not from this ref. A tag, branch or sha. |
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--allow-network` | boolean | `false` | Not implemented. Forge enrichment is deferred; harvest reads local history only. |
| `--actor` | str | — | Who is running this. Recorded on every revision written. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt identity build`

Build the canonical URI for a referent.

```shell
adopt identity build [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | str | **required** | firm/engagement/system/environment, as immutable slugs. |
| `--kind` | str | **required** | The identity_kind. |
| `--key` | str | **required** | The local key. Repeat for a multi-segment key such as a symbol path; a single value is one segment, so any slash inside it is data. |
| `--namespace` | str | — | The namespace. Omit where the kind needs none. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt identity parse`

Parse a URI into its seven segments.

```shell
adopt identity parse URI [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `URI` | str | **required** | The identity URI. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt identity validate`

Check a URI against the grammar and canonical form. Exits 2 if it is not.

```shell
adopt identity validate URI [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `URI` | str | **required** | The identity URI. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt import`

Verify a bundle whole, then restore it into an empty store.

```shell
adopt import DIRECTORY [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `DIRECTORY` | path | **required** | The bundle directory. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--into` | path | **required** | The store to restore into. Created empty if absent. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt ingest`

Turn documents into knowledge, bound to the identities they refer to.

```shell
adopt ingest PATHS [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `PATHS` | path | **required** | Documents or directories to ingest. Markdown and text. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--audience` | str | — | Override the audience for every document in this run, beating frontmatter. |
| `--actor` | str | — | Who is running this. Recorded on every revision written. |
| `--unverified` | boolean | `false` | Land these documents unverified, each queued for a person to confirm in `adopt review`. Use it for text nobody has vouched for -- anything an agent wrote or helped write. Until confirmed it counts toward no coverage, serves no answer and reaches no pack. Its name-match suggestions are held back (`suggestions_deferred`) and queued by the next ingest of the confirmed text. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt init`

Create a store, resolve a scope, detect the archetype and declare the boundary.

```shell
adopt init [PATH] [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `PATH` | path | `.` | The tree to classify. Read, never executed. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | str | — | firm/engagement/system/environment, as immutable slugs. All four are required: a boundary is declared for one environment of one system. |
| `--answers` | path | `answers.json` | JSON file carrying the three qualification answers. |
| `--archetype` | str | — | Accept an archetype explicitly, when detection was ambiguous. This is the human decision PRD §8 requires before an archetype is written -- there is no confidence exemption and no flag that lets a proposal write itself. |
| `--store` | path | — | Store path override. |
| `--write-statement` | path | — | Also write the human-readable boundary statement. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt map`

Extract identities from a repository, deterministically and offline.

```shell
adopt map [PATH] [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `PATH` | path | `.` | Repository root to map. Defaults to the working directory. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--packs` | str | — | Comma-separated pack names, overriding archetype selection. For a mixed system whose archetype names only its dominant half. |
| `--report` | boolean | `false` | Render the stored map: counts by kind, listing with provenance. |
| `--check-expected` | path | — | A curated file of expected identity URIs. Exits 4 naming every miss. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt pack`

Assemble an audience-scoped handover pack from the store. Sections select **confirmed** knowledge by audience and kind; the map contributes the inventory; the coverage join contributes the gap appendix, and any open conflict a probe recorded is listed inside it; Build 0's observability boundary is embedded so the pack states its own limits. Every section carries its verification status and date. The Markdown is byte-stable given the same revisions -- no clock reaches it, so running this twice over an unchanged store produces identical files. With `--draft-missing`, uncovered identities are drafted first and render under UNVERIFIED banners until a human confirms them.

```shell
adopt pack [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--audience` | str | `technical` | Who the pack is for: technical, client_ops, end_user, admin, or a firm's own tag. |
| `--out` | path | `handover` | Directory to write the pack into. Created if absent. |
| `--format` | str | `md` | md (canonical), docx (via pandoc) or pdf (via typst). Derived formats are content-equivalent conversions of the Markdown, never canon. |
| `--draft-missing` | boolean | `false` | Draft uncovered sections through the configured model adapter. Drafts land UNVERIFIED and must be confirmed in `adopt review` before they count. |
| `--sections` | str | — | Re-render only these sections, comma-separated -- the section-scoped regeneration Build 8's review queue feeds. Names come from the pack's lineage sidecar via `adopt_handover.sections_affected`. |
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--store` | path | — | Path to the store. |
| `--json` | boolean | `false` | Machine-readable output. |

## `adopt probe add`

Validate a probe file and store it as a definition plus a revision.

```shell
adopt probe add FILE [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `FILE` | path | **required** | The probe YAML file. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt probe baseline`

Version how each probe's system behaves today. `--set` takes each active probe's latest run that observed something and writes it as a `baseline_version`. A run that failed or was refused by the manifest is never eligible: versioning a fault as *how the system behaves* would make the next clean run read as drift away from a bug. A run whose outcome was `diff` **is** eligible, and the report says so. That is what re-baselining after drift is -- a human accepting a change -- and it is deliberately not silent.

```shell
adopt probe baseline [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--set` | boolean | `false` | Version the latest recorded run of each probe as its baseline. |
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt probe diff`

Name what changed since the baseline -- and who changed it. Exits `4` when any probe drifted: degraded-with-findings, the same contract `adopt doctor` and `adopt map --check-expected` already use. The command **worked**; it found something a human must see. A probe whose latest run is of a different revision than its baseline reports `probe_changed` and never drift. The probe file was edited, so the question changed -- and calling that a change in the client's system is the one mistake that would train an FDE to ignore this command.

```shell
adopt probe diff [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt probe manifest validate`

Validate a probe capability manifest. Exits `3` on a violation.

```shell
adopt probe manifest validate FILE [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `FILE` | path | **required** | The document to validate. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt probe run`

Execute probes, recording what each one observed. Exits `1` when any probe failed or was blocked, so a CI step running probes fails the build rather than reporting green over a refusal.

```shell
adopt probe run [TARGET] [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `TARGET` | str | — | A probe name, or a path to a probe file to run without storing it. Omit it and pass --all to run every probe in scope. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--all` | boolean | `false` | Run every probe defined in the scope. |
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--store` | path | — | Store path override. |
| `--allow-network` | boolean | `false` | Permit the model adapter to be reached for `prompt` steps. An `http` step always reaches its declared hosts; this governs the agent seam only. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt pull`

Refresh this store from the control plane it replicates.

```shell
adopt pull [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--store` | path | — | Store path. Defaults to the resolved ADOPT_STORE_PATH. |
| `--init-replica` | boolean | `false` | Permit replacing a store that is not already a replica of this system. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt refresh`

Re-map the repository, classify what changed, and queue the review.

```shell
adopt refresh [PATH] [OPTIONS]
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `PATH` | path | `.` | Repository root to re-map. Defaults to the working directory. |

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--packs` | str | — | Comma-separated pack names, overriding archetype selection. Identities whose extractor does not run are exempt from death, never retired. |
| `--no-probes` | boolean | `false` | Skip the probe re-run. The map diff still runs; nothing opens a socket. |
| `--allow-network` | boolean | `false` | Permit the model adapter for a probe's `prompt` steps. An `http` step always reaches its declared hosts; this governs the agent seam only. |
| `--actor` | str | — | Who ran it, for the record. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt review`

The one review queue: harvest candidates, suggested bindings and changes. With no flag it lists. With one it resolves. What confirming *does* depends on which population the item belongs to -- bindings for a suggestion, a verified revision for a candidate -- and `adopt_knowledge.review` is where that rule lives and is documented. **`--resolve --action` is the change population's separate door, and the separation is deliberate.** `--confirm` on a suggestion answers "is this document about this identity?"; the three change actions answer "the referent moved -- what should this note do about it?". One flag meaning both is how a reviewer presses a button for one reason and gets a second thing they never looked at, which is the failure the two-populations rule in `adopt_knowledge.review` exists to prevent.

```shell
adopt review [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--confirm` | str | — | Confirm one review item by id. |
| `--reject` | str | — | Reject one review item by id. |
| `--edit` | str | — | Correct one review item by id. Needs --file. |
| `--file` | path | — | The corrected body for --edit. Markdown or text. |
| `--confirm-batch` | str | — | Confirm every open item in one batch -- the per-document batch confirm. |
| `--resolve` | str | — | Resolve one refresh change item by id. Needs --action. |
| `--action` | str | — | What the change means for the item: retire \| rebind \| confirm-current. |
| `--to` | str | — | The successor identity URI for --action rebind. Defaults to the recorded alias when the referent moved; required otherwise. |
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--actor` | str | — | Who is running this. Recorded on every revision written. |
| `--store` | path | — | Store path override. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt serve`

Answer questions over loopback HTTP: `POST /ask`, `GET /healthz`.

```shell
adopt serve [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--host` | str | — | Bind address. Loopback by default; anything else warns. |
| `--port` | int | — | Bind port. |
| `--scope` | str | — | firm/engagement/system/environment. Defaults to the store's. |
| `--store` | path | — | Store path override. |

## `adopt store doctor`

Report every finding in the store, and change nothing. Exit `4` with findings -- degraded success, not failure. The store opened, the checks ran, and their answers are trustworthy; what needs a human is what they found.

```shell
adopt store doctor [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--store` | path | — | Store path. Defaults to the resolved ADOPT_STORE_PATH. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt store info`

Schema version and row counts. Reads; never writes.

```shell
adopt store info [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--store` | path | — | Store path. Defaults to the resolved ADOPT_STORE_PATH. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt store migrate`

Apply pending forward migrations, then report the resulting version. Forward-only, always: implementation spec §7.4 states the schema has no rollback, and recovery is older code against a newer store.

```shell
adopt store migrate [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--store` | path | — | Store path. Defaults to the resolved ADOPT_STORE_PATH. |
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |

## `adopt version`

Report the binary, schema and export versions and the build provenance.

```shell
adopt version [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--json` | boolean | `false` | Emit the strict JSON envelope only. |
