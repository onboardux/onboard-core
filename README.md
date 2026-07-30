# `adopt-core`

**The shared substrate for the Adoption-Phase Platform.** Apache-2.0.

A run-where-the-code-is setup tool. Local-first, offline by default, **zero
telemetry — permanently, with no opt-in switch to add later.**

> **Status: Build 0, sprint S0 complete.** Two repositories, sixteen packages,
> every CI gate wired and proven by a planted violation — and no product code
> yet. The schema arrives in S1. That order is deliberate: a gate added after
> the code it guards starts life with exemptions.

## What is here today

| Package | State |
|---|---|
| `adopt-const` | **Complete.** 41 tunables, one home, zero dependencies. |
| `adopt-obs` | **Complete.** Structured logging, the error registry, prefixed ULIDs, the field deny-list, the injectable clock. |
| `adopt-cli` | `adopt version`, `adopt doctor`. Config resolution with source reporting. |
| everything else | Declared, importable, empty. Each `__init__` states which sprint fills it and which invariants it will carry. |

## Quick start

```sh
uv sync --all-packages
uv run adopt doctor --json     # every config key with its resolved value and source
uv run adopt version --json
```

## The gates, and what each one exists to stop

Run them exactly as CI does:

```sh
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict packages/ scripts/ tools/
uv run lint-imports --config importlinter.ini      # all twelve import contracts
uv run python scripts/constants_sync.py --check
uv run python scripts/error_registry_sync.py --check
uv run python scripts/licence_gate.py --self-test  # proves the gate still rejects
uv run python scripts/licence_gate.py --check
uv run python scripts/ci_ratchet.py --budget unit -- uv run pytest -m unit
```

| Gate | Stops |
|---|---|
| `constants-sync` | A tunable inlined as a literal, a value drifting from its documented table, or one name declared in both constants modules. |
| `error-registry-sync` | A documented error code with no implementation, or an undocumented code a client can receive. Checked in both directions. |
| `licence-gate` | Copyleft linked into the distribution, a dependency denied by name, or a verification record missing any of its seven fields. Re-runs weekly, because dependencies relicense between our commits. |
| `lint-imports` | All twelve import contracts, including the four that are source rules rather than graph rules — no `CREATE TABLE` outside migrations, no `UPDATE` on a `*_revision` table, no coverage-cache write outside `adopt_coverage`, no impurity in a `@workflow` body. |
| `ci-ratchet` | A suite that grows past its runtime budget. A green suite over budget still fails. |

Two constants-sync waivers are visible in every run by design: a number that
genuinely differs from a tunable it happens to equal is waived inline with a
reason, and the waiver is **reported**, never hidden.

## Two repositories, and why

`adopt-core` is Apache-2.0 and public. `adopt-plane` — the Postgres realization,
scope enforcement and the DBOS backend — is closed and private. They are
separate repositories, not directories, and `adopt-plane` pins a *released*
`adopt-core` rather than vendoring it.

The boundary is treated as irreversible because it is: **Apache-2.0 cannot be
un-published.** A file pushed here is permissively licensed to the world from
that moment, permanently, and no later commit withdraws it. Placement is decided
before a file is created, not at review.

## Conventions worth knowing before the first pull request

- **Tunables live in `adopt_const` and nowhere else.** No number in prose, a
  docstring, a prompt, or a non-test source literal.
- **Schema-first, additive-only.** Every store or wire change starts in
  `schema/canonical.yaml` and the contracts document, in the same change. From
  the `0.3.0` tag, additive-only binds forever.
- **Nothing is updated in place.** Knowledge, bindings, identities and probe
  definitions are append-only revisions.
- **There is no free-text log parameter.** Events are stable snake_case names
  with structured fields, which is what makes "no client content in a log line"
  a structural property rather than a habit.
- **Every test passes the defect sentence** — *fails when ___ breaks; matters
  because ___; no other instrument catches it because ___* — before it is
  written. Test count and line coverage are banned as targets.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
Security posture and reporting: [`SECURITY.md`](SECURITY.md).
Dependency records: [`licence-verifications.md`](licence-verifications.md).
