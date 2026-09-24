---
name: adopt-dev-gates-release
description: The CI gates adopt-core runs, how to run them exactly as CI does, the planted-violation discipline that proves each one can fail, the dependency licence policy, workspace distribution rules, the runtime ratchets, the packaged-artifact check, and how a release is cut (lockstep versions, the frozen distribution set, signed wheels and binaries, provenance). Use whenever adding or changing a gate, adding a dependency or a workspace package, a CI job fails, preparing or debugging a release, or asking "which checks must pass before I push" in adopt-core.
---

# Gates and release

**A gate nobody has seen fail is a gate nobody should trust.** This repository has
repeatedly found instruments reporting success because they had nothing to
measure: a coverage floor over the wrong denominator, a discovery step blind to
submodules reporting `0/0 covered (100%)`, a benchmark that never ran its
budget. So every gate here ships with a way to watch it fail, and a new gate
gets one too.

## The sweep: run it before you push

From `adopt-core/`, exactly as CI runs it:

```shell
uv sync --all-packages
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict packages/ scripts/ tools/ bench/ plugins/
uv run lint-imports --config importlinter.ini            # thirteen import contracts
uv run python scripts/constants_sync.py --check
uv run python scripts/error_registry_sync.py --check
uv run python scripts/licence_gate.py --self-test && uv run python scripts/licence_gate.py --check
uv run python scripts/no_destructive_sql.py --self-test && uv run python scripts/no_destructive_sql.py
uv run python scripts/first_party_deps.py --self-test && uv run python scripts/first_party_deps.py --check
uv run python scripts/gen_skills.py --self-test && uv run python scripts/gen_skills.py --check
uv run adopt-schema generate --check
uv run adopt-schema lint --base origin/main
uv run pytest -m unit
uv run pytest -m property --hypothesis-seed=0
```

CI adds the seven journeys (`map-`, `knowledge-`, `ask-`, `pack-`, `probe-`,
`refresh-` and `handover-journey`), `golden-g0`, `append-only`, `durability`,
`packaged-artifact`, `coverage-floor`, `conformance-matrix` and more. Every
required job is named in `REQUIRED_JOBS` in `tests/unit/test_ci_gates.py`. **A
job not named there can be deleted without any instrument noticing.** When you
add a gate or a journey, add its job **and** its name there in the same change.

## Planted violations

```shell
uv run python scripts/plant_violation.py --kind <kind>   # plant; the matching gate must now fail
uv run python scripts/plant_violation.py --revert        # restores every file byte-for-byte
```

The kinds are `revision-update`, `drop-column`, `provider-sdk`,
`covered-cache-write`, `probe-io` and `bad-parameter`. A new gate needs either a
kind here or its own `--self-test` that plants and requires failure. **Plant
where discovery is hardest** (a submodule, not the package root), or the
self-test proves nothing about the discovery bug it exists to catch.

## The import contracts

`no-raw-sqlite` (only `adopt_store.sqlite`) · `no-provider-sdk` (only
`adopt_agent.adapters.*`, and no vendor SDK at all; adapters speak raw HTTP) ·
`no-dbos` · `no-raw-psycopg` · `no-vector-impl` · `core-independent` (no
`adopt_*` → `plane_*`) · `const-leaf` · `generated-purity` · `no-foreign-tables`
· `no-revision-update` · `no-covered-cache-write` · `workflow-body-purity` ·
`probe-io`. The last four are custom source rules in `tools/contracts/`. Name
stdlib modules at top level in a contract; grimp collapses submodules.

## Dependencies and licences

Four modes (`licence-verifications.md`, with seven fields per row):

| Mode | Allows |
|---|---|
| `in-binary` | permissive only; the default for a dependency with **no row**, so it fails closed |
| `subprocess` | copyleft allowed, invoked as a separate process; also listed in `subprocess-deps.toml` (pandoc, typst) |
| `dev-only` | copyleft allowed; never shipped |
| `service-side` | the private plane only; copyleft allowed but not AGPL or SSPL |

Add the row in the same change as the dependency. The binary packer is Nuitka;
PyInstaller is refused because its bootloader ships inside the artifact.

## Workspace distributions

- **Every first-party import is a declared dependency of the distribution that
  imports it** (`first-party-deps`). The workspace installs everything, so only
  this gate sees a package that works only because a sibling happens to be
  present.
- A **new distribution** is a one-line ratification in `release_context.py`'s
  `CANONICAL_DISTRIBUTIONS`, which the release enforces exactly. Claim its name
  on PyPI first. Check it with `https://pypi.org/simple/<name>/`: the JSON API
  answers 404 for a reserved project with no releases, and `/project/<name>/`
  answers 200 for names that do not exist.
- The CLI registers verbs lazily, and `CLI_COLD_START_MS` is a measured budget.

## Ratchets and budgets

`scripts/ci_ratchet.py` makes suite runtime a hard budget: adding runtime means
removing some. Durations are **judged only on the reference runner**
(`bench/RUNNER.md`); a laptop reading is diagnostic, never a gate result.
`coverage_floor.py` is an alarm floor, never a target.

## Things that must never be made to pass

- `conformance-matrix` needs two non-test adapters green against real
  providers. A red caused by an expired credential is fixed by rotating the
  credential, never by code that lets an authentication error through.
- `packaged-artifact` installs what ships and uses it from outside the checkout.
  Its subject is the artifact, not the source tree, which is why it found
  packaging defects every source-tree test passed.

## Cutting a release

The machinery is `.github/workflows/release.yml` and `scripts/release_context.py`.
The ordering has already cost real attempts:

1. The workspace version is **lockstep** across every distribution; the minor
   version tracks `schema_version`. Update `CHANGELOG.md`, and regenerate the
   plugin (`scripts/gen_skills.py`), whose version is the CLI's.
2. Run a strict `publish=false` dry run on `main`: wheels, sdists, three Nuitka
   binaries **packed from the built wheels** (an editable install maps nothing),
   size ceilings, SBOM, cosign signatures, SLSA provenance, and an exact
   artifact inventory.
3. The release environment's **tag allow-list is hand-maintained**. Add the
   exact new tag **before** pushing it, or the publish is refused in one second
   with no runner, which reads like an infrastructure glitch.
4. Confirm every distribution name carries its trusted publisher. `skip-existing`
   is false, so a publish that fails on the last name strands the rest at a
   version PyPI will never let you reuse.
5. Push the tag, let its strict run go green, then dispatch `publish=true` on that
   tag. Verify **from the published artifact**: install from PyPI and check
   `adopt version --json` reports a stamped `build_id`. Exporting a bundle with
   the shipped binary is what once caught a binary stamping the wrong version into
   everything it wrote.
