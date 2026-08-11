# `adopt-core`

The Apache-2.0, local-first substrate for the Adoption-Phase Platform. `adopt`
maps and initializes an engagement, maintains a portable SQLite knowledge store,
and exports that knowledge without requiring an operated service.

The CLI is offline by default and has no telemetry switch. Network access is an
explicit per-invocation choice, and client content is structurally excluded from
logs.

> **Status:** `0.3.0` release candidate. The implementation and release
> machinery are complete enough for strict public validation, but no `0.3.0`
> tag or PyPI release exists yet. See the repository's release and security
> pages for published artifacts; do not treat a workflow artifact as a release.

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

## Quick start

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required.

```sh
uv sync --all-packages
uv run adopt --help
uv run adopt doctor --json
uv run adopt version --json
```

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
