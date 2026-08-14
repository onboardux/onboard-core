# Licence verifications — `adopt-core`

**Every third-party dependency carries a row here before it may appear in any
manifest.** A row missing any of the seven required fields blocks the build:
`repository`, `version`, `licence hash`, `security status`, `usage mode`,
`owner`, `re-verification date`. A partial record is worse than an absent one,
because it reads as verified in a review.

Enforced by `scripts/licence_gate.py`, run on every pull request and again
**weekly on a schedule** — dependencies relicense between our commits, and a
gate that only fires on a diff will not notice.

## How to verify a licence

Read the `LICENSE` file **in the dependency's own repository**, at the version
pinned in `uv.lock`. A package-index summary is a rendering of metadata the
project supplied, not the licence itself, and the two have disagreed before.
Record the digest of the declared licence expression in the `Licence hash`
column so that a silent relicense shows up as a hash change.

## Usage modes and what each one permits

| Usage mode | Meaning | Licence rule |
|---|---|---|
| `in-binary` | Linked into the wheel or the single-file binary | **Permissive only.** No copyleft, ever. |
| `subprocess` | Invoked across a process boundary; never linked | Copyleft permitted, and the invocation site must be declared in `subprocess-deps.toml` |
| `dev-only` | Development or test dependency; never shipped to a user | Copyleft permitted. Promoting one of these to a runtime dependency is a policy violation, not a refactor. |
| `service-side` | Runtime dependency of a service that is **never distributed** | Copyleft permitted **except AGPL and SSPL**, whose obligations are triggered by network use. Used only in `adopt-plane`; no row in this file carries it, because everything here ships. *(CR-29)* |

A dependency with **no row** is treated as `in-binary` — the strictest rule.
Failing closed is the point: an undeclared dependency must never be the lenient
case.

## Denied by name

| Denied component | Reason |
|---|---|
| CodeQL CLI engine | Its terms forbid analysis of non-open-source code, which is this product's entire purpose. No usage mode makes it compliant. |
| Semgrep maintained / Pro rule sets | Internal-use only and non-redistributable. Semgrep CE itself remains available as a declared subprocess; it is the maintained rules that cannot ship. |

## Verification records

**Security status** is `clean-2026-08-07` for every row below except the four
S1.4 added, which carry `clean-2026-08-14`: `pip-audit` was run against the whole
resolved tree on each of those dates and reported no known vulnerability. The date
is per-row rather than global because it records **when that row's dependency was
last audited**, and back-dating a new row to an older sweep would claim an audit
that never saw it. `licence_gate.py --check --strict-verify` — the release gate at
S9 — **rejects `pending-audit`**, which is what these rows said until the audit
ran.

**The audit found one real vulnerability and it was fixed rather than accepted.**
`pytest` 8.4.2 carried `PYSEC-2026-1845`, fixed in 9.0.3. It would have been
defensible to accept it — `pytest` is `dev-only` and never enters the wheel or
the binary, so the exposure was to our own CI rather than to any client — but the
upgrade turned out to cost nothing: the pin moved to `>=9.0.3,<10` in **both**
repositories, resolution landed on 9.1.1, and all 710 tests, the cross-repository
durability drill, the conformance suite, the seeded property run and the unit
ratchet were green with no code change. Accepting a risk you can remove for free
is how a `dev-only` exemption becomes a habit.

**How the audit is run**, and why it adds no rows to this file:

```sh
uv export --format requirements-txt --no-hashes --all-groups -o reqs.check.txt
uvx pip-audit@2.9.0 -r reqs.check.txt
```

`uvx` runs `pip-audit` in an **isolated environment**, so its own dependency tree
— roughly twenty packages — never enters `uv.lock` and never needs a row here. A
scanner added as a dev dependency would have made every one of its transitive
dependencies a verification obligation, which is a poor trade for a tool that only
reads a list.

**The audited set is proven to be this set.** The distribution rows below and the
third-party distributions in the export match exactly, in both directions, with no
first-party package counted — otherwise a row could be marked clean here having
never been audited. **Current: 36 and 36**, verified by
`licence_gate.py --check --strict-verify`, which fails on either direction. Do not
quote that figure from this sentence — run the gate. *(S1.4 added **four** —
`ast-grep-py`, `graphql-core`, `tree-sitter`, `tree-sitter-language-pack` — all
MIT, each read from its own repository's `LICENSE` at the pinned version, and each
audited by the `pip-audit` run above on 2026-08-14, which reported no known
vulnerability. Three further libraries `03` §2 names were **declined**: see
`docs/pack/OPEN-DECISIONS.md` OD-12.)*

**`tree-sitter-language-pack` vendors its grammars**, so its own MIT row is not the
whole story for a reviewer: the wheel carries compiled grammars whose upstream
licences travel inside it rather than as separate distributions the gate can see.
Every grammar this build loads — `python`, `javascript`, `typescript`, `proto`,
`graphql` — is MIT or Apache-2.0 upstream, all `in-binary`-legal. **Stated because
the gate cannot check it**: a bundled artefact is invisible to a check that reads
distribution metadata, and the honest response is to name the blind spot rather
than let the MIT row imply a coverage it does not have. Re-verify at the same
2026-10-30 date as the row.

| Dependency | Repository | Version | Licence hash | Security status | Usage mode | Owner | Re-verification date | Licence |
|---|---|---|---|---|---|---|---|---|
| `annotated-doc` | https://github.com/fastapi/annotated-doc | 0.0.5 | `e5dcffe836b6ec8a` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | MIT |
| `annotated-types` | https://github.com/annotated-types/annotated-types | 0.8.0 | `e5dcffe836b6ec8a` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | MIT |
| `ast-grep-py` | https://github.com/ast-grep/ast-grep | 0.45.1 | `e5dcffe836b6ec8a` | clean-2026-08-14 | in-binary | onboardux | 2026-10-30 | MIT |
| `click` | https://github.com/pallets/click | 8.4.2 | `118dbcd2d5c9f9b2` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | BSD-3-Clause |
| `colorama` | https://github.com/tartley/colorama | 0.4.6 | `684e9824f05014cf` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | BSD-3-Clause |
| `coverage` | https://github.com/coveragepy/coveragepy | 7.15.4 | `2af71558e438db0b` | clean-2026-08-10 | dev-only | onboardux | 2026-10-30 | Apache-2.0 |
| `graphql-core` | https://github.com/graphql-python/graphql-core | 3.2.11 | `e5dcffe836b6ec8a` | clean-2026-08-14 | in-binary | onboardux | 2026-10-30 | MIT |
| `grimp` | https://github.com/seddonym/grimp | 3.15 | `684e9824f05014cf` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | BSD-3-Clause |
| `hypothesis` | https://github.com/HypothesisWorks/hypothesis | 6.164.0 | `09962c1dc23fac80` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | MPL-2.0 |
| `import-linter` | https://github.com/seddonym/import-linter | 2.13 | `684e9824f05014cf` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | BSD-3-Clause |
| `iniconfig` | https://github.com/pytest-dev/iniconfig | 2.3.0 | `e5dcffe836b6ec8a` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | MIT |
| `librt` | https://github.com/mypyc/librt | 0.13.0 | `e5dcffe836b6ec8a` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | MIT |
| `markdown-it-py` | https://github.com/executablebooks/markdown-it-py | 4.2.0 | `e5dcffe836b6ec8a` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | MIT |
| `mdurl` | https://github.com/executablebooks/mdurl | 0.1.2 | `e5dcffe836b6ec8a` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | MIT |
| `mypy` | https://github.com/python/mypy | 1.20.2 | `e5dcffe836b6ec8a` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | MIT |
| `mypy-extensions` | https://github.com/python/mypy_extensions | 1.1.0 | `e5dcffe836b6ec8a` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | MIT |
| `packaging` | https://github.com/pypa/packaging | 26.2 | `f83c2e92648be1cb` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | Apache-2.0 OR BSD-2-Clause |
| `pathspec` | https://github.com/cpburnz/python-pathspec | 1.1.1 | `09962c1dc23fac80` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | MPL-2.0 |
| `pluggy` | https://github.com/pytest-dev/pluggy | 1.6.0 | `e5dcffe836b6ec8a` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | MIT |
| `py-cpuinfo` | https://github.com/workhorsy/py-cpuinfo | 9.0.0 | `e5dcffe836b6ec8a` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | MIT |
| `pydantic` | https://github.com/pydantic/pydantic | 2.13.4 | `e5dcffe836b6ec8a` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | MIT |
| `pydantic-core` | https://github.com/pydantic/pydantic-core | 2.46.4 | `e5dcffe836b6ec8a` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | MIT |
| `pygments` | https://github.com/pygments/pygments | 2.20.0 | `248dd895a2f89e28` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | BSD-2-Clause |
| `pytest` | https://github.com/pytest-dev/pytest | 9.1.1 | `e5dcffe836b6ec8a` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | MIT |
| `pytest-benchmark` | https://github.com/ionelmc/pytest-benchmark | 5.2.3 | `248dd895a2f89e28` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | BSD-2-Clause |
| `pyyaml` | https://github.com/yaml/pyyaml | 6.0.3 | `e5dcffe836b6ec8a` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | MIT |
| `rich` | https://github.com/Textualize/rich | 14.3.4 | `e5dcffe836b6ec8a` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | MIT |
| `ruff` | https://github.com/astral-sh/ruff | 0.16.0 | `e5dcffe836b6ec8a` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | MIT |
| `shellingham` | https://github.com/sarugaku/shellingham | 1.5.4 | `d8d62d58d661d5dd` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | ISC |
| `sortedcontainers` | https://github.com/grantjenks/python-sortedcontainers | 2.4.0 | `6a666d685ab3d80b` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | Apache-2.0 |
| `tree-sitter` | https://github.com/tree-sitter/py-tree-sitter | 0.26.0 | `e5dcffe836b6ec8a` | clean-2026-08-14 | in-binary | onboardux | 2026-10-30 | MIT |
| `tree-sitter-language-pack` | https://github.com/xberg-io/tree-sitter-language-pack | 1.14.3 | `e5dcffe836b6ec8a` | clean-2026-08-14 | in-binary | onboardux | 2026-10-30 | MIT |
| `typer` | https://github.com/fastapi/typer | 0.27.0 | `e5dcffe836b6ec8a` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | MIT |
| `typing-extensions` | https://github.com/python/typing_extensions | 4.16.0 | `606b04e71db9ca7a` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | PSF-2.0 |
| `types-pyyaml` | https://github.com/python/typeshed | 6.0.12.20260724 | `2af71558e438db0b` | clean-2026-08-07 | dev-only | onboardux | 2026-10-30 | Apache-2.0 |
| `typing-inspection` | https://github.com/pydantic/typing-inspection | 0.4.2 | `e5dcffe836b6ec8a` | clean-2026-08-07 | in-binary | onboardux | 2026-10-30 | MIT |

## Subprocess-invoked tools (Build 1, S1.3)

`universal-ctags` is the degrade ladder's second rung (`01` F9.2, `03` §2). It is
**GPL-2.0-or-later**, which the in-binary allowlist forbids and the `subprocess`
mode permits — so it carries a row here *and* an entry in `subprocess-deps.toml`,
naming the one site that invokes it. Without both, `licence_gate.py` treats it as
`in-binary` and rejects it, which is the fail-closed behaviour the policy wants.

**It is not a Python distribution and does not appear in `uv.lock`**, so its row
records what a reviewer needs rather than what the gate resolves: it is invoked,
never linked, never redistributed, and a machine without it degrades to the
`regex` rung rather than failing. That is why the version column reads *any* —
we depend on its output format, not on a pin, and `run_tool` tolerates a tool
that will not start.

| Component | Repository | Version | Licence hash | Security status | Usage mode | Owner | Re-verify by | Licence |
|---|---|---|---|---|---|---|---|---|
| `universal-ctags` | https://github.com/universal-ctags/ctags | any (not pinned; not a Python distribution) | `n/a -- not resolved by uv` | clean-2026-08-14 | subprocess | onboardux | 2026-10-30 | GPL-2.0-or-later |

## Toolchain that is never distributed

The development container and the CI runner provide `git`, `uv`, the `sqlite3`
command-line shell and the PostgreSQL client (`psql`). **None of them is a
Python distribution, none appears in `uv.lock`, and none is linked into the
wheel or the single-file binary** — they are the tools that build and validate
the artifact, in the same class as the compiler. They therefore carry no row
above, and `licence_gate.py` does not see them.

Recorded here rather than omitted, because "why is `sqlite3` not in the table"
is a reasonable review question and the answer should not have to be inferred:
the SQLite shell is public domain and the PostgreSQL client is under the
permissive PostgreSQL Licence, so neither would fail the policy even if it were
linked — which it is not.

## Notes on two copyleft dev dependencies

`hypothesis` and `pathspec` are **MPL-2.0**, which is file-level copyleft and is
therefore *not* on the in-binary allowlist. Both are recorded `dev-only`: they
are test and type-checking dependencies and are never linked into the wheel or
the single-file binary. Promoting either to a runtime dependency would flip them
to `in-binary` and the gate would reject the change — which is the intended
behaviour, not an obstacle to work around.
