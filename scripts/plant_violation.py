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
#: so the planted violation is unambiguously "a column was removed" and not a
#: cascade of consequential errors that could mask the rule under test.
DROPPED_TABLE: Final[str] = "knowledge_item"
DROPPED_COLUMN: Final[str] = "data_residency_region"


def _backup(path: Path) -> None:
    Path(str(path) + BACKUP_SUFFIX).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def plant_drop_column() -> str:
    """Remove a shipped column from the manifest -- PRD F2.2's `column-removed`."""
    text = MANIFEST.read_text(encoding="utf-8")
    needle = f"{{ name: {DROPPED_COLUMN},"
    kept = [line for line in text.splitlines(keepends=True) if needle not in line]
    if len(kept) == len(text.splitlines(keepends=True)):
        raise SystemExit(
            f"{DROPPED_TABLE}.{DROPPED_COLUMN} is not in the manifest in the expected form, "
            "so nothing was planted. Update this script rather than leaving the gate unproven."
        )
    _backup(MANIFEST)
    MANIFEST.write_text("".join(kept), encoding="utf-8", newline="")
    return f"removed {DROPPED_TABLE}.{DROPPED_COLUMN} from {MANIFEST.name}"


KINDS: Final[dict[str, Callable[[], str]]] = {
    "drop-column": plant_drop_column,
}


def revert() -> list[str]:
    restored: list[str] = []
    for backup in sorted(REPO_ROOT.rglob(f"*{BACKUP_SUFFIX}")):
        original = Path(str(backup)[: -len(BACKUP_SUFFIX)])
        original.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8", newline="")
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
