# `adopt-core`

The Apache-2.0, local-first substrate for the Adoption-Phase Platform. `adopt`
maps and initializes an engagement, maintains a portable SQLite knowledge store,
and exports that knowledge without requiring an operated service.

The CLI is offline by default and has no telemetry switch. Network access is an
explicit per-invocation choice, and client content is structurally excluded from
logs.

> **Status: `0.3.0` is released** — fifteen distributions on PyPI, plus three
> signed single-file binaries, a CycloneDX SBOM and SLSA provenance on the
> [GitHub Release](https://github.com/onboardux/onboard-core/releases/tag/v0.3.0).
> A workflow artifact is still not a release; only the tagged, signed bundle is.
>
> **Known issue in `0.3.0`.** `adopt-store` imports `adopt_identity` and
> `adopt-cli` imports `adopt_model` without declaring them, so installing one of
> those distributions **on its own** raises `ModuleNotFoundError` on first use.
> `pip install adopt-cli` — the supported path below — is **unaffected**, because
> it declares `adopt-identity` itself and reaches `adopt-model` transitively.
> Fixed on `main`; see [CHANGELOG.md](CHANGELOG.md).

## What is included

The uv workspace contains 15 publishable packages:

| Area | Packages |
|---|---|
| CLI and workflow | `adopt-cli`, `adopt-workflow` |
| Store and schema | `adopt-store`, `adopt-schema`, `adopt-model`, `adopt-identity`, `adopt-scope` |
| Analysis | `adopt-detect`, `adopt-coverage`, `adopt-freshness`, `adopt-policy`, `adopt-agent` |
| Portability and operations | `adopt-export`, `adopt-obs`, `adopt-const` |

The command surface includes `init`, `detect`, `boundary`, store migration and
inspection, identity parsing, coverage and freshness evaluation, export/import,
policy validation, adapter checks, `doctor`, and release provenance reporting.

## Install

One package pulls in the thirteen the CLI needs:

```sh
pip install adopt-cli          # or: uv tool install adopt-cli
adopt version --json
```

That is fourteen of the fifteen distributions. `adopt-workflow` is a library the
CLI does not depend on, so it is published but not installed by the line above.

Or download a single-file binary — `adopt-linux-x86_64`, `adopt-macos-arm64` or
`adopt-windows-x86_64.exe` — from the GitHub Release. It needs no Python.
**Verify it before you run it**; [SECURITY.md](SECURITY.md) has the exact
`cosign` and attestation commands.

To run from a checkout instead — Python 3.12 and
[uv](https://docs.astral.sh/uv/):

```sh
uv sync --all-packages
uv run adopt --help
```

## First run

`adopt` reads a tree, classifies it, and records what it may observe. Nothing is
sent anywhere: the CLI is offline unless you pass `--allow-network`.

```sh
# 1. What kind of system is this? Detection refuses rather than guesses.
adopt detect ./myproject --json

# 2. Answer the three qualification questions.
cat > answers.json <<'JSON'
{"artifact_access": true, "deploy_signal": true, "safe_interaction": true}
JSON

# 3. Create the store, resolve the scope, declare the boundary.
adopt init ./myproject \
  --scope northwind/acme-erp/support-agent/prod \
  --answers answers.json --json

# 4. Look at what you have. Looking never repairs.
adopt store info --json
adopt doctor --json
```

`--scope` is four immutable slugs — firm, engagement, system, environment — and
all four are required, because a boundary is declared for one environment of one
system. `init` reports the archetype, the negotiated tier and a `boundary_id`.

**If `detect` exits non-zero with `DETECT_AMBIGUOUS`, that is the design.** A
wrong archetype is a different set of extractors, not a slightly wrong answer, so
detection refuses and ranks instead of guessing. Narrow the path to one system,
or accept an archetype yourself with `adopt init --archetype <a>`. A proposal is
never a decision: nothing is written until a human names it.

Development checkouts intentionally report `null` for `sbom_sha256` and
`build_id`. A signed release wheel or binary embeds those immutable build facts.

## Validate a checkout

```sh
uv lock --check
uv sync --frozen --all-packages
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict packages/ scripts/ tools/ bench/
uv run lint-imports --config importlinter.ini
uv run pytest
uv run python scripts/licence_gate.py --check
uv run python scripts/packaged_artifact.py --check
```

Two maintainer-only consistency jobs compare this repository with private design
and control-plane repositories. They are skipped for forks and are not required
to build, test, or use `adopt-core`; all self-contained product gates continue to
run on public contributions.

## Design invariants

- Tunables live in `adopt_const`; application code does not duplicate them as
  literals.
- Store and wire evolution is schema-first and additive-only from `0.3.0`.
- Knowledge, bindings, identities, and probe definitions use append-only
  revisions.
- Offline mode opens no socket except a configured local model endpoint.
- Stable structured events replace free-text logging, and deny-listed content
  fields are dropped at the sink.
- Every distributed dependency is licence-verified. The in-binary policy is
  permissive-only.
- A valid release includes 15 wheels, 15 source distributions, three onefile
  binaries, a CycloneDX SBOM, SLSA provenance, and keyless cosign evidence.

## Repository boundary

Everything committed here is offered under Apache-2.0. The operated control
plane is a separate private repository and is never vendored here. Contributions
must not contain control-plane implementation, client material, credentials, or
other non-redistributable content.

## Licence and security

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Security reporting and
release verification are documented in [SECURITY.md](SECURITY.md); dependency
evidence is in [licence-verifications.md](licence-verifications.md).
