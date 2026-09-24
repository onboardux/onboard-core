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

In Claude Code:

```text
/plugin marketplace add onboardux/onboard-core
/plugin install adopt@onboardux
```

Install it at **user scope**, never by copying the skills into a client's
repository: `adopt map` walks the repository, and anything in it — skill files,
templates, notes — is read as part of the client's system, permanently.

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
