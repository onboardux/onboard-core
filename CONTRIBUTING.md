# Contributing to `adopt-core`

Thanks for looking. This repository has a few conventions that are unusual
enough to be worth reading before you write code — most of them are enforced by
CI, so discovering them from a red build is the slow way round.

## Before you start

```sh
uv sync --all-packages
uv run pytest
```

If that passes, your environment is fine. Python 3.12 and
[uv](https://docs.astral.sh/uv/) are the only prerequisites.

## The five rules that surprise people

**1. A gate nobody has seen fail is a gate nobody should trust.**
Every gate here is proven by a planted violation. `scripts/plant_violation.py`
plants a real defect, you assert the gate rejects it, then `--revert` restores
the tree byte-exactly. If you add a gate, add its planted proof in the same
change — several scripts carry a `--self-test` that does exactly this.

**2. Every test must pass the defect sentence before it is written.**

> *"Fails when \_\_\_ breaks; matters because \_\_\_; no other instrument catches
> it because \_\_\_."*

If you cannot complete that sentence, the test is not worth its runtime.
**Test count and line coverage are banned as targets** — coverage survives only
as a floor alarm. The suite runtime is a hard ratchet: adding runtime means
removing equivalent runtime in the same pull request.

**3. Every tunable lives in `adopt_const`, and nowhere else.**
No number in prose, a docstring, a prompt, or a non-test source literal. `0`,
`1`, `2` and `-1` are exempt. A genuine exception is waived inline with
`# const-sync: ok -- <reason>`, and **every waiver prints on every run** — a
standing visible decision, never a silent escape.

**4. Schema changes start in `schema/canonical.yaml`.**
Never edit a generated file. Change the manifest, run `uv run adopt-schema
generate`, and all four targets regenerate together. From `0.3.0` the change
must be **additive-only**: a removed or retyped column is rejected by
`adopt-schema lint`.

**5. Nothing is updated in place.**
Identities, knowledge items, bindings and probe definitions are append-only
revision chains. There is no update path and no delete path, and two import
contracts (`no-revision-update`, `no-covered-cache-write`) exist because those
are the invariants a well-meaning one-line "fix" is most likely to break.

## Running what CI runs

```sh
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict packages/ scripts/ tools/ bench/
uv run lint-imports --config importlinter.ini      # twelve import contracts
uv run python scripts/licence_gate.py --check
uv run python scripts/no_destructive_sql.py
uv run adopt-schema generate --check
uv run python scripts/packaged_artifact.py --check  # installs a built wheel and uses it
uv run pytest
```

`packaged-artifact` is worth knowing about: it is the only gate whose subject is
a **built artefact** rather than the source tree. Every other gate, and the whole
test suite, runs against an editable install — which is exactly how a wheel that
could not create a store once passed everything.

## Adding a dependency

Check the licence **before** you write code against it, not after the gate
rejects it. Dependencies carry a four-mode policy: `in-binary` is
permissive-only, `subprocess` and `dev-only` permit copyleft. A dependency with
no row is treated as `in-binary` and **fails closed**. Add a row to
`licence-verifications.md` with all seven fields in the same change.

## Two jobs will be skipped on your pull request

`constants-sync` and `error-registry-sync` compare this repository against a
private design repository, so they are skipped for forks and Dependabot. That is
expected and does not block you — every self-contained gate still runs, and a
maintainer runs the other two before merge. `conformance-matrix` also needs
vendor credentials and is skipped without them.

## What does not belong here

Everything committed here is Apache-2.0 **from the moment it is pushed, and no
later commit withdraws it**. Do not contribute control-plane implementation,
client material, credentials, or anything not redistributable under Apache-2.0.
The operated control plane is a separate private repository and is never
vendored here.

## Reporting a vulnerability

Not through an issue or a pull request. See [SECURITY.md](SECURITY.md).
