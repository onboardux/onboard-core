"""`first-party-deps`: every `adopt_*` a distribution imports, it also declares.

**Two of the fifteen distributions tagged at `v0.3.0` import a first-party module
they never declared, and the release shipped that way.** `adopt-store` imports
`adopt_identity` in `facades/identity.py`; `adopt-cli` imports `adopt_model` in
`commands/init.py` and `commands/boundary.py`. `pip install adopt-store` alone
raises `ModuleNotFoundError` on its first facade import. See `BACKLOG.md` B-06.

**Nothing could have caught them, and that is the interesting part.** Two
independent maskings, both the shape of CR-53 and CR-54 -- *correct in the tree
every test runs against, broken from an installed subset*:

* Every test runs under `uv sync --all-packages`, which installs all fifteen
  distributions whatever any one of them declares. No test in this repository
  can observe a missing declaration, because the module is always importable.
* `packaged-artifact` -- the one gate whose subject is an installed artefact
  rather than the source tree -- installs `adopt-cli`, which declares
  `adopt-identity` itself. So `adopt_identity` arrives transitively and
  `adopt-store`'s gap is invisible even there. `adopt-model` reaches `adopt-cli`
  through `adopt-store` and masks the second gap the same way.

`adopt-plane` was the first consumer to install a *subset*, and it found both
immediately. That is the gap this gate closes: the workspace is not a consumer,
and until something judges each manifest on its own, the next instance is found
by someone outside the repository after a release.

**The module-to-distribution map is discovered, never listed.** It is built by
walking each package's `src/` for its top-level module directories, so a new
distribution or a renamed module cannot arrive unregistered. CR-50 and CR-67 are
both records of a hand-maintained list in exactly this position going stale, and
CR-64 is a record of one going stale in the ticket that raised it.

**A floor, because the failure mode of this gate is silence.** A discovery bug
that finds no packages, or finds packages but no imports, would report zero
violations and exit zero -- indistinguishable from a clean tree. CR-67 records
that exact outcome (`0/0 covered (100%)`, exit zero) in `escape_coverage.py`
after its subject became a package. So the run refuses to pass unless it found
at least `MIN_DISTRIBUTIONS` manifests and `MIN_EDGES` first-party import edges.
"""

import argparse
import ast
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: Below either of these the run is not evidence. See the module docstring: a
#: gate whose broken state is indistinguishable from its passing state is not a
#: gate. These are floors on *discovery*, not targets -- raising them to today's
#: measurement would turn a floor into a ratchet, which `03` §2.3 forbids for
#: exactly the reason it forbids coverage targets.
MIN_DISTRIBUTIONS: Final[int] = 10
MIN_EDGES: Final[int] = 10

#: A first-party module is one this workspace publishes. The prefix is how a
#: candidate is *recognised*; membership is decided by the discovered map, so an
#: `adopt_`-prefixed module that no distribution ships is reported rather than
#: silently ignored -- it is either a typo or a dependency on something that is
#: not packaged, and both are defects.
FIRST_PARTY_PREFIX: Final[str] = "adopt_"


@dataclass(frozen=True, slots=True)
class Distribution:
    name: str
    directory: Path
    modules: frozenset[str]
    declared: frozenset[str]


@dataclass
class Report:
    distributions: int = 0
    edges: int = 0
    violations: list[str] = field(default_factory=list)


def _requirement_name(requirement: str) -> str:
    """`adopt-store>=0.3.0` and `adopt-store[extra]` are both `adopt-store`."""
    for index, character in enumerate(requirement):
        if character in "<>=!~[; ":
            return requirement[:index].strip()
    return requirement.strip()


def discover(packages_dir: Path) -> list[Distribution]:
    """Every distribution, with the modules it ships and the names it declares."""
    found: list[Distribution] = []
    for manifest in sorted(packages_dir.glob("*/pyproject.toml")):
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        project = data["project"]
        src = manifest.parent / "src"
        modules = (
            frozenset(
                child.name
                for child in src.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            )
            if src.is_dir()
            else frozenset()
        )
        declared = frozenset(
            _requirement_name(requirement) for requirement in project.get("dependencies", [])
        )
        found.append(
            Distribution(
                name=str(project["name"]),
                directory=manifest.parent,
                modules=modules,
                declared=declared,
            )
        )
    return found


def _imported_modules(source: Path) -> set[str]:
    """Top-level `adopt_*` modules imported by one file.

    Parsed rather than matched by regular expression, so `import adopt_store` in
    a docstring or a comment is not a dependency and a multi-line
    `from x import (\n    y,\n)` is not missed. A file that does not parse is
    reported by the caller rather than skipped.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        # `level` > 0 is a relative import: `from .records import X` is inside
        # this distribution and can never be a cross-package edge.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return {module for module in modules if module.startswith(FIRST_PARTY_PREFIX)}


def _display(path: Path, packages_dir: Path) -> str:
    """A path a reader can act on, whichever tree was judged.

    `--packages-dir` may name a tree outside this checkout -- a worktree at a tag
    is how the gate was proven against the release that shipped the defect -- so
    this cannot anchor on `REPO_ROOT`. It tries the repository first, then the
    judged tree, and falls back to the absolute path rather than raising.
    """
    for anchor in (REPO_ROOT, packages_dir.parent):
        try:
            return str(path.relative_to(anchor))
        except ValueError:
            continue
    return str(path)


def check(packages_dir: Path) -> Report:
    report = Report()
    distributions = discover(packages_dir)
    report.distributions = len(distributions)

    owner: dict[str, str] = {}
    for distribution in distributions:
        for module in distribution.modules:
            owner[module] = distribution.name

    for distribution in distributions:
        for source in sorted((distribution.directory / "src").rglob("*.py")):
            try:
                imported = _imported_modules(source)
            except SyntaxError as exc:
                report.violations.append(f"{_display(source, packages_dir)} does not parse: {exc}")
                continue
            for module in sorted(imported):
                if module in distribution.modules:
                    continue
                report.edges += 1
                required = owner.get(module)
                where = _display(source, packages_dir)
                if required is None:
                    report.violations.append(
                        f"{distribution.name}: {where} imports `{module}`, which no "
                        "distribution in this workspace ships"
                    )
                elif required not in distribution.declared:
                    report.violations.append(
                        f"{distribution.name}: {where} imports `{module}` but the manifest "
                        f"does not declare `{required}` -- `pip install {distribution.name}` "
                        "alone raises ModuleNotFoundError"
                    )
    return report


def _judge(report: Report) -> int:
    print(f"first-party-deps: {report.distributions} distributions, {report.edges} import edges")

    if report.distributions < MIN_DISTRIBUTIONS or report.edges < MIN_EDGES:
        print(
            f"VIOLATION: discovery found {report.distributions} distributions and "
            f"{report.edges} edges, below the floor of {MIN_DISTRIBUTIONS} and {MIN_EDGES}. "
            "A run that measures nothing reports no violations and exits zero, which is "
            "indistinguishable from a clean tree -- so it fails instead."
        )
        return 1

    if report.violations:
        for violation in report.violations:
            print(f"VIOLATION: {violation}")
        print(f"first-party-deps: {len(report.violations)} undeclared first-party dependency(ies)")
        return 1

    print("first-party-deps: OK -- every first-party import is declared by the manifest")
    return 0


def _self_test(packages_dir: Path) -> int:
    """Plant a real violation, require the gate to name it, restore byte-exactly.

    **The planted edge is in a submodule, not at a package root.** CR-67 records
    a self-test that passed against a gate which had gone blind, because it
    planted its subject where the broken discovery still looked. Discovery here
    walks `src/**/*.py`, so the plant is placed one level down to exercise it.
    """
    print("self-test: a clean tree first")
    if _judge(check(packages_dir)) != 0:
        print("SELF-TEST FAILED: the tree is not clean, so a planted violation proves nothing.")
        return 1

    victims = sorted(packages_dir.glob("*/src/*/"))
    victim_dir = next(
        (directory for directory in victims if (directory / "facades").is_dir()),
        victims[0] if victims else None,
    )
    if victim_dir is None:
        print("SELF-TEST FAILED: found no package source directory to plant in.")
        return 1
    submodule = victim_dir / "facades" if (victim_dir / "facades").is_dir() else victim_dir
    planted = submodule / "_planted_first_party_deps.py"

    # `adopt_policy` is a real distribution, and no package that has a `facades/`
    # directory declares it -- so this is a genuine undeclared edge rather than a
    # module that happens not to exist.
    planted.write_text(
        '"""Planted by first_party_deps.py --self-test. Removed before it returns."""\n\n'
        "import adopt_policy  # noqa: F401\n",
        encoding="utf-8",
    )
    try:
        print("\nself-test: with an undeclared `adopt_policy` import planted in a submodule")
        report = check(packages_dir)
    finally:
        planted.unlink(missing_ok=True)

    if planted.exists():
        print(f"SELF-TEST FAILED: {planted} survived the self-test.")
        return 1

    named = [violation for violation in report.violations if "adopt_policy" in violation]
    if not named:
        print("SELF-TEST FAILED: the gate passed, or failed without naming the planted import.")
        for violation in report.violations:
            print(f"  saw: {violation}")
        return 1

    print(f"  OK -- rejected, naming the import ({len(named)} finding(s))")
    print(f"  OK -- {named[0]}")
    print("  OK -- the planted file is removed")

    # The floor is the other half, and it has its own failure mode: it must
    # reject a run that discovered nothing, or the gate can go blind exactly as
    # `escape_coverage.py` did.
    if _judge(Report(distributions=0, edges=0)) == 0:
        print("SELF-TEST FAILED: a run that discovered nothing was accepted.")
        return 1
    print("  OK -- a run that discovers nothing is rejected rather than reported clean")

    print("\nself-test OK: an undeclared first-party dependency cannot reach a release")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Judge every manifest.")
    parser.add_argument("--self-test", action="store_true", help="Prove the gate still fails.")
    parser.add_argument("--packages-dir", type=Path, default=REPO_ROOT / "packages")
    args = parser.parse_args(argv)

    if not args.packages_dir.is_dir():
        print(f"VIOLATION: {args.packages_dir} is not a directory.")
        return 1
    if args.self_test:
        return _self_test(args.packages_dir)
    if args.check:
        return _judge(check(args.packages_dir))
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
