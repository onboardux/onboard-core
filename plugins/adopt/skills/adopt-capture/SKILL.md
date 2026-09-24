---
name: adopt-capture
description: The daily knowledge loop with the adopt CLI — ask the store (KNOWN / STALE / UNKNOWN), escalate an unknown, bank a person's answer as confirmed knowledge, triage the one review queue (harvest candidates, suggested bindings, unverified documents, drafts), bind knowledge to identities, and disposition coverage gaps. Use whenever someone asks what the store knows about a system, wants a question answered from it, has an answer to record, needs to work through `adopt review`, or is closing gaps from `adopt gaps` — including "what do we know about X", "record what Sam just told us", "go through the review queue", or "why is this answer stale". Load adopt-cli first.
---

# The capture loop

**Why this loop exists:** the question an FDE answers in Slack today gets asked
again next month, of whoever is left. `adopt` turns each answer into knowledge
bound to the thing it is about, so the next asker gets KNOWN with a citation.
The loop only works if the store stays honest:

- **Only a person's words become canon.** You ask, escalate, propose and
  organise. You never author an answer, and you never confirm your own text.
- **Only confirmed knowledge counts.** Harvest candidates, drafts and
  `--unverified` documents serve no answer and close no gap until a person
  confirms them.

Read `adopt-cli` first for the contract and which commands need a yes.

## 1. Ask

```shell
adopt ask "Why do refunds over 500 need a second approver?" --json
```

Exactly three branches:

| `branch` | What you have | Report it as |
|---|---|---|
| `known` | Passages quoted verbatim, each with `revision_id`, `item_id`, `freshness_state` and the `deciding_rule` | The answer, **quoted**, with its `revision_id`. Never paraphrase it into something stronger than the source says. |
| `stale` | The prior answer **plus the cause** that made it stale | Stale, naming the cause. Never present it as current. `adopt freshness resolve --item <item_id> --json` explains the rule. |
| `unknown` | Nothing in the store covers it | Unknown. Offer to escalate. **Never fill the gap with your own guess.** |

`ask` is extractive: it quotes what the store holds and composes nothing. If
you know something is documented and `ask` says unknown, check in this order:
was it ingested (`adopt store info --json`), is it in this scope, is it confirmed
(candidates and drafts never serve), and is the index current
(`adopt ask "..." --reindex --json`).

## 2. Escalate an unknown

```shell
adopt ask "How do we rotate the orders API key?" --escalate --json
```

This records the question **with its text** and returns an `escalation_id`.
Passive question logging is off by default. Escalating is the explicit act that
stores the text, so escalate only what the person agrees should be recorded.

## 3. Bank a person's answer

When a person answers (in the conversation, a meeting or a message they paste),
record **their words, in their name**:

```shell
adopt answer esc_01M2X6MA8593JP5PS32GRX1SC4 \
   --text "Rotate it in Vault under orders/api, then restart the two API pods. Only the platform team can." \
   --uri 'onboard-v1://northwind/acme-erp/orders-api/prod/config_key/env/ORDERS_API_KEY' \
   --actor sam@client.com --json
```

- `--text` is the person's answer, verbatim or lightly tidied **with their
  agreement**. Show them what will be banked before running it.
- `--actor` is the person who answered, never you.
- `--uri` (repeatable) binds the answer to the identities it is about. Find them
  with `adopt gaps --json` or `adopt map --report --json`; build new ones with
  `adopt identity build`. Check `unmatched_uris` in the result: a URI that
  matched nothing is reported there, not bound.
- `--audience` (repeatable) names who it is for; the default suits technical
  readers.

One command writes the item, its confirmed revision, provenance and bindings,
and closes the question. The next `ask` returns KNOWN, citing that revision. In
remote mode (a control plane is configured) the same command posts the capture
to the plane instead; see `adopt-connect`.

## 4. Text you wrote

Sometimes the useful thing is a note you drafted: a summary of how a subsystem
works, pieced together from reading the code. It is **not** knowledge until a
person vouches for it. There are two honest routes:

- **When preflight reports `ingest_unverified`:** write the note in the
  engagement workspace (never the repository), tell the person, then

  ```shell
  adopt ingest ../orders-api-adopt/notes/refund-flow.md --unverified --json
  ```

  It lands unverified with authored provenance and is queued in an
  `ingest-unverified` review batch. It counts toward nothing until a person
  confirms it in review. Run ingest from the repository root, like every
  ingest. Its name-match suggestions are held back (`suggestions_deferred` in
  the envelope). After the person confirms the note, **run the same ingest
  command again**, and they are queued against the confirmed text, one question
  at a time. Structural bindings (URIs and exact paths the note cites) land at
  once, so citing canonical URIs in your notes is what makes them bind.
- **Otherwise:** give the person the text as a proposed answer. If they agree, it
  goes in through `adopt answer` in their name.

Never plain-`ingest` your own text: that lands it `verified` as if the client had
written it, and nothing afterwards can tell the difference.

## 5. Work the review queue

```shell
adopt review --json > ../orders-api-adopt/review.json
```

One queue, several populations. **What "confirm" does depends on the
population**, so say which one before asking the person to decide:

| `source` | The person is asked | Confirming |
|---|---|---|
| `ingest` | "Is this document about this identity?" | creates the suggested binding(s) |
| `harvest` | "Is this commit a real decision worth keeping?" | appends a verified revision |
| `draft` | "Is this drafted section true of the system?" | appends a verified revision |
| `ingest-unverified` | "Is this document, which nobody vouched for, true?" | appends a verified revision |
| `refresh` / `sense` | "This changed; is the note still right?" | not confirm: see `adopt-watch` |

Present each item with its evidence: the suggested URIs for a suggestion, the
commit sha and decision records for a candidate. Then record the person's
decision:

```shell
adopt review --confirm <review-item> --actor sam@client.com --json
adopt review --reject  <review-item> --actor sam@client.com --json
adopt review --edit    <review-item> --file ../orders-api-adopt/corrected.md --actor sam@client.com --json
adopt review --confirm-batch <review-batch> --actor sam@client.com --json
```

- `--edit` appends the person's corrected text as a new revision. The mined or
  drafted original keeps its own provenance forever.
- `--confirm-batch` is for a person who has read the whole batch, never a way to
  clear a queue faster.
- A rejected candidate or draft stays in the store, unverified, as evidence. It
  never serves and never counts.
- A name-match suggestion never binds until confirmed. That is the binding-honesty
  rule: a false binding makes a gap disappear and stales notes that never
  described the thing that changed.

## 6. Bind by hand

For a link no matcher found, and **only on a person's say-so**:

```shell
adopt bind ki_01M2X6JY9RBBBSTC1SZ9P81DHN 'onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/POST%20%2Fv1%2Frefunds' --json
```

Bindings are load-bearing by default: a change to the identity stales the note.
Pass `--not-load-bearing` only when the person confirms that a change to it
should not make the note suspect.

## 7. Gaps and their dispositions

```shell
adopt gaps --json
```

Identities minus covered knowledge, ranked. **Only confirmed knowledge with a
confirmed or structural binding counts.** Walk the list with the person in rank
order; the goal is not 100%, it is that the next question somebody asks is
already answered. Dispositions record a person's decision and never write
coverage:

```shell
adopt gaps --ack <gap-key> --owner alice@client.com --note "SME session Thursday" --json
adopt gaps --resolve <gap-key> --json
adopt gaps --waive <gap-key> --until 2026-12-31 --note "deprecated in Q4" --json
```

A waiver requires `--until`. A gap that recompute stops deriving disappears
whatever its disposition says. `conflicts` in the same listing is confirmed
knowledge that a probe has since seen contradicted; see `adopt-probes`.

## 8. Coverage, and the alarm you should expect

```shell
adopt coverage recompute --json            # after captures: disagreements are expected
adopt coverage recompute --rebuild --json  # exit 4 on the run that repairs the cache
adopt coverage recompute --json            # exit 0, "disagreements": []
```

The cache is rebuilt from the function and never the reverse. A disagreement
after a capture is the alarm working. Claim coverage moved only with this output.

## 9. Serving answers locally

`adopt serve` exposes `POST /ask` on loopback for tools that prefer HTTP. It
binds loopback by default. `--host` with anything else exposes the store to a
network: get a yes first, and say so.

## When something refuses

<!-- BEGIN GENERATED: errors ASK_,ESCALATION_,REVIEW_,BIND_,GAP_,COVERAGE_,FRESHNESS_ -->
| Code | Category | Exit |
|---|---|---|
| `ASK_OUTSIDE_BOUNDARY` | policy | `3` |
| `BIND_TARGET_NOT_FOUND` | usage | `2` |
| `COVERAGE_CACHE_DISAGREEMENT` | integrity | `1` |
| `ESCALATION_ALREADY_ANSWERED` | policy | `3` |
| `ESCALATION_NOT_FOUND` | usage | `2` |
| `FRESHNESS_SENSOR_DEGRADED` | policy | `3` |
| `GAP_NOT_FOUND` | usage | `2` |
| `GAP_WAIVER_NEEDS_UNTIL` | usage | `2` |
| `REVIEW_ITEM_NOT_FOUND` | usage | `2` |
| `REVIEW_ITEM_RESOLVED` | policy | `3` |
<!-- END GENERATED -->
