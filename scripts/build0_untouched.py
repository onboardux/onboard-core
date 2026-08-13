"""`build0-untouched`: Build 1 adds packages; it does not edit Build 0's.

Build 1's implementation spec §4 states the ownership rule: *"`adopt-core` is
Build 0's. Build 1 adds files and appends to the constants and error modules. It
never edits Build 0's store adapter, revision helpers, URI module, scope
resolver, archetype detector, agent seam or workflow facade."* Build 1's sprint
plan makes it a Final Output Validation line.

**Why this script exists rather than the `git diff --stat` the pack wrote.** The
pack's line names five paths under `packages/adopt-core/src/adopt/core/`, a
layout Build 0 never built -- it shipped fifteen packages with flat modules. As
written the command exits 128 with `fatal: ambiguous argument`, which is noisy
enough that the natural repair is to add `--`; after that it returns empty and
exits **0 forever, whatever anyone edits**. A gate whose broken state is
indistinguishable from its passing state is not a gate (Build 0 CR-67), so this
one refuses to run against a name that does not resolve.

Two rules, both enforced:

1. **Every protected package still exists.** A missing subject is a broken gate,
   never a pass. This is the check that would have caught the pack's own line.
2. **Every package that existed at the baseline is classified**, protected or
   editable, each with a stated reason. A package added after the baseline is
   Build 1's own and needs no row -- which is what lets `adopt-map` land without
   editing this file, while a *new Build 0 package* cannot appear unclassified.

The baseline is the merge base with `main`, not the `v0.3.0` tag: Build 0 stays
under maintenance after its release (a dependency declaration landed in
`adopt-store` after the tag), and this gate answers *"did **this branch** edit
Build 0's modules"*, which is what the sprint line means.
"""

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
PACKAGES: Final[Path] = REPO_ROOT / "packages"

DEFAULT_BASE_REF: Final[str] = "main"

#: Build 0 packages Build 1 must not touch, and why each one is load-bearing.
#: Keyed by distribution directory name under `packages/`.
PROTECTED: Final[dict[str, str]] = {
    "adopt-store": "the store adapter and the revision helpers -- append-only is enforced here",
    "adopt-identity": "`build_uri` is the only mint site; Build 1 calls it and never edits it",
    "adopt-scope": "the scope resolver; environment isolation is structural because of it",
    "adopt-agent": "the AgentRunner seam -- one door for every model call",
    "adopt-workflow": "the workflow facade",
    "adopt-detect": "archetype detection and tier negotiation",
    "adopt-schema": "the canonical manifest and its emitters; the schema is frozen additive-only",
    "adopt-coverage": "`recompute_coverage` is the coverage authority Build 1 reports from",
    "adopt-freshness": "`resolve_freshness`",
    "adopt-model": "generated from the manifest; regenerate, never hand-edit",
    "adopt-export": "the canonical interchange contract downstream reads",
    "adopt-policy": "probe capability manifests and outbound envelopes",
}

#: Build 0 packages Build 1 may append to, and the rule that permits it.
EDITABLE: Final[dict[str, str]] = {
    "adopt-const": "B1-CR-12: one constants home, and Build 1 appends its block to it",
    "adopt-obs": "Build 1 registers its error codes in the same registry",
    "adopt-cli": "Build 1 adds the `map`, `surface` and `extractors` commands",
}


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def baseline(base_ref: str) -> str:
    """The merge base with `base_ref`, or `base_ref` itself when unrelated."""
    try:
        return _git("merge-base", "HEAD", base_ref).strip()
    except RuntimeError:
        return base_ref


def packages_at(commit: str) -> set[str]:
    """Distribution directory names present under `packages/` at `commit`."""
    listing = _git("ls-tree", "--name-only", f"{commit}:packages")
    return {line.strip().rstrip("/") for line in listing.splitlines() if line.strip()}


def check_subjects_resolve() -> list[str]:
    """Rule 1: a protected package that does not exist is a broken gate."""
    return [
        f"{name} is protected but `packages/{name}` does not exist. "
        "A gate whose subject is missing passes vacuously -- fix the name or "
        "remove the row, but never leave it pointing at nothing."
        for name in sorted(PROTECTED)
        if not (PACKAGES / name).is_dir()
    ]


def check_classification(base: str) -> list[str]:
    """Rule 2: every package present at the baseline is classified."""
    try:
        existing = packages_at(base)
    except RuntimeError as exc:
        return [f"cannot list packages at {base}: {exc}"]
    classified = set(PROTECTED) | set(EDITABLE)
    return [
        f"packages/{name} existed at the baseline but is neither protected nor "
        "editable. Classify it with a reason: an unclassified Build 0 package is "
        "one this gate silently ignores."
        for name in sorted(existing - classified)
    ]


def changed_paths(base: str) -> list[str]:
    """Protected paths this branch modified, including untracked additions."""
    targets = [f"packages/{name}" for name in sorted(PROTECTED) if (PACKAGES / name).is_dir()]
    if not targets:
        return []
    modified = _git("diff", "--name-only", base, "--", *targets).splitlines()
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", *targets).splitlines()
    return sorted({line.strip() for line in [*modified, *untracked] if line.strip()})


def run(base_ref: str = DEFAULT_BASE_REF) -> Report:
    report = Report()

    report.violations += check_subjects_resolve()
    if report.violations:
        return report

    base = baseline(base_ref)
    report.notes.append(
        f"baseline {base[:12]} ({base_ref}); {len(PROTECTED)} protected, "
        f"{len(EDITABLE)} editable package(s)."
    )

    report.violations += check_classification(base)

    for path in changed_paths(base):
        package = Path(path).parts[1] if len(Path(path).parts) > 1 else path
        report.violations.append(
            f"{path} changed, and packages/{package} is Build 0's: "
            f"{PROTECTED.get(package, 'protected')}. A change needed there is a "
            "Build 0 amendment with its own review, not a Build 1 edit."
        )

    return report


def self_test() -> int:
    """Plant an edit in every protected package and require a red run for each.

    Planting into *one* package would leave eleven subjects unproven, and the
    defect this gate exists for -- a subject that silently stops being watched --
    is per-package. Each file is restored byte-exactly.
    """
    failures: list[str] = []

    clean = run()
    if not clean.ok:
        print("self-test: the tree is already dirty against the baseline:")
        for violation in clean.violations:
            print(f"  {violation}")
        return 1

    for name in sorted(PROTECTED):
        target = next((PACKAGES / name / "src").rglob("*.py"), None)
        if target is None:
            failures.append(f"{name}: no source file to plant into")
            continue

        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# planted by build0_untouched --self-test\n")
            planted = run()
            if planted.ok:
                failures.append(f"{name}: a planted edit did not fail the gate")
            elif not any(name in v for v in planted.violations):
                failures.append(f"{name}: the gate failed but did not name the package")
        finally:
            target.write_bytes(original)

    restored = run()
    if not restored.ok:
        failures.append("the tree was not restored byte-exactly after planting")

    for failure in failures:
        print(f"SELF-TEST FAILURE: {failure}")
    if failures:
        print(f"build0-untouched --self-test: {len(failures)} failure(s)")
        return 1
    print(f"build0-untouched --self-test: OK ({len(PROTECTED)} packages each proven to fail)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit non-zero on any violation")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="plant an edit in every protected package and require the gate to fail",
    )
    parser.add_argument("--base", default=DEFAULT_BASE_REF, help="baseline ref (default: main)")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    report = run(args.base)
    for note in report.notes:
        print(f"note: {note}")
    for violation in report.violations:
        print(f"VIOLATION: {violation}")

    if report.ok:
        print("build0-untouched: OK")
        return 0
    print(f"build0-untouched: {len(report.violations)} violation(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
