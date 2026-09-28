# `adopt` — agent skills for driving the adopt CLI

Seven skills that let a coding agent (Claude Code, or any agent that reads
`SKILL.md`) run the published `adopt` CLI on a client engagement: onboard a
repository, capture and serve knowledge, baseline behaviour, triage drift, run a
verified handover, and connect to an Onboard control plane.

**The CLI is the capability; the skills are the judgement.** Nothing here
re-implements `adopt` or writes to its store by any other route. The skills
supply what the CLI deliberately leaves to a person — the scope, the archetype
when detection is unsure, which identities matter, what a probe should assert,
and every decision in the review queue — and they make the agent ask before
anything permanent.

## Install

**Always at user scope, never into a client's repository.** `adopt map` walks
the repository, and anything in it — skill files, templates, notes — is read as
part of the client's system, permanently. Measured: seven skills installed into
a 21-file client repository became 259 extra identities on the next `map`, one
for every key of `surface.json` and every template, in a store that deletes
nothing.

With the [skills CLI](https://github.com/vercel-labs/skills), from anywhere:

```text
npx skills add onboardux/onboard-core -g -a claude-code \
    -s adopt-cli adopt-onboard adopt-capture adopt-probes adopt-watch adopt-handover adopt-connect
```

- **`-g` is the one flag that matters.** Without it the CLI installs at project
  scope — `.agents/skills/`, `.claude/skills/` and a `skills-lock.json` in the
  current directory — and it does so without asking when an agent runs it. If
  that has happened in a client repository, undo it **before** the next
  `adopt map`: `npx skills remove -s adopt-cli adopt-onboard adopt-capture
  adopt-probes adopt-watch adopt-handover adopt-connect -y` (by name — `'*'`
  would take the client's own skills too), then delete whatever `git status`
  still shows as untracked from the install, typically `skills-lock.json`.
- `-s` takes the names space-separated; a comma-separated list matches nothing.
- `-a claude-code` limits the install to one agent; drop it to be asked.

Or in Claude Code, as a plugin:

```text
/plugin marketplace add onboardux/onboard-core
/plugin install adopt@onboardux
```

The `adopt-cli` skill's preflight refuses to report ready while these skills sit
inside the work tree it is checking.

The CLI itself is installed separately, with the person's agreement; the
`adopt-cli` skill's preflight tells the agent how (`pip`/`uv`/`pipx`, or the
signed standalone binary for machines without Python 3.12).

## The skills

| Skill | Use it for |
|---|---|
| `adopt-cli` | Start here. Preflight, the JSON and exit-code contract, where output may go, what needs a person's yes, and which skill does what. |
| `adopt-onboard` | First day on a system: detect, scope, boundary, init, map, the recall list, ingest, harvest, first gaps. |
| `adopt-capture` | The daily loop: ask, escalate, bank a person's answer, review queue, bindings, gap dispositions. |
| `adopt-probes` | Behaviour baselines: author, run, baseline and diff declarative probes. |
| `adopt-watch` | After the system changes: refresh, classify, resolve change items. |
| `adopt-handover` | Packs per audience, optional drafting, the six-step verified handover. |
| `adopt-connect` | Operated systems: remote mode, `adopt pull`, remote capture, `ci-sense` in the client's CI. |

## Versioning

The plugin's version **is** the CLI's version it was generated against. Command
tables, flags and error codes inside the skills are generated from the CLI by
`scripts/gen_skills.py` and checked in CI, so a skill can never name a flag the
CLI lacks. The CLI's surface is additive-only from `0.3.0`, so these skills work
with any CLI at or above their floor (`0.4.1`); features newer than an installed
CLI (for example `ingest --unverified`) are detected by preflight, and each skill
says what to do without them.

## Maintaining

```shell
cd adopt-core
uv run python scripts/gen_skills.py             # regenerate the generated parts
uv run python scripts/gen_skills.py --check     # fail on drift (CI runs this)
uv run python scripts/gen_skills.py --self-test # prove the check can fail
```

Never hand-edit `skills/adopt-cli/references/commands.md`,
`skills/adopt-cli/references/errors.md`, `skills/adopt-cli/scripts/surface.json`
or anything between `<!-- BEGIN GENERATED -->` markers. Everything else is
written by people and reviewed like code. Scenario evals live in
`plugins/evals/`, outside the plugin, so they are never installed.
