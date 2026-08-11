"""`release`: refuse to proceed unless the release is complete.

NFR N14 and PRD F15.6: **a release missing an SBOM, provenance or a signature is
not a release.** Asserting it here means the failure arrives before anything is
published rather than after, which is the difference between a delayed release
and a withdrawn one.

`--allow-missing-provenance` exists solely for a private, non-publishing
diagnostic where GitHub's attestation service is unavailable. It never certifies
a release; every public dry run, tag and publication remains provenance-strict.

Also enforces `BINARY_MAX_MB`, read from the constants module rather than
written here.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from adopt_const import BINARY_MAX_MB
from scripts.emit_sbom import CYCLONEDX_FORMAT, CYCLONEDX_SPEC_VERSION, SBOM_ROOT_NAME
from scripts.release_context import CANONICAL_DISTRIBUTIONS

# const-sync: ok -- 1024 here is the binary prefix, not URI_MAX_BYTES.
BYTES_PER_MB: Final[int] = 1024 * 1024

#: Artefacts that are themselves evidence, not things needing evidence.
EVIDENCE_SUFFIXES: Final[frozenset[str]] = frozenset({".sig", ".pem", ".intoto.jsonl"})

SBOM_NAME: Final[str] = "sbom.cdx.json"
EXPECTED_BINARIES: Final[frozenset[str]] = frozenset(
    {"adopt-linux-x86_64", "adopt-macos-arm64", "adopt-windows-x86_64.exe"}
)
EXPECTED_DISTRIBUTION_COUNT: Final[int] = len(CANONICAL_DISTRIBUTIONS)

_WHEEL_BUILD_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9][0-9A-Za-z_.]*$")
_WHEEL_TAG_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9A-Za-z_.]+$")
_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9A-Za-z_.+!]+$")


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _is_payload(path: Path) -> bool:
    return path.is_file() and not any(path.name.endswith(suffix) for suffix in EVIDENCE_SUFFIXES)


def _is_nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _normalise_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalise_archive_version(version: str) -> str:
    # Wheel/sdist builders escape hyphens in a version component. PEP 440's
    # normal forms used by this workspace otherwise survive unchanged.
    return version.replace("-", "_")


def _parse_wheel_filename(filename: str) -> tuple[str, str]:
    """Return canonical distribution name and version from a PEP 427 filename."""
    if not filename.endswith(".whl"):
        raise ValueError("not a wheel")
    parts = filename.removesuffix(".whl").split("-")
    # const-sync: ok -- PEP 427 field counts, not product tunables.
    if len(parts) not in {5, 6}:
        raise ValueError("expected five wheel fields plus an optional build tag")
    distribution, version = parts[0], parts[1]
    if not distribution or _VERSION_RE.fullmatch(version) is None:
        raise ValueError("invalid distribution or version field")
    tag_offset = 2
    if len(parts) == 6:
        if _WHEEL_BUILD_RE.fullmatch(parts[2]) is None:
            raise ValueError("invalid wheel build tag")
        # const-sync: ok -- PEP 427's tag offset, not a product tunable.
        tag_offset = 3
    if any(_WHEEL_TAG_RE.fullmatch(tag) is None for tag in parts[tag_offset:]):
        raise ValueError("invalid Python, ABI or platform tag")
    return _normalise_distribution_name(distribution), version


def _parse_sdist_filename(filename: str) -> tuple[str, str]:
    """Return canonical distribution name and version from a PEP 625 filename."""
    if not filename.endswith(".tar.gz"):
        raise ValueError("not a source distribution")
    stem = filename.removesuffix(".tar.gz")
    if "-" not in stem:
        raise ValueError("source distribution has no name/version separator")
    distribution, version = stem.rsplit("-", maxsplit=1)
    if not distribution or _VERSION_RE.fullmatch(version) is None:
        raise ValueError("invalid distribution or version field")
    return _normalise_distribution_name(distribution), version


def _validate_sbom(sbom: Path, expected_version: str | None, report: Report) -> None:
    if not sbom.is_file():
        report.violations.append(f"no {SBOM_NAME}: a release without an SBOM is not a release.")
        return
    try:
        parsed: Any = json.loads(sbom.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        report.violations.append(f"{SBOM_NAME} is not valid UTF-8 JSON: {exc}")
        return
    if not isinstance(parsed, dict):
        report.violations.append(f"{SBOM_NAME} root must be a JSON object.")
        return
    if parsed.get("bomFormat") != CYCLONEDX_FORMAT:
        report.violations.append(f"{SBOM_NAME} is not a {CYCLONEDX_FORMAT} document.")
    if parsed.get("specVersion") != CYCLONEDX_SPEC_VERSION:
        report.violations.append(f"{SBOM_NAME} must use CycloneDX {CYCLONEDX_SPEC_VERSION}.")
    if parsed.get("version") != 1:
        report.violations.append(f"{SBOM_NAME} document version must be 1.")

    metadata = parsed.get("metadata")
    root = metadata.get("component") if isinstance(metadata, dict) else None
    if not isinstance(root, dict):
        report.violations.append(f"{SBOM_NAME} has no metadata.component object.")
    else:
        if root.get("name") != SBOM_ROOT_NAME:
            report.violations.append(f"{SBOM_NAME} root component must be {SBOM_ROOT_NAME!r}.")
        if expected_version is not None and root.get("version") != expected_version:
            report.violations.append(
                f"{SBOM_NAME} root version is {root.get('version')!r}; "
                f"expected {expected_version!r}."
            )

    components = parsed.get("components")
    if not isinstance(components, list) or not components:
        report.violations.append(f"{SBOM_NAME} components must be a non-empty array.")
        return
    malformed = [
        index
        for index, component in enumerate(components)
        if not isinstance(component, dict)
        or not isinstance(component.get("name"), str)
        or not component.get("name")
        or not isinstance(component.get("version"), str)
        or not component.get("version")
    ]
    if malformed:
        report.violations.append(
            f"{SBOM_NAME} components lack string name/version at indexes: "
            + ", ".join(str(index) for index in malformed)
        )
    else:
        report.notes.append(f"SBOM lists {len(components)} components.")


def _validate_python_distributions(
    wheels: list[Path],
    sdists: list[Path],
    expected_version: str | None,
    report: Report,
) -> None:
    expected_archive_version = (
        _normalise_archive_version(expected_version) if expected_version is not None else None
    )
    parsed_wheels: dict[str, list[tuple[Path, str]]] = {}
    parsed_sdists: dict[str, list[tuple[Path, str]]] = {}
    for path, parser, records in (
        *((path, _parse_wheel_filename, parsed_wheels) for path in wheels),
        *((path, _parse_sdist_filename, parsed_sdists) for path in sdists),
    ):
        try:
            name, version = parser(path.name)
        except ValueError as exc:
            report.violations.append(f"cannot parse release distribution {path.name}: {exc}")
            continue
        records.setdefault(name, []).append((path, version))

    for kind, records in (("wheel", parsed_wheels), ("source distribution", parsed_sdists)):
        unexpected = set(records) - CANONICAL_DISTRIBUTIONS
        if unexpected:
            report.violations.append(f"unexpected {kind} names: " + ", ".join(sorted(unexpected)))
        for name in sorted(CANONICAL_DISTRIBUTIONS):
            matches = records.get(name, [])
            if len(matches) != 1:
                report.violations.append(
                    f"expected exactly one {kind} for {name}, found {len(matches)}."
                )
                continue
            path, version = matches[0]
            if expected_archive_version is not None and version != expected_archive_version:
                report.violations.append(
                    f"{path.name} carries version {version}; expected {expected_archive_version}."
                )


def _validate_provenance(path: Path, report: Report) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        report.violations.append(f"{path.name} is not readable UTF-8: {exc}")
        return
    if not text.strip():
        report.violations.append(f"{path.name} is empty.")
        return

    # GitHub currently emits JSON Lines. Accept a single ordinary JSON document
    # too so a formatting-only action change does not create a false red.
    try:
        documents: list[Any] = [json.loads(text)]
    except json.JSONDecodeError:
        documents = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                documents.append(json.loads(line))
            except json.JSONDecodeError as exc:
                report.violations.append(
                    f"{path.name} contains malformed JSON at line {line_number}: {exc}"
                )
                return
    if not documents or any(not isinstance(document, dict) for document in documents):
        report.violations.append(f"{path.name} must contain one or more JSON objects.")


def check(
    directory: Path,
    *,
    require_provenance: bool = True,
    expected_version: str | None = None,
    expected_python_distributions: int | None = None,
) -> Report:
    report = Report()
    if not directory.is_dir():
        report.violations.append(
            f"release directory does not exist or is not a directory: {directory}"
        )
        return report

    if expected_version is None:
        report.violations.append("an expected release version is required.")
    if expected_python_distributions != EXPECTED_DISTRIBUTION_COUNT:
        report.violations.append(
            f"expected Python distribution count must be {EXPECTED_DISTRIBUTION_COUNT}; "
            f"received {expected_python_distributions!r}."
        )

    _validate_sbom(directory / SBOM_NAME, expected_version, report)

    entries = list(directory.iterdir())
    unexpected_directories = sorted(path.name for path in entries if path.is_dir())
    if unexpected_directories:
        report.violations.append(
            "unexpected directories in release bundle: " + ", ".join(unexpected_directories)
        )
    payloads = sorted(path for path in entries if _is_payload(path))
    if not payloads:
        report.violations.append(f"no release artefacts found in {directory}.")

    for artefact in payloads:
        if artefact.name in EXPECTED_BINARIES:
            size_mb = artefact.stat().st_size / BYTES_PER_MB
            if size_mb > BINARY_MAX_MB:
                report.violations.append(
                    f"{artefact.name} is {size_mb:.1f} MB, over the "
                    f"{BINARY_MAX_MB} MB binary ceiling."
                )
        signature = directory / f"{artefact.name}.sig"
        certificate = directory / f"{artefact.name}.pem"
        if not _is_nonempty(signature):
            report.violations.append(
                f"{artefact.name} has no non-empty cosign signature. An unsigned artefact cannot "
                "be verified by anyone who did not build it."
            )
        if not _is_nonempty(certificate):
            report.violations.append(
                f"{artefact.name} has no non-empty signing certificate. Keyless verification "
                "cannot establish which workflow signed it."
            )

    wheels = [path for path in payloads if path.name.endswith(".whl")]
    sdists = [path for path in payloads if path.name.endswith(".tar.gz")]
    _validate_python_distributions(wheels, sdists, expected_version, report)

    other_payloads = {
        path.name
        for path in payloads
        if not path.name.endswith((".whl", ".tar.gz")) and path.name != SBOM_NAME
    }
    missing_binaries = EXPECTED_BINARIES - other_payloads
    unexpected_payloads = other_payloads - EXPECTED_BINARIES
    if missing_binaries:
        report.violations.append(
            "missing platform binaries: " + ", ".join(sorted(missing_binaries))
        )
    if unexpected_payloads:
        report.violations.append(
            "unexpected release payloads: " + ", ".join(sorted(unexpected_payloads))
        )

    expected_signatures = {f"{path.name}.sig" for path in payloads}
    expected_certificates = {f"{path.name}.pem" for path in payloads}
    actual_signatures = {path.name for path in directory.glob("*.sig")}
    actual_certificates = {path.name for path in directory.glob("*.pem")}
    orphan_evidence = (actual_signatures - expected_signatures) | (
        actual_certificates - expected_certificates
    )
    if orphan_evidence:
        report.violations.append(
            "signing evidence has no release payload: " + ", ".join(sorted(orphan_evidence))
        )

    provenance = list(directory.glob("*.intoto.jsonl"))
    if len(provenance) > 1 or (require_provenance and len(provenance) != 1):
        report.violations.append(
            "exactly one SLSA provenance attestation is required. Provenance is what ties a "
            "published artefact back to the commit and workflow that produced it."
        )
    if len(provenance) == 1:
        _validate_provenance(provenance[0], report)

    report.notes.append(f"checked {len(payloads)} artefact(s) in {directory}.")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--python-distributions", type=int, required=True)
    parser.add_argument(
        "--allow-missing-provenance",
        action="store_true",
        help="for non-publishing diagnostic runs where no attestation service is reachable",
    )
    args = parser.parse_args(argv)

    report = check(
        args.dir,
        require_provenance=not args.allow_missing_provenance,
        expected_version=args.version,
        expected_python_distributions=args.python_distributions,
    )
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
