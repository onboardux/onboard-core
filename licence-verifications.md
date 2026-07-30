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

A dependency with **no row** is treated as `in-binary` — the strictest rule.
Failing closed is the point: an undeclared dependency must never be the lenient
case.

## Denied by name

| Denied component | Reason |
|---|---|
| CodeQL CLI engine | Its terms forbid analysis of non-open-source code, which is this product's entire purpose. No usage mode makes it compliant. |
| Semgrep maintained / Pro rule sets | Internal-use only and non-redistributable. Semgrep CE itself remains available as a declared subprocess; it is the maintained rules that cannot ship. |

## Verification records

**Security status** is `pending-audit` for every row below: no vulnerability
audit has been run against this dependency set yet. That is recorded honestly
rather than asserted clear. `licence_gate.py --check --strict-verify` — the
release gate at S9 — **rejects `pending-audit`**, so this set must be audited
before `0.3.0` ships.

| Dependency | Repository | Version | Licence hash | Security status | Usage mode | Owner | Re-verification date | Licence |
|---|---|---|---|---|---|---|---|---|
| `annotated-doc` | https://github.com/fastapi/annotated-doc | 0.0.5 | `e5dcffe836b6ec8a` | pending-audit | in-binary | eng-lead | 2026-10-30 | MIT |
| `click` | https://github.com/pallets/click | 8.4.2 | `118dbcd2d5c9f9b2` | pending-audit | in-binary | eng-lead | 2026-10-30 | BSD-3-Clause |
| `colorama` | https://github.com/tartley/colorama | 0.4.6 | `684e9824f05014cf` | pending-audit | in-binary | eng-lead | 2026-10-30 | BSD-3-Clause |
| `grimp` | https://github.com/seddonym/grimp | 3.15 | `684e9824f05014cf` | pending-audit | dev-only | eng-lead | 2026-10-30 | BSD-3-Clause |
| `hypothesis` | https://github.com/HypothesisWorks/hypothesis | 6.164.0 | `09962c1dc23fac80` | pending-audit | dev-only | eng-lead | 2026-10-30 | MPL-2.0 |
| `import-linter` | https://github.com/seddonym/import-linter | 2.13 | `684e9824f05014cf` | pending-audit | dev-only | eng-lead | 2026-10-30 | BSD-3-Clause |
| `iniconfig` | https://github.com/pytest-dev/iniconfig | 2.3.0 | `e5dcffe836b6ec8a` | pending-audit | dev-only | eng-lead | 2026-10-30 | MIT |
| `librt` | https://github.com/mypyc/librt | 0.13.0 | `e5dcffe836b6ec8a` | pending-audit | dev-only | eng-lead | 2026-10-30 | MIT |
| `markdown-it-py` | https://github.com/executablebooks/markdown-it-py | 4.2.0 | `e5dcffe836b6ec8a` | pending-audit | in-binary | eng-lead | 2026-10-30 | MIT |
| `mdurl` | https://github.com/executablebooks/mdurl | 0.1.2 | `e5dcffe836b6ec8a` | pending-audit | in-binary | eng-lead | 2026-10-30 | MIT |
| `mypy` | https://github.com/python/mypy | 1.20.2 | `e5dcffe836b6ec8a` | pending-audit | dev-only | eng-lead | 2026-10-30 | MIT |
| `mypy-extensions` | https://github.com/python/mypy_extensions | 1.1.0 | `e5dcffe836b6ec8a` | pending-audit | dev-only | eng-lead | 2026-10-30 | MIT |
| `packaging` | https://github.com/pypa/packaging | 26.2 | `f83c2e92648be1cb` | pending-audit | dev-only | eng-lead | 2026-10-30 | Apache-2.0 OR BSD-2-Clause |
| `pathspec` | https://github.com/cpburnz/python-pathspec | 1.1.1 | `09962c1dc23fac80` | pending-audit | dev-only | eng-lead | 2026-10-30 | MPL-2.0 |
| `pluggy` | https://github.com/pytest-dev/pluggy | 1.6.0 | `e5dcffe836b6ec8a` | pending-audit | dev-only | eng-lead | 2026-10-30 | MIT |
| `py-cpuinfo` | https://github.com/workhorsy/py-cpuinfo | 9.0.0 | `e5dcffe836b6ec8a` | pending-audit | dev-only | eng-lead | 2026-10-30 | MIT |
| `pygments` | https://github.com/pygments/pygments | 2.20.0 | `248dd895a2f89e28` | pending-audit | in-binary | eng-lead | 2026-10-30 | BSD-2-Clause |
| `pytest` | https://github.com/pytest-dev/pytest | 8.4.2 | `e5dcffe836b6ec8a` | pending-audit | dev-only | eng-lead | 2026-10-30 | MIT |
| `pytest-benchmark` | https://github.com/ionelmc/pytest-benchmark | 5.2.3 | `248dd895a2f89e28` | pending-audit | dev-only | eng-lead | 2026-10-30 | BSD-2-Clause |
| `rich` | https://github.com/Textualize/rich | 14.3.4 | `e5dcffe836b6ec8a` | pending-audit | in-binary | eng-lead | 2026-10-30 | MIT |
| `ruff` | https://github.com/astral-sh/ruff | 0.16.0 | `e5dcffe836b6ec8a` | pending-audit | dev-only | eng-lead | 2026-10-30 | MIT |
| `shellingham` | https://github.com/sarugaku/shellingham | 1.5.4 | `d8d62d58d661d5dd` | pending-audit | in-binary | eng-lead | 2026-10-30 | ISC |
| `sortedcontainers` | https://github.com/grantjenks/python-sortedcontainers | 2.4.0 | `6a666d685ab3d80b` | pending-audit | dev-only | eng-lead | 2026-10-30 | Apache-2.0 |
| `typer` | https://github.com/fastapi/typer | 0.27.0 | `e5dcffe836b6ec8a` | pending-audit | in-binary | eng-lead | 2026-10-30 | MIT |
| `typing-extensions` | https://github.com/python/typing_extensions | 4.16.0 | `606b04e71db9ca7a` | pending-audit | in-binary | eng-lead | 2026-10-30 | PSF-2.0 |

## Notes on two copyleft dev dependencies

`hypothesis` and `pathspec` are **MPL-2.0**, which is file-level copyleft and is
therefore *not* on the in-binary allowlist. Both are recorded `dev-only`: they
are test and type-checking dependencies and are never linked into the wheel or
the single-file binary. Promoting either to a runtime dependency would flip them
to `in-binary` and the gate would reject the change — which is the intended
behaviour, not an obstacle to work around.
