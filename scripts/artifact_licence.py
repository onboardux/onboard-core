"""`artifact-licence`: the licence travels inside every artefact we publish.

**Fifteen Apache-2.0 distributions were one dispatch away from PyPI carrying no
licence at all.** Every `dist-info` held exactly `METADATA`, `WHEEL`,
`entry_points.txt` and `RECORD`; `METADATA` declared no `License`, no
`License-Expression` and no classifier; neither the wheel nor the source
distribution contained the Apache-2.0 text or the `NOTICE`. Fifteen PyPI pages
would have read *License: UNKNOWN*.

The irony is the point. `licence_gate.py` holds 32 **third-party** distributions
to seven audited fields each, and nothing looked at our own. Apache-2.0 §4(a)
requires a copy of the License to accompany a redistribution and §4(d) requires
the NOTICE; the repository satisfied both at its root and neither in anything it
shipped. **The gap was never the policy -- it was the subject**, which is the
same sentence `packaged-artifact` was created to answer (CR-53) and the reason
this is a second gate rather than a line in that one.

**Byte-identity is the load-bearing assertion here, not the presence check.**
`LICENSE` and `NOTICE` have exactly one authored copy, at the repository root,
and reach an artefact through each manifest's `sdist.force-include` -- `uv build`
builds the wheel *from the sdist*, so placing them there puts them in both. That
design has no committed duplicate to drift, and comparing bytes is what keeps it
that way: the day someone "fixes" a build by committing a stale copy into a
package directory, this gate says so instead of shipping two licences.

`--self-test` strips the licence configuration from one manifest, rebuilds, and
requires the gate to fail **naming that distribution**. A gate nobody has seen
fail is a gate nobody should trust.
"""

import argparse
import email
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from scripts.release_context import CANONICAL_DISTRIBUTIONS

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: The one authored copy of each. Everything else is compared against these.
LICENCE_FILES: Final[tuple[str, ...]] = ("LICENSE", "NOTICE")

#: `03` §7.3 permits no other expression for a first-party artefact: `adopt-core`
#: is the Apache-2.0 half of the boundary, and a wheel that says otherwise is a
#: licence change nobody reviewed.
EXPECTED_LICENCE_EXPRESSION: Final[str] = "Apache-2.0"


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _authored() -> dict[str, bytes]:
    """The repository root's copies -- the only ones a human edits."""
    authored: dict[str, bytes] = {}
    for name in LICENCE_FILES:
        path = REPO_ROOT / name
        if not path.is_file():
            raise ValueError(f"the repository root has no {name}; there is nothing to ship")
        authored[name] = path.read_bytes()
    return authored


def _check_wheel(wheel: Path, authored: dict[str, bytes], report: Report) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        dist_info = {name.split("/", 1)[0] for name in names if ".dist-info/" in name}
        if len(dist_info) != 1:
            report.violations.append(f"{wheel.name}: expected exactly one .dist-info directory")
            return
        root = dist_info.pop()

        try:
            raw = archive.read(f"{root}/METADATA").decode("utf-8")
        except KeyError:
            report.violations.append(f"{wheel.name}: no METADATA")
            return
        metadata = email.message_from_string(raw)

        expression = metadata.get("License-Expression")
        if expression != EXPECTED_LICENCE_EXPRESSION:
            report.violations.append(
                f"{wheel.name}: License-Expression is {expression!r}, expected "
                f"{EXPECTED_LICENCE_EXPRESSION!r}. A published wheel with no licence "
                f"expression reads as 'License: UNKNOWN' on PyPI."
            )

        declared = set(metadata.get_all("License-File") or ())
        missing_declaration = set(LICENCE_FILES) - declared
        if missing_declaration:
            report.violations.append(
                f"{wheel.name}: METADATA declares no License-File for "
                + ", ".join(sorted(missing_declaration))
            )

        for name in LICENCE_FILES:
            member = f"{root}/licenses/{name}"
            if member not in names:
                report.violations.append(
                    f"{wheel.name}: does not carry {name}. Apache-2.0 requires it to "
                    f"accompany the redistribution, not merely be referenced by it."
                )
                continue
            shipped = archive.read(member)
            if shipped != authored[name]:
                report.violations.append(
                    f"{wheel.name}: its {name} differs from the repository root's copy. "
                    f"There is meant to be exactly one authored copy; two have drifted."
                )


def _check_sdist(sdist: Path, authored: dict[str, bytes], report: Report) -> None:
    with tarfile.open(sdist) as archive:
        members = {member.name: member for member in archive.getmembers()}
        roots = {name.split("/", 1)[0] for name in members}
        if len(roots) != 1:
            report.violations.append(f"{sdist.name}: expected exactly one top-level directory")
            return
        root = roots.pop()

        for name in LICENCE_FILES:
            member = members.get(f"{root}/{name}")
            if member is None:
                report.violations.append(f"{sdist.name}: does not carry {name} at its root.")
                continue
            extracted = archive.extractfile(member)
            if extracted is None or extracted.read() != authored[name]:
                report.violations.append(
                    f"{sdist.name}: its {name} differs from the repository root's copy."
                )


def check(directory: Path) -> Report:
    """Judge every built distribution in `directory`."""
    report = Report()
    authored = _authored()

    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    expected = len(CANONICAL_DISTRIBUTIONS)
    if len(wheels) != expected or len(sdists) != expected:
        report.violations.append(
            f"expected {expected} wheels and {expected} source distributions in "
            f"{directory}; found {len(wheels)} and {len(sdists)}."
        )

    for wheel in wheels:
        _check_wheel(wheel, authored, report)
    for sdist in sdists:
        _check_sdist(sdist, authored, report)

    report.notes.append(
        f"checked {len(wheels)} wheel(s) and {len(sdists)} source distribution(s) "
        f"against the repository root's {' and '.join(LICENCE_FILES)}."
    )
    return report


def _build(into: Path) -> None:
    subprocess.run(
        ["uv", "build", "--all-packages", "--out-dir", str(into)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )


def _run_check(directory: Path | None) -> int:
    with tempfile.TemporaryDirectory() as scratch:
        if directory is None:
            directory = Path(scratch) / "dist"
            print("building the fifteen distributions ...")
            _build(directory)
        report = check(directory)

    for note in report.notes:
        print(f"note: {note}")
    for violation in report.violations:
        print(f"VIOLATION: {violation}")
    if report.ok:
        print("artifact-licence: OK -- every artefact carries the licence it is published under.")
        return 0
    print(f"artifact-licence: {len(report.violations)} violation(s)")
    return 1


def _self_test() -> int:
    """Strip one manifest's licence configuration and require a named failure."""
    victim = REPO_ROOT / "packages" / "adopt-const" / "pyproject.toml"
    original = victim.read_text(encoding="utf-8")
    planted = original
    for line in (
        'license = "Apache-2.0"\n',
        'license-files = ["LICENSE", "NOTICE"]\n',
        '"../../LICENSE" = "LICENSE"\n',
        '"../../NOTICE" = "NOTICE"\n',
    ):
        planted = planted.replace(line, "")
    if planted == original:
        print("VIOLATION: nothing was planted -- the manifest is not shaped as expected.")
        return 1

    print("planting: removing adopt-const's licence configuration")
    try:
        victim.write_text(planted, encoding="utf-8")
        with tempfile.TemporaryDirectory() as scratch:
            built = Path(scratch) / "dist"
            _build(built)
            report = check(built)
    finally:
        victim.write_text(original, encoding="utf-8")

    # The tree must come back byte-exact, or the gate has become the defect it
    # was written to catch. Reported rather than asserted: `plant_violation.py`
    # sets the precedent that a planting tool proves its own revert.
    if victim.read_text(encoding="utf-8") != original:
        print(f"VIOLATION: {victim} was not restored byte-exactly after planting.")
        return 1

    named = [v for v in report.violations if "adopt_const" in v]
    if not named:
        print("VIOLATION: the gate passed, or failed without naming adopt-const.")
        for violation in report.violations:
            print(f"  saw: {violation}")
        return 1

    print(f"  OK -- the gate fails and names the distribution ({len(named)} finding(s))")
    print(f"  OK -- {named[0]}")
    print("  OK -- the manifest is restored byte-exactly")
    print("self-test OK: a distribution published without its licence is caught before upload")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Build and judge the artefacts.")
    parser.add_argument("--dir", type=Path, help="Judge distributions already built here.")
    parser.add_argument("--self-test", action="store_true", help="Prove the gate still fails.")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.check or args.dir is not None:
        if shutil.which("uv") is None and args.dir is None:
            print("VIOLATION: uv is not on PATH and no --dir was given.")
            return 1
        return _run_check(args.dir)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
