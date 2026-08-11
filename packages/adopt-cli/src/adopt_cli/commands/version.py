"""`adopt version` -- contracts §14.

Output: ``{version, schema_version, export_version, sbom_sha256, build_id}``.

`sbom_sha256` and `build_id` are **build facts, not configuration**: they are
injected by the release job and are absent from a development checkout, where
they render as `null`. Reporting a fabricated build id would make the field
useless for the thing it exists for -- tying a binary in the field back to the
artifact that was signed.
"""

from importlib import metadata
from typing import Any, Final

from adopt_cli._build_info import BUILD_ID, SBOM_SHA256
from adopt_const import EXPORT_VERSION, SCHEMA_VERSION

__all__ = ["build_payload"]

_DISTRIBUTION: Final[str] = "adopt-cli"


def _package_version() -> str:
    try:
        return metadata.version(_DISTRIBUTION)
    except metadata.PackageNotFoundError:  # pragma: no cover -- source checkout only
        return "0.0.0+unknown"


def build_payload() -> dict[str, Any]:
    return {
        "version": _package_version(),
        "schema_version": SCHEMA_VERSION,
        "export_version": EXPORT_VERSION,
        "sbom_sha256": SBOM_SHA256,
        "build_id": BUILD_ID,
    }
