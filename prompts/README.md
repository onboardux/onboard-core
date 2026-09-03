# `prompts/` — immutable prompt versions

AI spec §5, placed here by `03` §1.2 (CR-44). **Build 0 contained exactly one
production prompt**, `detect-001@1`, and that ratio was deliberate: expensive and
non-deterministic work stays off the critical path, and the substrate is nothing
but critical path. Build 3 added `ask-001/v1`, Build 4 added `draft-001/v1`, and
Build 5 adds `probe-001/v1`; all three are optional passes over work the
deterministic path has already completed, so the ratio still holds where it
matters -- nothing here is required for a command to produce its output. A probe
made only of `http` steps runs to completion with no adapter configured at all.

## The layout

```
prompts/<id>/v<n>/
├── SKILL.md            # frontmatter + the verbatim SYSTEM text
├── user.md             # the verbatim USER template, rendered with the request's inputs
└── output_schema.json  # the prompt's strict, closed output schema
```

**A prompt version directory is a skill-format carrier** *(CR-47)*. AI spec §5.2
rule 3 says `skill_sha256` "binds every run to exact **prompt** bytes", which is
only true if the prompt is what the loader loaded — so `AgentRequest.skill_ref`
points here and `adopt_agent.skills.load_skill` reads it. The alternative would
have been a second loader, a second digest field and two answers to "what was
asked", which is the drift the whole version discipline exists to prevent.

The digest covers **the whole directory**, so `user.md` and `output_schema.json`
are inside `skill_sha256` alongside the system text. A prompt whose schema changed
is a different prompt, and the digest says so without anyone deciding to record it.

## The three rules that are not conventions

1. **A prompt file is immutable once merged.** A change is a new version id;
   `v1` stays in the repository. `detect-001@1` means one byte sequence, forever.
   A reflowed line is a different prompt with the same id, which is precisely what
   the digest exists to make impossible to miss.
2. **Callers name the version explicitly. There is no "latest".**
3. **A new prompt version requires its golden-set result table in the PR.** A
   prompt merged without eval evidence is a merge-blocker (AI spec §5.2 rule 4).
   `tests/golden_prompts/detect_001/` is where that evidence lives; it is
   **reported and not gating** at Build 0, because a 15-item set cannot support a
   blocking threshold and inventing one would be fake precision.

## `detect-001@1`

| | |
|---|---|
| Caller | `adopt_detect.disambiguate`, only when detection is ambiguous **and** `ADOPT_FEATURE_AGENT_DISAMBIGUATION` is on |
| Output schema | `ArchetypeProposal` — `primary` from `02` §2.1's `archetype` vocabulary |
| Budget | `AGENT_DETECT_MAX_USD` / `AGENT_DETECT_MAX_WALL_SECONDS` (`03` §2.2) |
| Sends | per-archetype scores, the rules that fired with their paths, a bounded directory listing |
| Never sends | **file contents, or any source code** |

That last row is a privacy invariant rather than a token-saving measure: it is what
lets the offline and no-content-leaves-the-environment claims survive a security
review **even when the flag is on** (AI spec §4). And whatever the model proposes,
**a human must accept it before anything is written** — PRD §8 allows no confidence
exemption.

## `ask-001/v1`

| | |
|---|---|
| Caller | `adopt_ask.synthesis.synthesize`, only when an answer already has citations and an adapter is configured |
| Output schema | `{answer_md, cited_revision_ids}` |
| Budget | `AGENT_ASK_MAX_USD` / `AGENT_ASK_MAX_WALL_SECONDS` (`03` §2.2) |
| Sends | the question, and the passages the freshness check already approved |
| Never sends | withheld unverified revisions, or anything the extractive answer would not have shown |

**Nothing it produces is persisted.** Synthesis is a rendering: an ungrounded
output is discarded and the extractive answer -- which is already complete --
serves instead (critical semantic invariant #7).

## `draft-001/v1`

| | |
|---|---|
| Caller | `adopt_knowledge.drafting.draft_one`, from `adopt pack --draft-missing` and `adopt draft <uri>` |
| Output schema | `{body_md, cited_facts, unknowns}` |
| Budget | `AGENT_DRAFT_MAX_USD` / `AGENT_DRAFT_MAX_WALL_SECONDS` (`03` §2.2) |
| Sends | one referent's canonical URI and kind, the facts the store already holds about it, and the audience |
| Never sends | repository contents. Everything sent was already observed into the store by the deterministic mapper or written there by a human |

**What it produces *is* persisted, and that is what makes it the strictest of the
three.** A surviving draft lands as an `unverified` knowledge revision bound to
its identity, so invariant #7's discard rule is the only thing between a
fabricated citation and a client's handover pack. Every citation is checked
against the exact fact set that was sent; one foreign key discards the whole
draft. A landed draft renders with an UNVERIFIED banner and becomes confirmed
only when a human confirms it in `adopt review`.

## `probe-001/v1`

| | |
|---|---|
| Caller | `adopt_probe.runner._run_prompt_step`, from `adopt probe run` |
| Output schema | **none, deliberately** — the reply is recorded verbatim. See below |
| Budget | the **probe's own** `cost` block (`max_model_calls`, `max_tokens`), not a programme default |
| Sends | one probe step's authored `input` text, verbatim |
| Never sends | anything else. Not the store, not the repository, not the other steps' results |

**The thinnest prompt here, and deliberately so.** A behavioural probe records
what the client's model deployment *does*; instructions of ours added to the
interaction would be recorded as part of that behaviour and compared against the
baseline forever. So the skill text says only "answer the supplied text directly,
add nothing, prefer the plain phrasing" — the rules exist to keep our own
contribution constant, not to shape the answer.

**It declares no output schema, and the first real-model run is what proved it
must not.** `probe-001/v1` originally carried `output_schema.json` requiring
`{"reply": "..."}`. The seam **validates** an output schema but never sends it to
the provider — a prompt has to ask for the shape it wants — and this skill's text
says the opposite: *answer directly, in your own words, add nothing*. A real model
obeyed the text, failed the schema, burned the single retry `04` §3 allows and
returned `status=error`. **`fake_recorded` could not show it**: the recorded fake
replays a scripted `{"reply": ...}` whatever it is sent, so the contradiction
between the prompt and its own schema was structurally invisible in CI, which is
CR-51's finding arriving a second time. The schema was the defect, not the model:
a probe records what the system said, so the reply is now taken verbatim as text.

**Its output never becomes knowledge.** Unlike `draft-001`, nothing here lands as
a knowledge revision: the reply is recorded as a `probe_observation`,
fingerprinted, and read only as *what the system said when asked this*. The
budget is the probe's because a probe is a document a human approved, and a
programme-wide default would let one probe spend on the authority of nobody.
