"""`adopt version` -- contracts §14.

Output: ``{version, schema_version, export_version, sbom_sha256, build_id}``.

`sbom_sha256` and `build_id` are **build facts, not configuration**: they are
injected by the release job and are absent from a development checkout, where
they render as `null`. Reporting a fabricated build id would make the field
useless for the thing it exists for -- tying a binary in the field back to the
artifact that was signed.
"""

import os
from collections.abc import Mapping
from importlib import metadata
from typing import Any, Final

from adopt_const import EXPORT_VERSION, SCHEMA_VERSION

__all__ = ["build_payload"]

#: Set by the release workflow, absent everywhere else.
SBOM_DIGEST_ENV: Final[str] = "ADOPT_BUILD_SBOM_SHA256"
BUILD_ID_ENV: Final[str] = "ADOPT_BUILD_ID"

_DISTRIBUTION: Final[str] = "adopt-cli"


def _package_version() -> str:
    try:
        return metadata.version(_DISTRIBUTION)
    except metadata.PackageNotFoundError:  # pragma: no cover -- source checkout only
        return "0.0.0+unknown"


def build_payload(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = os.environ if env is None else env
    return {
        "version": _package_version(),
        "schema_version": SCHEMA_VERSION,
        "export_version": EXPORT_VERSION,
        "sbom_sha256": source.get(SBOM_DIGEST_ENV) or None,
        "build_id": source.get(BUILD_ID_ENV) or None,
    }
