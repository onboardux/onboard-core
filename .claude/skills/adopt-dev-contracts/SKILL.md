---
name: adopt-dev-contracts
description: How to change any contract adopt-core publishes — a table, column or enum value (schema/canonical.yaml and its four generated targets), a constant (adopt_const), an error code (adopt_obs.errors), an id prefix, a CLI command, flag or JSON key, or a model prompt — so the gates pass and nothing already shipped breaks. Use whenever a change adds or alters persisted state, the export bundle, a tunable, an error, the command surface, or prompt text in adopt-core, including "add a column", "new error code", "add a --flag", "change the default", or a failing schema-check, schema-lint, constants-sync, error-registry-sync or skills-sync job.
---

# Changing a contract

adopt-core publishes contracts that other people build on: stores sitting on
client machines, bundles in client archives, scripts that branch on exit codes,
agents that run its flags. From `0.3.0` on, **every one of them is
additive-only.** A removed column, a retyped field, a renamed flag or a changed
JSON key is a broken promise to someone who is not in the room. Each contract
has one authored home and a gate that fails when the home and its copies
disagree.

## The schema

`schema/canonical.yaml` is the only schema authority: 38 tables at
`schema_version: 4`, every enum, and each table's `scope_level` and
`exportable`. Four targets are **generated** from it and never hand-edited:

```text
canonical.yaml ─> schema/migrations/sqlite/…        (DDL; enums become CHECK constraints)
               ─> schema/migrations/postgres/…      (DDL + every row-level-security policy, derived from scope_level)
               ─> schema/export.schema.json         (the bundle's JSON Schema)
               ─> packages/adopt-model/src/adopt_model/   (Pydantic v2, extra="forbid")
```

To change it:

1. Edit `canonical.yaml`. Add only: a new table, a new nullable or defaulted
   column, or a new enum value. A new schema version arrives as a **per-version
   migration tranche**, so an existing store upgrades rather than being
   stranded. Never write an `ALTER` by hand, and never `DROP`.
2. `uv run adopt-schema generate`, then commit all four targets with the
   manifest.
3. `uv run adopt-schema generate --check` (no drift) and
   `uv run adopt-schema lint --base origin/main` (additive-only).
4. Ship the table's **first writer and first reader in the same change**. A table
   nobody writes is a table whose shape nobody tested.
5. Bump `schema_version` only for a new tranche. The package minor version tracks
   the schema version (`0.4.x` ships schema 4).

**Budget:** the whole v6 line budgets one new table (`coverage_gap`) and at most
one column. A change proposing more schema is re-litigating Build 0, and that is
a design decision, not an implementation one. Prefer the 31 tables Build 0
created empty.

`no-foreign-tables` forbids `CREATE TABLE` anywhere outside
`schema/migrations/**` and the emitters. `no-destructive-sql` forbids `DROP`,
`ALTER … DROP` and unpredicated `DELETE` in the store package.

## Constants

Every tunable lives in `adopt_const` (or `plane_const` in the private repository)
as `NAME: Final[...] = value`, and nowhere else. `adopt_const` imports nothing
(`const-leaf`). `uv run python scripts/constants_sync.py --check` fails when:

- the constants table and the module disagree;
- a name exists in both `adopt_const` and `plane_const`;
- **a numeric literal in non-test source equals a declared value.** Avoid the
  collision (a named regex group, for example, instead of a group index), or
  waive it inline with `# const-sync: ok -- <why this is a different number>`.
  Every waiver is printed on every run.

## Error codes

`AdoptError(ErrorCode.X, "message for a human", hint="what to do next")`. The code
is registered in `adopt_obs.errors` with exactly one category, and **the category
fixes the exit code**:

| Category | Exit |
|---|---|
| `usage` | 2 |
| `policy` | 3 |
| `integrity`, `transient`, `internal` | 1 |

To add one, add it to `ErrorCode` and `ERROR_CATEGORIES`, then run
`uv run python scripts/error_registry_sync.py --check`. Its `--check` compares the
module with the contracts registry in the private design pack when one is
configured; in CI that job runs only where the pack is reachable. Write the
`hint` for the person who hit the error: the fix, named. Codes are frozen once
released: never rename one, never recategorise one.

Ids are `<prefix>_<ULID>` from `adopt_obs.ids.new_id(prefix)`. A new prefix goes
into `PREFIX_REGISTRY`; an unregistered one is refused.

## The command surface

The CLI is part of the contract: command names, flags, JSON keys and exit codes.

- **Add; never rename or remove.** A new JSON key appears on **every** run, so
  the envelope keeps one shape whichever way a flag is set.
- **Register verbs lazily.** Import the domain package inside the command body.
  `CLI_COLD_START_MS` is a budget, and eager imports are what break it.
- **Never refuse with a parser exception.** The installed typer vendors its own
  click (`typer._click`), so a `typer.BadParameter` is not a
  `click.ClickException`. It escapes `adopt_cli.main` and exits `1` with a
  traceback and an empty stdout. Validate inside the command and raise a
  registered `AdoptError` instead. A unit test enforces this; see
  `scripts/plant_violation.py --kind bad-parameter`.
- **Help text is rendered by rich.** Square brackets in help text are markup;
  `0.4.0` shipped nine `--help` pages that crashed on them. Check
  `uv run adopt <command> --help`.
- **Regenerate what documents the surface, in the same change:**
  - `uv run python scripts/gen_skills.py` then `--check`. The FDE plugin's
    command tables, and a lint of every `adopt …` its skills show. A flag the
    skills name that the CLI lacks fails `skills-sync`.
  - The handbook's two generated chapters, from the design pack checkout:
    `uv run python ../tools/gen_docs.py --check`.
- Add a line under `[Unreleased]` in `CHANGELOG.md`.

## Prompts

`prompts/<id>/v<n>/` holds `SKILL.md` (the system text), `user.md` and
`output_schema.json`. `adopt_agent.skills` loads it, and its digest covers the
whole directory. **A merged prompt is never edited.** A change is a new version
directory with its golden-set result table, and callers name the version
explicitly; there is no "latest". This `SKILL.md` is product data, loaded at
runtime. It is not an agent skill; do not confuse the two.

## Before you push

```shell
uv run adopt-schema generate --check
uv run adopt-schema lint --base origin/main
uv run python scripts/constants_sync.py --check
uv run python scripts/error_registry_sync.py --check
uv run python scripts/gen_skills.py --check
uv run pytest -m unit
```
