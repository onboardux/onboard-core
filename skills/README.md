# `skills/` — the SKILL.md carrier library

**Build 0 owns the mechanism; items 1–4 fill this directory** (AI spec §6). It is
checked in empty on purpose: a carrier format with one example skill invented to
demonstrate it is a format defined by its example, and the first real skill then
inherits whatever that example happened to do.

## The format

```
skills/<name>/v<n>/
├── SKILL.md          # YAML frontmatter + body. Required.
├── scripts/          # optional — materialized into scratch, NEVER executed
├── references/       # optional
└── assets/           # optional
```

`SKILL.md` opens with a fenced frontmatter block declaring `name` (at most
`SKILL_NAME_MAX_CHARS`) and `description` (at most
`SKILL_DESCRIPTION_MAX_CHARS`). Both bounds live in `adopt_const`; the loader
reads them from there and `constants-sync` keeps them agreeing with
implementation spec §2.2.

## The three rules that are not conventions

1. **Skills are versioned by directory and never edited in place.** The same rule
   as prompts (AI spec §5.2, §6), for the same reason: `skill_sha256` binds a run
   to exact bytes, and an audit inside a client environment reconstructs what was
   asked from the digest. Editing `v1` makes every trace that names it wrong.

2. **The digest covers the whole directory**, over sorted relative paths and
   content. Reference material a prompt loads is part of what was asked, and a
   digest of `SKILL.md` alone would report two skills with different references
   as the same skill.

3. **`scripts/` are copied into the scratch directory and never executed by the
   loader** — and there is no API in `adopt_agent` that would execute one.
   Executing agent-generated code in-process is a permanently cut non-goal
   (PRD §10).

## Loading one

```python
from adopt_agent.skills import load_skill

skill = load_skill("extract-django/v1", root=Path("skills"), scratch=scratch_dir)
```

Anything malformed raises `MANIFEST_INVALID` **before** a provider call is
formed. A broken skill must cost nothing.
