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
| `known` | `citations`: whole documents, each with `revision_id`, `item_id`, `body_md`, `freshness_state` and the `deciding_rule` | The part of `body_md` that answers, **quoted**, with its `revision_id` — after checking that it does answer (below). Never paraphrase it into something stronger than the source says. |
| `stale` | The prior answer **plus the cause** that made it stale | Stale, naming the cause. Never present it as current. `adopt freshness resolve --item <item_id> --json` explains the rule. |
| `unknown` | Nothing in the store covers it | Unknown. Offer to escalate. **Never fill the gap with your own guess.** |

`ask` is extractive: it quotes what the store holds and composes nothing. **What
decides the branch is lexical, and you have to know the rule to read it right.**
A document "covers" a question when it contains at least two of the question's
words, matched exactly after case-folding: no stemming, no synonyms, function
words ignored. Three consequences, all measured:

- **`known` is not "answered".** "What is the on-call escalation path for the
  carrier sync?" came back `known`, citing a runbook that says only how to re-run
  the sync: "carrier" and "sync" matched. Read each citation's `body_md`. If
  none answers, say so ("the store has related text, not an answer") and offer to
  escalate. Quote only the passage that answers; the citation is the whole
  document.
- **`unknown` can be vocabulary.** "Who owns the migrations?" was `unknown`
  against an ADR saying "Migrations are owned by the platform team" (`owns` is
  not `owned`); "Postgres" never matches "PostgreSQL". Before escalating a
  question you have reason to think is documented, ask it **once** more in the
  documents' own words. Once, not until something matches.
- **A fresh match hides a stale one.** When any matching document is fresh, the
  answer is `known` with only the fresh ones, and a stale document on exactly
  this topic is not mentioned. If the citations miss the point and an open
  `refresh` item in `adopt review` concerns the subject, the answer you want is
  probably the stale one: say so, with its cause.

If you know something is documented and `ask` still says unknown, check in this
order: was it ingested (`adopt store info --json`), is it in this scope, is it
confirmed (candidates and drafts never serve), and is the index current
(`adopt ask "..." --reindex --json`).

## 2. Escalate an unknown

```shell
adopt ask "How do we rotate the orders API key?" --escalate --json
```

This records the question **with its text** and returns an `escalation_id`.
Passive question logging is off by default. Escalating is the explicit act that
stores the text, so escalate only what the person agrees should be recorded.
**Only an `unknown` escalates.** If the same words come back `known`, there is no
`escalation_id` and nothing was recorded, even when the citations do not answer.
Word the question so it is plainly about the missing fact, and check the envelope
for the id before promising anyone an answer will be routed.

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
| `ingest` | "Is this document about **each** of these identities?" | creates **every** suggested binding on the item |
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
- **An `ingest` item is all or nothing.** Its suggestions are listed under
  `suggestions` in the envelope, one row per URI with the matched words as
  `evidence`, and `--confirm` binds every one. There is no per-suggestion confirm
  and no unbind. Measured: a README's eleven suggestions included the `uvicorn`
  dependency because the README's run command names it. When any suggestion is
  wrong, show the person the list, `--reject` the item, and `adopt bind` the
  right ones by hand on their say-so (section 6).
- After a `refresh` item on a document is resolved with `confirm-current`, the
  next `ingest` can queue that document's name-match suggestions again, including
  ones the person already rejected. Say so, and reject them again. Do not
  confirm them to clear the queue.

## 6. Bind by hand

For a link no matcher found, and **only on a person's say-so**:

```shell
adopt bind ki_01M2X6JY9RBBBSTC1SZ9P81DHN 'onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/POST%20%2Fv1%2Frefunds' --json
```

Bindings are load-bearing by default: a change to the identity stales the note.
Pass `--not-load-bearing` only when the person confirms that a change to it
should not make the note suspect.

One item and one identity have **one** binding, for ever: its history is its
revisions. So `bind` refuses a pair that was ever bound — even one a `rebind`
has since marked `moved` — with `REVISION_CHAIN_FORK`, exit `1`, category
`integrity`. That is not store corruption, and nothing below it is unreliable,
but on `0.4.1` no command re-activates the old link either. Report it; do not
retry.

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

A `gap-key` is `<uri>|<environment>|<kind>`; copy it from the listing, never
build it. A waiver requires `--until`. A gap that recompute stops deriving
disappears whatever its disposition says: a key that was covered since your last
listing answers `GAP_NOT_FOUND`, which is progress, not an error.

A gap whose `reasons` is `identity_revision_not_active` is a referent `refresh`
retired as dead. It stays in the listing and in the pack's gap table, and no
knowledge can ever cover it. Offer the person a waiver that says so (`--note
"retired by refresh: renamed to CARRIER_API_TOKEN"`) rather than chasing an
answer for something that no longer exists.

`conflicts` in the same listing is confirmed knowledge that a probe has since
seen contradicted; see `adopt-probes`.

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
