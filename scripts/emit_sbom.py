"""Emit one target-aware CycloneDX SBOM for the release bundle.

PRD F15.6 and NFR N14: every release ships an SBOM, SLSA provenance and a
signature. A release missing any of the three is not a release.

The release builds binaries on Linux, macOS and Windows. Inventorising only the
Linux build environment silently omits marker-gated dependencies such as
``colorama ; sys_platform == 'win32'``. Instead this module consumes the exact
runtime constraints exported from ``uv.lock`` with ``--no-dev`` and
``--no-emit-workspace``. The constraints contain the union of the supported
platform closures, and each marker is retained on its component.

Licence and repository fields come from ``licence-verifications.md`` -- the
audited source -- rather than from whichever platform happens to emit the SBOM.
"""

import argparse
import hashlib
import json
import re
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from scripts.licence_gate import (
    REQUIRED_VERIFICATION_FIELDS,
    USAGE_IN_BINARY,
    VERIFICATIONS_PATH,
    normalise,
    parse_verifications,
)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
RUNTIME_CONSTRAINTS_PATH: Final[Path] = REPO_ROOT / "runtime-constraints.txt"
CYCLONEDX_SPEC_VERSION: Final[str] = "1.5"
CYCLONEDX_FORMAT: Final[str] = "CycloneDX"
SBOM_ROOT_NAME: Final[str] = "adopt-core"
MARKER_PROPERTY: Final[str] = "adopt:pep508-marker"

_EXACT_REQUIREMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"==(?P<version>[^\s;]+)(?:\s*;\s*(?P<marker>.+))?$"
)
_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.'\"<>=!~()\s-]+$")
_AUDIT_HASH_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True)
class RuntimeRequirement:
    name: str
    version: str
    marker: str | None = None


@dataclass(frozen=True)
class RuntimeDependency:
    name: str
    version: str
    licence: str
    repository: str
    marker: str | None = None


def parse_runtime_constraints(text: str) -> list[RuntimeRequirement]:
    """Parse exact ``name==version [; marker]`` entries or fail closed."""
    requirements: dict[str, RuntimeRequirement] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        match = _EXACT_REQUIREMENT_RE.fullmatch(line)
        if match is None:
            raise ValueError(
                f"runtime constraints line {line_number} is not one exact pinned requirement: "
                f"{raw!r}"
            )

        name = normalise(match.group("name"))
        version = match.group("version")
        marker = match.group("marker")
        if marker is not None:
            marker = marker.strip()
            if (
                not marker
                or _MARKER_RE.fullmatch(marker) is None
                or marker.count("'") % 2
                or marker.count('"') % 2
                or marker.count("(") != marker.count(")")
            ):
                raise ValueError(
                    f"runtime constraints line {line_number} has an invalid marker: {marker!r}"
                )
        if name.startswith(("adopt-", "plane-")):
            raise ValueError(
                f"runtime constraints unexpectedly include workspace distribution {name}; "
                "export with --no-emit-workspace"
            )
        if name in requirements:
            raise ValueError(f"runtime constraints contain duplicate distribution {name}")
        requirements[name] = RuntimeRequirement(name=name, version=version, marker=marker)

    if not requirements:
        raise ValueError("runtime constraints contain no dependencies")
    return [requirements[name] for name in sorted(requirements)]


def load_audited_runtime_dependencies(
    constraints_path: Path,
    verifications_path: Path = VERIFICATIONS_PATH,
) -> list[RuntimeDependency]:
    """Join the exported runtime closure to its audited licence records."""
    if not constraints_path.is_file():
        raise ValueError(f"runtime constraints are missing: {constraints_path}")
    if not verifications_path.is_file():
        raise ValueError(f"licence verifications are missing: {verifications_path}")

    requirements = parse_runtime_constraints(constraints_path.read_text(encoding="utf-8"))
    verifications = parse_verifications(verifications_path.read_text(encoding="utf-8"))
    dependencies: list[RuntimeDependency] = []
    for requirement in requirements:
        row = verifications.get(requirement.name)
        if row is None:
            raise ValueError(
                f"runtime dependency {requirement.name} has no audited licence verification"
            )
        missing = [
            field for field in (*REQUIRED_VERIFICATION_FIELDS, "licence") if not row.get(field)
        ]
        if missing:
            raise ValueError(
                f"audit row for {requirement.name} is missing: {', '.join(sorted(missing))}"
            )
        if row["version"] != requirement.version:
            raise ValueError(
                f"runtime dependency {requirement.name} is pinned at {requirement.version}, "
                f"but its audited version is {row['version']}"
            )
        if row["usage_mode"] != USAGE_IN_BINARY:
            raise ValueError(
                f"runtime dependency {requirement.name} is audited as "
                f"{row['usage_mode']!r}, not {USAGE_IN_BINARY!r}"
            )
        recorded_hash = row["licence_hash"].lower()
        if _AUDIT_HASH_RE.fullmatch(recorded_hash) is None:
            raise ValueError(
                f"audit row for {requirement.name} has an invalid licence hash: "
                f"{row['licence_hash']!r}"
            )
        dependencies.append(
            RuntimeDependency(
                name=requirement.name,
                version=requirement.version,
                licence=row["licence"],
                repository=row["repository"],
                marker=requirement.marker,
            )
        )
    return dependencies


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{name}@{version}"


def _component(dependency: RuntimeDependency) -> dict[str, Any]:
    component: dict[str, Any] = {
        "type": "library",
        "bom-ref": _purl(dependency.name, dependency.version),
        "name": dependency.name,
        "version": dependency.version,
        "purl": _purl(dependency.name, dependency.version),
        "licenses": [{"expression": dependency.licence}],
        "externalReferences": [{"type": "vcs", "url": dependency.repository}],
    }
    if dependency.marker is not None:
        component["properties"] = [{"name": MARKER_PROPERTY, "value": dependency.marker}]
    return component


def build_sbom(
    root_name: str,
    root_version: str,
    dependencies: Sequence[RuntimeDependency],
) -> dict[str, Any]:
    if not root_name or not root_version:
        raise ValueError("SBOM root name and version must be non-empty")
    if not dependencies:
        raise ValueError("SBOM runtime dependency set must be non-empty")
    return {
        "bomFormat": CYCLONEDX_FORMAT,
        "specVersion": CYCLONEDX_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "component": {
                "type": "application",
                "bom-ref": _purl(root_name, root_version),
                "name": root_name,
                "version": root_version,
            },
            "tools": [{"name": "adopt-sbom", "version": root_version}],
        },
        "components": [_component(dependency) for dependency in dependencies],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", default=SBOM_ROOT_NAME)
    parser.add_argument("--version", default="0.0.0")
    parser.add_argument("--constraints", type=Path, default=RUNTIME_CONSTRAINTS_PATH)
    parser.add_argument("--verifications", type=Path, default=VERIFICATIONS_PATH)
    args = parser.parse_args(argv)

    try:
        dependencies = load_audited_runtime_dependencies(args.constraints, args.verifications)
        sbom = build_sbom(args.name, args.version, dependencies)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"VIOLATION: {exc}")
        return 1

    # Sorted keys and LF make the exact shipped bytes independent of host OS.
    # Timestamp and UUID intentionally distinguish separately generated SBOMs.
    payload = json.dumps(sbom, indent=2, sort_keys=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8", newline="\n")

    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    print(f"sbom: {len(sbom['components'])} components -> {args.out}")
    print(f"sbom sha256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
