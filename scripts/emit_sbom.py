"""Emit a CycloneDX SBOM from the installed distribution set.

PRD F15.6 and NFR N14: every release ships an SBOM, SLSA provenance and a
signature. A release missing any of the three is not a release.

Generated from `importlib.metadata` rather than from a lockfile, so the SBOM
describes **what was actually installed into the artifact being shipped**, not
what a resolver said would be. Those two have differed, and when they do it is
the installed set that a vulnerability scanner will be asked about.

No new dependency: this reuses the same metadata reader the licence gate uses,
which keeps the SBOM and the licence records describing the same tree.
"""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from scripts.licence_gate import installed_dependencies

CYCLONEDX_SPEC_VERSION: Final[str] = "1.5"
CYCLONEDX_FORMAT: Final[str] = "CycloneDX"


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{name}@{version}"


def _component(name: str, version: str, licence: str, repository: str) -> dict[str, Any]:
    component: dict[str, Any] = {
        "type": "library",
        "bom-ref": _purl(name, version),
        "name": name,
        "version": version,
        "purl": _purl(name, version),
    }
    if licence and licence != "UNKNOWN":
        component["licenses"] = [{"expression": licence}]
    if repository:
        component["externalReferences"] = [{"type": "vcs", "url": repository}]
    return component


def build_sbom(root_name: str, root_version: str) -> dict[str, Any]:
    dependencies = installed_dependencies()
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
        "components": [
            _component(dep.name, dep.version, dep.licence, dep.repository)
            for dep in dependencies
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", default="adopt-core")
    parser.add_argument("--version", default="0.0.0")
    args = parser.parse_args(argv)

    sbom = build_sbom(args.name, args.version)
    # Sorted keys and a fixed separator: the SBOM must be byte-reproducible
    # apart from its timestamp and serial number, or its digest is meaningless.
    payload = json.dumps(sbom, indent=2, sort_keys=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")

    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    print(f"sbom: {len(sbom['components'])} components -> {args.out}")
    print(f"sbom sha256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
