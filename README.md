# `adopt-core`

The Apache-2.0, local-first substrate for the Adoption-Phase Platform. `adopt`
maps and initializes an engagement, maintains a portable SQLite knowledge store,
and exports that knowledge without requiring an operated service.

The CLI is offline by default and has no telemetry switch. Network access is an
explicit per-invocation choice, and client content is structurally excluded from
logs.

> **Status: `0.3.1` is released** — fifteen distributions on PyPI, plus three
> signed single-file binaries, a CycloneDX SBOM, SLSA provenance and the v3
> reference bundle on the
> [GitHub Release](https://github.com/onboardux/onboard-core/releases/tag/v0.3.1).
> A workflow artifact is still not a release; only the tagged, signed bundle is.
>
> **Upgrading from `0.3.0` is a straight replacement.** No schema change, so a
> `0.3.0` store opens unchanged. `0.3.1` fixes two defects: the single-file
> binary stamped `adopt-core/0.0.0+unknown` as provenance into every bundle and
> store it wrote, and two distributions were missing a dependency declaration so
> installing them alone raised `ModuleNotFoundError`. Neither affected a
> `pip install adopt-cli` journey. See [CHANGELOG.md](CHANGELOG.md).

## What is included

The uv workspace contains 16 publishable packages:

| Area | Packages |
|---|---|
| CLI and workflow | `adopt-cli`, `adopt-workflow` |
| Store and schema | `adopt-store`, `adopt-schema`, `adopt-model`, `adopt-identity`, `adopt-scope` |
| Analysis | `adopt-detect`, `adopt-map`, `adopt-coverage`, `adopt-freshness`, `adopt-policy`, `adopt-agent` |
| Portability and operations | `adopt-export`, `adopt-obs`, `adopt-const` |

The command surface includes `init`, `detect`, `map`, `boundary`, store
migration and inspection, identity parsing, coverage and freshness evaluation,
export/import, policy validation, adapter checks, `doctor`, and release
provenance reporting.

## Install

One package pulls in the fifteen the CLI needs:

```sh
pip install adopt-cli          # or: uv tool install adopt-cli
adopt version --json
```

That is seventeen of the eighteen distributions. `adopt-workflow` is a library
the CLI does not depend on, so it is published but not installed by the line
above.

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

# 4. Inventory what the system actually contains. Deterministic, offline.
adopt map ./myproject --json

# 5. Look at what you have. Looking never repairs.
adopt map --report --json
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

### `adopt map`

`map` walks the repository once and records what it finds as identities, each
with a canonical URI and provenance — the file and line span, the extractor and
its version. Three archetype packs ship: **generic** (declared dependencies,
config keys, environment variables and settings classes, scheduled jobs, CI
workflows, files of interest), **web** (HTTP endpoints, middleware and auth
boundaries, schema fields) and **ai** (prompt files and named prompts,
tool/function schemas, pinned model identifiers, retrieval and data-source
config, agent graph nodes). The archetype `init` recorded chooses the packs;
`--packs generic,web` overrides that for a mixed system.

```sh
adopt map ./myproject                             # exit 0
adopt map ./myproject --report                    # counts by kind, listing with provenance
adopt map ./myproject --check-expected list.txt   # exit 4, naming every miss
```

**No model is called, and nothing in the tree is executed or written.** Parsing
is `ast` and manifest-first; extractors receive a read-only view of the tree and
have no capability to do either.

**Re-running writes nothing when nothing changed.** Observation is keyed on the
URI, so the revision chain records what changed rather than how often you
scanned. A file that moves is recorded as a *move* when the evidence is
unambiguous — the old URI stays resolvable forever — and reported without being
written when it is not.

`--check-expected` takes a curated file of identity URIs, one per line with `#`
comments, and exits `4` naming every one that is absent. It is a recall floor
rather than a coverage percentage on purpose: a percentage improves when its
denominator shrinks, and a named list cannot be gamed that way. See
[tests/reference/](tests/reference/) for the two real repositories this is
proven against.

### `adopt ask`

`ask` answers from the store and tells you how much to trust the answer. Three
outcomes, and never a fourth:

```sh
adopt ask "why does the approval step exist on refunds?"
adopt ask "how do I rotate the API key?" --json
adopt ask "..." --reindex          # rebuild the retrieval index first
```

- **KNOWN** — the passages verbatim, each citing its `knowledge_revision` id,
  the identity URIs it is bound to, and the freshness rule that let it serve.
- **STALE** — the same answer, served *with* the cause: `STALE because
  load_bearing_identity_moved`. What was true before, and why it may not be now.
- **UNKNOWN** — a refusal. If matching text existed but was unconfirmed, it says
  so, because "nobody has written this" and "somebody wrote it and nobody
  confirmed it" send you to different places.

**All three exit `0`.** An honest refusal is a correct answer, not a failure;
scripts branch on the `branch` field of the `--json` payload. A boundary
refusal is different and exits `3` (`ASK_OUTSIDE_BOUNDARY`).

**No model is called.** The answer is quotation with attribution — the store
already holds prose a human wrote and confirmed. Retrieval is SQLite FTS5 in the
runtime annex, a derived index rebuilt whenever it disagrees with the store,
never exported and never canon.

**Only confirmed knowledge is ever cited**, and every answer passes a freshness
resolution before it is composed — that check is a type signature rather than a
step, so there is no code path around it.

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
- A valid release includes 18 wheels, 18 source distributions, three onefile
  binaries, a CycloneDX SBOM, SLSA provenance, and keyless cosign evidence.
  `scripts/release_context.py` holds the canonical set and the gate asserts it
  on every pull request, so that module is the authority and this line is a
  description of it.

## Repository boundary

Everything committed here is offered under Apache-2.0. The operated control
plane is a separate private repository and is never vendored here. Contributions
must not contain control-plane implementation, client material, credentials, or
other non-redistributable content.

## Licence and security

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Security reporting and
release verification are documented in [SECURITY.md](SECURITY.md); dependency
evidence is in [licence-verifications.md](licence-verifications.md).
