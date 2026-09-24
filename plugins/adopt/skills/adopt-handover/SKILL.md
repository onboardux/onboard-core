---
name: adopt-handover
description: Produce handover deliverables with the adopt CLI — audience-scoped packs assembled from confirmed knowledge (every section stamped fresh / stale / unverified and dated), optional model drafting of missing sections that lands unverified for review, DOCX/PDF copies, and the six-step verified handover (start, elicit, pack, verify, snapshot, close) that transfers ownership with the open items attached. Use whenever someone needs a handover pack, runbook, client-facing documentation from the store, drafts for uncovered sections, or is closing an engagement and handing a system to its new owners — "write up the handover", "generate the ops pack", "we're leaving this client next month". Load adopt-cli first.
---

# Handover

**Why:** a handover that is a document written from memory the week before the
FDE leaves is out of date on arrival, and silent about what nobody knows. `adopt`
assembles packs from the store, stamps every section with how much it can be
trusted, **includes the gaps**, and records the transfer as a sequence of steps
that both parties hold.

Read `adopt-cli` first. Every output goes to the engagement workspace: `pack` and
`handover` default to `./handover` **inside the tree**, which `map` would then
read as part of the system.

## 1. Packs

```shell
adopt pack --audience client_ops --out ../orders-api-adopt/packs --json
```

- Audiences: `technical` (the default), `client_ops`, `end_user`, `admin`, or the
  firm's own tag. A pack selects **confirmed** knowledge tagged for that
  audience.
- Sections are an overview from the map, runbook and how-to content, answers to
  common questions, decisions, **gaps**, and the observability boundary. The gap
  section belongs in the pack: a handover that lists what is not known is the one
  worth signing.
- Every section carries its stamp (`fresh`, `stale` or `unverified`) and a date.
  Unverified content renders under a banner. **Never edit the Markdown to make a
  stamp go away**; fix the knowledge instead (`adopt-capture`, `adopt-watch`).
- Markdown is canonical, and byte-stable given the same revisions. It sits beside
  a `.lineage.json` sidecar mapping each section to its source revisions.
- `--format docx` needs `pandoc` and `--format pdf` needs `typst`. Without them
  the command refuses with `PACK_RENDERER_MISSING`. Derived copies are
  content-equivalent, never canon.
- `--sections a,b` re-renders only named sections, for the scoped regeneration
  the sidecar enables.

**Read the boundary section before a pack leaves**, and tell the person what a
client will read there:

- It states whether a contractual approval is recorded. No command records one
  today, so it reads "Contractual: no" even when the boundary was agreed.
- It lists the capabilities the tier grants. On `0.4.1`, a `T3` boundary also
  lists change detection on deploy when the deploy-signal answer was `false`,
  because tiers are ordered as a ladder. Check the list against the three
  answers. Where they disagree, the answers are what was agreed.

Never hand-edit the Markdown to fix either one. Name them, and let the person
decide on a covering note.

## 2. Drafting what is missing (optional, uses a model)

With no model, a pack renders explicit "no knowledge yet" gap sections, and that
is complete. With one configured, `adopt` drafts uncovered sections **grounded
strictly on store facts** (identity attributes, provenance, mined decisions,
probe observations). A draft that cites nothing it was given is discarded.
Survivors land **unverified**, bound to their identities, and wait in review.

```shell
adopt agent adapters --json                            # what exists, and why each is unusable
adopt --allow-network pack --audience client_ops --draft-missing --out ../orders-api-adopt/packs --json
adopt --allow-network draft 'onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/POST%20%2Fv1%2Frefunds' --json
```

This needs a person's yes. It spends on their provider (bring your own key:
`ADOPT_ADAPTER`, `ADOPT_MODEL` and `ADOPT_API_KEY` in the environment; there is
never a default model) and it is egress. `--allow-network` is a **root option**
and goes before the command. Then take the drafts to a person through
`adopt-capture`'s review loop (`source: draft`). **Never confirm a draft
yourself.** A confirmed draft re-renders `fresh`; a rejected one stays stamped
UNVERIFIED.

## 3. The verified handover — six recorded steps

`adopt handover` is a state machine, not a document generator. Each step is
recorded, strictly ordered and resumable, and `status` always tells you where it
stands:

```shell
adopt handover status --json
```

Before step 1, agree with the person: who the receiving owner is (a team, or
with `--individual` a person), who will accept on their side, and when the
verification session happens.

1. **Start** freezes the scope and records the opening position (identities,
   coverage, open gaps, questions and conflicts). This transfers accountability,
   so get a yes.

   ```shell
   adopt handover start --receiving-owner "Northwind Platform Team" --actor fde@firm.com --json
   ```

2. **Elicit** turns open gaps into an agenda grouped by the owner who answers
   them. Assign owners first (`adopt gaps --ack <key> --owner ...`) and the
   grouping schedules the meetings.

   ```shell
   adopt handover elicit --out ../orders-api-adopt/handover --json
   ```

   Answers from those sessions go in through `adopt answer`, in the answering
   person's name.

3. **Pack** emits one pack per audience, from the same assembler. Draft and
   review **before** this step. What it emits is what a human has already
   confirmed, or left explicitly stamped.

   ```shell
   adopt handover pack --out ../orders-api-adopt/handover --json
   ```

4. **Verify** records what the receiving team could and could not do using only
   the pack and `adopt ask`. You may help the person write the tasks, starting
   from `assets/verification-checklist.template.yaml`. **The outcomes are
   theirs.** Record exactly what happened in the session and never fill in a
   `pass`. A failed task becomes an open question with its text, answerable in
   the room with `adopt answer`. Exit `4` when any task failed: it worked, and
   found something.

   ```shell
   adopt handover verify --checklist ../orders-api-adopt/handover/checklist.yaml --json
   ```

5. **Snapshot** writes the acceptance bundle and a recorded digest that both
   parties keep. The client can reproduce the digest themselves with `import`
   then `export`.

   ```shell
   adopt handover snapshot --out ../orders-api-adopt/handover/acceptance --json
   ```

6. **Close** transfers ownership **and the open items with it**. Unanswered
   questions and unclosed gaps become the new owner's, by name, instead of
   evaporating at signature. It refuses to leave the system unowned
   (`HANDOVER_UNOWNED`).

   ```shell
   adopt handover close --accepted-by "J. Okafor" --actor fde@firm.com --json
   ```

A step run out of order exits `2` with `HANDOVER_STEP_OUT_OF_ORDER`, naming what
comes first; `status` is the recovery. `HANDOVER_ALREADY_OPEN` means a handover
for this system is open: continue it rather than starting a second record of the
same transfer.

## Report back

Give each pack's path, its sections with their stamps, and the number of gaps it
lists. For a handover, give the state from `status`, the opening numbers,
verification pass/fail/skipped counts, the snapshot digest, and what transferred
open.

## When something refuses

<!-- BEGIN GENERATED: errors HANDOVER_,PACK_,AGENT_ -->
| Code | Category | Exit |
|---|---|---|
| `AGENT_ADAPTER_UNKNOWN` | usage | `2` |
| `AGENT_BUDGET_EXHAUSTED` | policy | `3` |
| `AGENT_OFFLINE_ADAPTER_DENIED` | policy | `3` |
| `AGENT_OUTPUT_SCHEMA` | internal | `1` |
| `AGENT_PROVIDER_ERROR` | transient | `1` |
| `HANDOVER_ALREADY_OPEN` | usage | `2` |
| `HANDOVER_CHECKLIST_INVALID` | usage | `2` |
| `HANDOVER_NOT_OPEN` | usage | `2` |
| `HANDOVER_STEP_OUT_OF_ORDER` | usage | `2` |
| `HANDOVER_TARGET_IS_REPLICA` | policy | `3` |
| `HANDOVER_UNOWNED` | policy | `3` |
| `PACK_RENDERER_MISSING` | usage | `2` |
| `PACK_SECTIONS_EMPTY` | usage | `2` |
<!-- END GENERATED -->
