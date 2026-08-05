"""`verify_roundtrip` — the G0 comparison, and where `EXPORT_ROUNDTRIP_UNSTABLE` lives.

The comparison is here rather than inside the test for two reasons. It is a
product statement, not a test convenience: contracts §13 registers
`EXPORT_ROUNDTRIP_UNSTABLE` as an error the programme raises, and an error code
whose only caller is an assertion is a code that means nothing to anyone
receiving it. And a comparison written inline in a test is a comparison nobody
ever watches fail, which is the same objection the planted-violation discipline
makes of every other gate here.

**Table files only.** `manifest.json` carries `written_at`, which differs
between two exports by design (PRD F9.2). Comparing it would make G0 fail on
every run for the one reason that is not a defect.
"""

from pathlib import Path
from typing import Final

from adopt_export.bundle import TABLES_DIRNAME
from adopt_obs import AdoptError, ErrorCode

__all__ = ["table_files", "verify_roundtrip"]

_FIRST: Final[str] = "first"
_SECOND: Final[str] = "second"


def table_files(bundle: Path) -> dict[str, bytes]:
    """Every `tables/*.ndjson` in a bundle, by file name, as raw bytes."""
    directory = bundle / TABLES_DIRNAME
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


def verify_roundtrip(first: Path, second: Path) -> None:
    """Raise unless two bundles' table files are byte-identical.

    Args:
        first: The bundle exported from the original store.
        second: The bundle exported from the store the first was imported into.

    Raises:
        AdoptError: ``EXPORT_ROUNDTRIP_UNSTABLE``, naming the files that differ
            and how -- present in one bundle only, or present in both with
            different bytes.
    """
    left = table_files(first)
    right = table_files(second)

    differences: list[str] = []
    for name in sorted(set(left) | set(right)):
        if name not in right:
            differences.append(f"{name}: in the {_FIRST} bundle only")
        elif name not in left:
            differences.append(f"{name}: in the {_SECOND} bundle only")
        elif left[name] != right[name]:
            differences.append(
                f"{name}: {len(left[name])} bytes then {len(right[name])} bytes, and they differ"
            )

    if differences:
        raise AdoptError(
            ErrorCode.EXPORT_ROUNDTRIP_UNSTABLE,
            message="re-export is not byte-identical: " + "; ".join(differences),
            hint="The bundle a client keeps must survive a round trip unchanged. Look for "
            "a rendering that depends on something other than the rows -- a key order, a "
            "timestamp format, a collation -- rather than for a difference in the data.",
        )
