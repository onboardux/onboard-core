---
name: adopt-dev-knowledge-serve
description: How adopt-core turns documents, history and people into knowledge and serves it honestly — ingest and its verification rule, harvest candidates, the two-tier binding rule, the one review queue and its populations, ask's KNOWN/STALE/UNKNOWN branch welded to freshness, escalation and one-step capture, grounded synthesis and drafting that land unverified, audience packs with stamps and byte-stable Markdown, and the verified-handover state machine. Use whenever changing adopt-knowledge, adopt-ask or adopt-handover, adding a review population, a binding heuristic, a pack section or a handover step, or debugging a false binding, an answer served without its cause, a draft that reached canon, or a pack that looks more certain than its sources.
---

# Knowledge, serving and handover

This area has one job that everything else serves: **the store must never be more
confident than its evidence.** The failures it exists to prevent are silent and
become trusted state: a binding nobody justified, an answer served without a
freshness check, generated text landing as canon, a pack section that hides its
own uncertainty. Every mechanism below makes one of those unrepresentable rather
than merely unlikely.

## Where knowledge comes from, and how verified it is

| Source | Module | Lands | Authority | Why |
|---|---|---|---|---|
| `adopt ingest` (default) | `adopt_knowledge.ingest` | `verified` | `artifact_observed` | a document the client shipped is their own prose, transcribed |
| `adopt ingest --unverified` | same | `unverified`, queued `ingest-unverified:` | `human_confirmed` (authored) | text nobody vouched for, above all agent-written |
| `adopt harvest` | `adopt_knowledge.harvest` | `unverified`, queued `harvest:` | `artifact_observed` + commit provenance | a mined inference about what a commit meant |
| `adopt answer` | `adopt_ask.capture` | `verified` | `human_confirmed` | a person said it, attributed to them |
| `adopt draft`, `pack --draft-missing` | `adopt_knowledge.drafting` | `unverified`, queued `draft:` | `human_confirmed` (authored) | generated, grounded on store facts |

**Provenance is a chain, not a flag.** Confirming or editing *appends* a
`human_confirmed` revision with `human` provenance; the superseded revision keeps
its own class and citation forever. Nothing authored can ever acquire
`artifact_observed`, which is a claim about where text was read from.

## Binding honesty (critical invariant #2)

*No binding row exists that a structural match or a person did not justify.*
`adopt_knowledge.matchers` has two tiers and no third:

- **Structural** (`STRUCTURAL_TIERS` = URI mention, or a path resolving to
  exactly one identity) auto-binds, load-bearing.
- **Name** (`NAME_TIER`) only **suggests**. Suggestions are derived again at
  review time, never stored: no provisional binding row exists, because
  `recompute_coverage` would count it.

A false binding is silent and self-reinforcing: the gap it hides stops being
asked about, and every later change stales a note that never described the thing.
**Adding a heuristic or promoting name-matching to auto-bind needs its measured
trigger** (≥ 95% confirm rate over ≥ 50 real suggestions, for that rule only).
Never add one because it seems accurate. `tests/unit/test_binding_honesty.py`
is the invariant's test; keep it adversarial.

Move survival (#3) is the substrate's: a move re-points through the alias, and
the binding is never rewritten.

## The one review queue

`adopt_knowledge.review` holds every population in `review_batch`/`review_item`,
told apart by the `batch_key` prefix its producer stamps (`source_of`). **What
confirming does depends on the population**, and that is membership data, not
branching:

- `ingest:`: confirm creates the suggested bindings.
- `_APPENDS_REVISION` = `harvest`, `draft`, `ingest-unverified`: confirm
  appends the verified revision. A new population whose confirmation means "this
  text is true" joins this set; it does not get a new branch.
- `CHANGE_POPULATIONS` = `refresh`, `sense`: resolved with
  `retire | rebind | confirm-current` in `adopt_knowledge.changes`, each of which
  writes to the store.

`confirm`, `edit` and `reject` each record the disposition **first, inside one
transaction** with the action. A double confirmation is refused, and a failed
action rolls the disposition back, so the item stays re-confirmable. A rejected
candidate or draft stays in the store, unverified, as evidence.

## Ask: three branches, freshness welded in (critical invariants #5, #7)

- **Retrieval sits behind a port** (`adopt_ask.records.SearchRecords`), realised
  on SQLite FTS5 in the runtime annex (`adopt_store/annex/search.py`) and on
  Postgres full-text in the plane. Ranking, merging and branching live above the
  port and are shared verbatim. The index is derived and never canon.
- **`branch.compose` takes `Resolved` values**: a candidate welded to its
  freshness resolution. A caller cannot express "serve this, I did not check". The
  rule is a type. `tests/property/test_ask_serve_path_freshness.py` holds it.
- Only `verified` revisions serve as KNOWN. STALE is served **with** its cause.
  UNKNOWN is a refusal and never a guess.
- **Escalation vs logging:** passive question logging is off by default
  (`ASK_LOG_QUESTIONS`); `--escalate` stores the question text, and the act is
  the consent.
- **Capture is one transaction**: item, verified revision, provenance, bindings,
  audience tag, and the escalation closed. Capture must cost less than answering
  in Slack. An untagged capture once closed no gap and reached no pack; the
  audience tag is part of the write.
- **Synthesis is optional and grounded:** `adopt_ask.synthesis.ground` keeps a
  model's answer only if it cites revisions it was given; otherwise the
  extractive answer serves. Nothing synthesised is persisted.

## Drafting: one generation module (critical invariant #7)

`adopt_knowledge.drafting` is the single module that generates knowledge-shaped
text (Build 4 packs, Build 8 fix drafts). It sends one identity's store facts
and **nothing from the repository**; every fact carries a key a person can
resolve. `ground` discards the whole draft if it cites a single key it was not
given. Survivors land `unverified`, authored, bound, queued `draft:`, stamped
UNVERIFIED in packs, and confirmed only by a person. `DRAFT_VERIFICATION` has no
parameter that changes it. Prompts are immutable, versioned files
(`adopt-dev-contracts`).

## Packs

`adopt_handover`: `assemble` (a query that selects **confirmed** knowledge by
audience and kind) → `sections` (the stamp rule: unverified, stale or fresh,
plus a date) → `render` (a **byte-stable** Markdown pure function of the
assembled pack, which carries no clock) → `sidecar` (section → revision ids,
the lineage Build 8 regenerates by) → `derived` (DOCX/PDF through pinned
`pandoc`/`typst` subprocesses; content-equivalent, never canon; licence mode
`subprocess`). **This package reads and never writes, and does not depend on
`adopt-agent`**, so assembly is identical with no model configured. The gap
section and the boundary statement are always present. Unverified drafts render
under a banner, identified by `draft:` provenance, so harvest candidates never
reach a client document.

## The verified handover (Build 9)

`adopt_handover.event` is a recorded, strictly ordered, resumable state machine:
start (freeze, record the opening position) → elicit (gaps by owner) → pack →
verify (a checklist; failures become open questions) → snapshot (a bundle plus a
reproducible digest) → close (ownership transfers **with** the open items).
Two honesty rules: it cannot close with the system unowned, and unresolved items
transfer to named owners rather than closing to look complete.

## Tests and journeys

`knowledge-journey`, `ask-journey`, `pack-journey` and `handover-journey`, plus
`test_binding_honesty`, `test_ask_*`, `test_drafting`, `test_pack_*`,
`test_review_queue`, `test_handover_*` and `test_ingest_unverified`. A change to
what confirms, serves or renders re-runs its journey; the journey **is** the
build's definition of done.
