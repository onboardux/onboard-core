"""Immutable facts stamped into release artifacts.

Development checkouts and ordinary local builds intentionally carry ``None``.
The release workflow replaces this module in its ephemeral checkout *before*
building the wheel and onefile binaries, so an installed artifact reports the
SBOM it actually shipped with and the workflow run that produced it.
"""

from typing import Final

SBOM_SHA256: Final[str | None] = None
BUILD_ID: Final[str | None] = None
