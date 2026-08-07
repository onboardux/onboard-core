# `prompts/` — immutable prompt versions

AI spec §5, placed here by `03` §1.2 (CR-44). **Build 0 contains exactly one
production prompt**, `detect-001@1`, and that ratio is deliberate: expensive and
non-deterministic work stays off the critical path, and the substrate is nothing
but critical path.

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
