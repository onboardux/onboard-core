"""Plant a violation so a gate can be watched failing.

A gate nobody has seen fail is a gate nobody should trust. Every gate in this
repository was proven by a planted violation before it was wired in, and this
script is how the schema gates are proven -- in CI, on every run, not once during
development and never again.

Each kind writes a backup beside the file it edits, so `--revert` restores the
tree exactly without depending on the state of the index or the working tree.

    python scripts/plant_violation.py --kind drop-column
    uv run adopt-schema lint --base HEAD~1     # must fail SCHEMA_NON_ADDITIVE
    python scripts/plant_violation.py --revert
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
MANIFEST: Final[Path] = REPO_ROOT / "schema" / "canonical.yaml"
BACKUP_SUFFIX: Final[str] = ".planted-backup"

#: The column `drop-column` removes. A nullable leaf column nothing references,
#: whose name appears **exactly once** in the manifest -- so the planted
#: violation is unambiguously "this one column was removed" rather than a
#: coincidental match across several tables, which would make the gate proof
#: report a different table from the one this script claims to have edited.
DROPPED_TABLE: Final[str] = "engagement"
DROPPED_COLUMN: Final[str] = "client_label"


def _backup(path: Path) -> None:
    """Byte-for-byte, so `--revert` restores the file and not an approximation.

    Text-mode round-tripping would silently rewrite line endings, which leaves
    the tree dirty after a gate proof and teaches people to ignore that dirtiness.
    """
    Path(str(path) + BACKUP_SUFFIX).write_bytes(path.read_bytes())


def plant_drop_column() -> str:
    """Remove a shipped column from the manifest -- PRD F2.2's `column-removed`."""
    original = MANIFEST.read_bytes()
    needle = f"{{ name: {DROPPED_COLUMN},".encode()
    kept = [line for line in original.splitlines(keepends=True) if needle not in line]
    if len(kept) == len(original.splitlines(keepends=True)):
        raise SystemExit(
            f"{DROPPED_TABLE}.{DROPPED_COLUMN} is not in the manifest in the expected form, "
            "so nothing was planted. Update this script rather than leaving the gate unproven."
        )
    _backup(MANIFEST)
    MANIFEST.write_bytes(b"".join(kept))
    return f"removed {DROPPED_TABLE}.{DROPPED_COLUMN} from {MANIFEST.name}"


KINDS: Final[dict[str, Callable[[], str]]] = {
    "drop-column": plant_drop_column,
}


def revert() -> list[str]:
    restored: list[str] = []
    for backup in sorted(REPO_ROOT.rglob(f"*{BACKUP_SUFFIX}")):
        original = Path(str(backup)[: -len(BACKUP_SUFFIX)])
        original.write_bytes(backup.read_bytes())
        backup.unlink()
        restored.append(str(original.relative_to(REPO_ROOT)))
    return restored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--kind", choices=sorted(KINDS), help="The violation to plant.")
    parser.add_argument("--revert", action="store_true", help="Restore every planted file.")
    args = parser.parse_args(argv)

    if args.revert:
        restored = revert()
        print("reverted: " + (", ".join(restored) if restored else "nothing was planted"))
        return 0

    if not args.kind:
        parser.error("give --kind or --revert")

    print(f"planted {args.kind}: {KINDS[args.kind]()}")
    print("The gate must now FAIL. Run `--revert` afterwards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
