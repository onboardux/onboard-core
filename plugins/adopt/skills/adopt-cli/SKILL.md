---
name: adopt-cli
description: Operating rules for driving the published `adopt` CLI (adopt-cli 0.4.1 or newer) on a client engagement — install and preflight, the JSON and exit-code contract, which commands write and which need a human's yes, where output may go, and which adopt-* skill handles each job. Use whenever a task involves the `adopt` command, an `.adopt/store.db`, an `onboard-v1://` URI, coverage gaps, knowledge bindings, a review queue, probe baselines, a handover pack, or an Onboard control plane — even when the user only says "map this repo for the handover", "what do we know about this system" or "set up adopt here". Not for changing adopt's own source code.
---

# Driving `adopt`

`adopt` records what a forward-deployed engineer learned about a client's system
so it survives them leaving. It maps the repository into stable identities, holds
knowledge bound to those identities in one append-only SQLite store, answers from
it honestly, notices when the system changes, and assembles handover packs.

**The CLI is the capability. You are the judgement.** Everything deterministic —
extraction, binding, coverage, freshness, byte-stable export — is already built,
signed and tested in the published package. What it deliberately leaves to a
person is what you help with: choosing the scope, confirming the archetype,
deciding which identities matter, writing probes, and triaging what a human must
decide. Never re-implement a capability, and never write into the store by any
route other than an `adopt` command.

**You are working on a real client engagement, and the store is append-only.**
There is no delete path and no update method on any revision table. Read the
three irreversible actions in section 6 before your first write.

## 1. Preflight, every session

From this skill's directory, run the bundled checker. It is read-only and needs
only Python 3.8 or newer:

```shell
python scripts/preflight.py --json
```

It reports whether `adopt` is installed, its version against this plugin's floor
(`0.4.1`), whether the artifact carries release provenance (`build_id`), whether
every command these skills use exists, which optional features the installed CLI
has (for example `ingest --unverified`), and whether a store and a git work tree
are present. `ready: false` names each problem.

| Result | Do |
|---|---|
| `adopt` not found | **Stop and ask** before installing anything. Then: `uv tool install "adopt-cli>=0.4.1"` or `pipx install "adopt-cli>=0.4.1"`. Without Python 3.12, use the signed standalone binary — see `references/install.md`. |
| version below `0.4.1` | Stop and say so. `0.4.0` crashes on nine `--help` pages and its captured answers never reach a pack. Upgrade with the same tool that installed it. |
| `build_id` null | Fine in a source checkout; on anything installed from PyPI or a release it is worth reporting. |
| a feature absent | Follow the fallback the relevant skill gives for it. Do not invent the flag. |

No Python on the machine? `adopt version --json` and `adopt doctor --json` give
the same facts by hand.

## 2. Where things live

Run `adopt` **from inside the client's repository**; that is what every command's
defaults assume. Keep everything else in a **sibling engagement workspace**:

```text
~/clients/acme/orders-api/          <- the client's repository (you run adopt here)
    .adopt/store.db                 <- the store; the walk always skips .adopt/
~/clients/acme/orders-api-adopt/    <- the engagement workspace (you write here)
    answers.json  expected-identities.txt  probes/  packs/  bundles/  handover/
```

Hide the store from git **without editing the client's tracked files**:

```shell
echo ".adopt/" >> .git/info/exclude
```

Why a workspace: `export`, `pack` and `handover` default to paths **under the
working directory**, and `map`/`refresh` walk that directory. Output left in the
tree is mapped as if it were the client's system — measured: 5 files walked
became 89 and one `refresh` minted 1,174 bogus identities, which the append-only
store keeps forever. The same happens to an `answers.json`, a probe file or an
expected-identities list saved in the tree. So every output flag points into the
workspace, always:

```shell
adopt export ../orders-api-adopt/bundles/2026-09-24
adopt pack --audience technical --out ../orders-api-adopt/packs
```

`scripts/adopt_run.py` (below) refuses an output path inside the git work tree
for exactly this reason.

## 3. Running commands and reading results

Always pass `--json`. It prints one JSON object on **stdout** and nothing else;
structured logs go to **stderr**. Two habits break this:

- **Never `2>&1`** into a parser — it mixes the logs into the JSON.
- **Never pipe into `head`** — the CLI takes the broken pipe and exits `1` with
  nothing on stderr, which looks exactly like a failure and is not one.

The bundled runner does it right on every platform, keeps the full envelope as a
file, and prints a short summary you can read:

```shell
python scripts/adopt_run.py --save ../orders-api-adopt/runs -- gaps
python scripts/adopt_run.py -- map --report
```

It adds `--json` if you omitted it, saves stdout, and prints the exit code with
its meaning, the error `code` and `hint` when there is one, and the top-level
keys. Read the saved file for detail rather than re-running.

## 4. Exit codes — branch, never chain with `&&`

| Code | Meaning | Your move |
|---|---|---|
| `0` | Success | Continue. |
| `1` | Operational failure | Stop. Anything below it is unreliable evidence. |
| `2` | Usage error | Your invocation was wrong. Fix it; never retry verbatim. |
| `3` | Policy refusal | It *would not*, by design. Report it; never route around it. |
| `4` | Degraded success with findings | It worked **and** found something a human must see. Read the findings; continue. |

Healthy runs of `refresh`, `probe diff`, `map --check-expected`, `doctor`,
`coverage recompute --rebuild` and `handover verify` exit `4`. A typed failure
prints `{"error": {"code", "category", "message", "hint", "run_id"}}`; branch on
`code`, read `hint` first — it usually names the fix. A parser error (unknown
flag or command) exits `2` with plain text and no envelope: that is your
invocation, not the tool. Every code is listed in `references/errors.md`.

## 5. What you may run, and what needs a person

**Read-only — run freely to orient yourself:**

```text
version · doctor · store info · store doctor · detect · gaps (no flags) · review (no flags)
ask (without --escalate) · freshness resolve · coverage recompute (without --rebuild)
identity build|parse|validate · agent adapters · agent check
probe manifest validate · probe diff · handover status · envelope validate
```

**Writes to the store — normal work, but say what you are about to do and why:**

```text
map · map --report · map --check-expected · ingest (client documents) · harvest
refresh --no-probes · coverage recompute --rebuild · probe add · export · pack
handover elicit|pack|snapshot
```

`map --report` and `map --check-expected` **re-run the map over PATH (default `.`)
before they report.** From the repository root that is harmless, because mapping
is idempotent. Anywhere else, it maps the wrong tree into the store: run from the
workspace, one agent mapped its own work files and "moved" 24 identities. Run
them from the root, or pass the path: `adopt map ../orders-api --report --json`.

**Needs an explicit yes from a person, every time:**

| Command | Why |
|---|---|
| `init` | Fixes four slugs forever and declares what you may observe. |
| `answer` | Records a person's words as confirmed knowledge. |
| `review --confirm / --reject / --edit / --confirm-batch / --resolve` | A review decision is a human judgement. |
| `bind` | Asserts that knowledge is about an identity. |
| `gaps --ack / --resolve / --waive` | A disposition is a commitment, often with a named owner and an expiry. |
| `probe run`, and `refresh` without `--no-probes` once probes are stored | Sends requests to the client's system — only to declared hosts, but it is still an action on their environment. |
| `probe baseline --set` | Declares "this behaviour is normal". |
| `handover start / verify / close` | Transfers accountability for a real system. |
| `ingest --unverified` of text you wrote | Asks a person to vouch for it; tell them it is coming. |
| `draft`, `pack --draft-missing` | Spend on a model and write unverified drafts. |
| `import`, `pull --init-replica` | Replace or restore a store. |
| anything with `--allow-network` | Egress. Offline is the default posture. |
| `ci-sense`, `serve --host` other than loopback | Posts to, or listens for, the outside world. |

Never run a command in order to get around another command's refusal.

## 6. Three things you cannot undo

1. **Output inside the mapped tree.** Section 2. The only recovery is deleting
   the store, which loses every answer and binding since.
2. **The four scope slugs.** `firm/engagement/system/environment` are fixed by
   `adopt init`; a rename raises `SCOPE_SLUG_IMMUTABLE` and a slug is never
   reissued. Confirm all four with a person before `init`.
3. **Knowledge nobody vouched for.** `adopt ingest` lands a document `verified` —
   right for prose the client wrote, wrong for text **you** wrote. Anything you
   author about the system goes in one of three ways only:
   - a person says it, and it is banked with `adopt answer` (their words, their name);
   - `adopt ingest --unverified` when preflight reports that feature, which lands
     it unverified and queues it for a person to confirm in `adopt review`;
   - `adopt draft` / `pack --draft-missing`, which land unverified behind a
     grounding check.

   If the installed CLI lacks `--unverified`, do **not** ingest your own text at
   all: hand it to the person as a proposed answer instead. Never confirm your own
   drafts, and when `ask` returns UNKNOWN, escalate — never compose an answer.

## 7. Which skill does the job

| The job | Skill |
|---|---|
| First day on a system: install, detect, scope, init, map, curate the recall list, ingest, harvest, first gaps | `adopt-onboard` |
| Daily: ask, escalate, bank a person's answer, triage the review queue, bind, dispositions | `adopt-capture` |
| Behaviour that changes without a commit: author, run, baseline and diff probes | `adopt-probes` |
| The system changed: refresh, classify, and resolve change items | `adopt-watch` |
| Packs per audience, drafting, the six-step verified handover | `adopt-handover` |
| An operated system: replica pull, remote capture, `ci-sense` in the client's CI | `adopt-connect` |

## 8. Reporting back

- Quote the envelope: counts, ids, `revision_id`, `item_id`, `freshness_state`.
  An answer stamped `stale` is reported as stale, with its cause.
- Name the exit code and `error.code` whenever a command did not return `0`.
- Report a refusal (exit `3`) as the product working, never as a bug.
- Say which knowledge is confirmed and which is unverified (harvest candidates,
  drafts, `--unverified` ingests). Only confirmed knowledge is canon.
- Do not claim coverage moved without `adopt coverage recompute` output showing it.

## References

- `references/commands.md` — every command and flag of the CLI these skills were
  generated against. Generated; never hand-edited.
- `references/errors.md` — the exit-code table and every error code by category.
  Generated.
- `references/install.md` — pip, uv and the signed standalone binary, with
  signature verification.
