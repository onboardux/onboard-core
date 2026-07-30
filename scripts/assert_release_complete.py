"""`release`: refuse to proceed unless the release is complete.

NFR N14 and PRD F15.6: **a release missing an SBOM, provenance or a signature is
not a release.** Asserting it here means the failure arrives before anything is
published rather than after, which is the difference between a delayed release
and a withdrawn one.

Also enforces `BINARY_MAX_MB`, read from the constants module rather than
written here.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from adopt_const import BINARY_MAX_MB

BYTES_PER_MB: Final[int] = 1024 * 1024

#: Artefacts that are themselves evidence, not things needing evidence.
EVIDENCE_SUFFIXES: Final[frozenset[str]] = frozenset({".sig", ".pem", ".intoto.jsonl"})

SBOM_NAME: Final[str] = "sbom.cdx.json"


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _is_payload(path: Path) -> bool:
    return path.is_file() and path.suffix not in EVIDENCE_SUFFIXES and path.name != SBOM_NAME


def check(directory: Path, *, require_provenance: bool = True) -> Report:
    report = Report()

    sbom = directory / SBOM_NAME
    if not sbom.exists():
        report.violations.append(f"no {SBOM_NAME}: a release without an SBOM is not a release.")
    else:
        try:
            parsed = json.loads(sbom.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.violations.append(f"{SBOM_NAME} is not valid JSON: {exc}")
        else:
            components = parsed.get("components", [])
            if not components:
                report.violations.append(f"{SBOM_NAME} lists no components.")
            else:
                report.notes.append(f"SBOM lists {len(components)} components.")

    payloads = sorted(p for p in directory.iterdir() if _is_payload(p))
    if not payloads:
        report.violations.append(f"no release artefacts found in {directory}.")

    for artefact in payloads:
        size_mb = artefact.stat().st_size / BYTES_PER_MB
        if size_mb > BINARY_MAX_MB:
            report.violations.append(
                f"{artefact.name} is {size_mb:.1f} MB, over the {BINARY_MAX_MB} MB ceiling."
            )
        if not (directory / f"{artefact.name}.sig").exists():
            report.violations.append(
                f"{artefact.name} has no cosign signature. An unsigned artefact cannot be "
                "verified by anyone who did not build it."
            )

    if require_provenance and not list(directory.glob("*.intoto.jsonl")):
        report.violations.append(
            "no SLSA provenance attestation found. Provenance is what ties a published "
            "artefact back to the commit and the workflow that produced it."
        )

    report.notes.append(f"checked {len(payloads)} artefact(s) in {directory}.")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument(
        "--allow-missing-provenance",
        action="store_true",
        help="for local dry runs, where no attestation service is reachable",
    )
    args = parser.parse_args(argv)

    if not args.dir.exists():
        print(f"VIOLATION: {args.dir} does not exist.")
        return 1

    report = check(args.dir, require_provenance=not args.allow_missing_provenance)
    for note in report.notes:
        print(f"note: {note}")
    for violation in report.violations:
        print(f"VIOLATION: {violation}")

    if report.ok:
        print("release completeness: OK")
        return 0
    print(f"release completeness: {len(report.violations)} violation(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
