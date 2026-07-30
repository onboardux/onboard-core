"""`error-registry-sync`: contracts §13 and the error module must agree.

The check is **bidirectional and includes the category**, because all three
failure directions have different causes and all three ship:

* a code in the document but not the module -- documented behaviour that does
  not exist, which a downstream team will write a handler for;
* a code in the module but not the document -- an error a client can receive
  with no published meaning, which turns a support call into an archaeology
  exercise;
* a code whose category differs -- the category decides the process exit code,
  so a mismatch means the document promises exit 3 and the binary returns 1.

Codes and categories are parsed out of the §13 table. Rows declaring several
codes at once (``A / B``) pair positionally with their categories, and a single
category is broadcast across all codes on the row.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from adopt_obs.errors import ERROR_CATEGORIES, ErrorCategory, ErrorCode

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

DEFAULT_CONTRACTS_PATH: Final[Path] = (
    REPO_ROOT.parent / "builds" / "build_0" / "02-contracts-build0.md"
)

_SECTION_START: Final[re.Pattern[str]] = re.compile(r"^##\s+13\.")
_SECTION_END: Final[re.Pattern[str]] = re.compile(r"^##\s+(?!13\.)")
_BACKTICK_RE: Final[re.Pattern[str]] = re.compile(r"`([A-Z][A-Z0-9_]+)`")

_VALID_CATEGORIES: Final[frozenset[str]] = frozenset(c.value for c in ErrorCategory)


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def parse_registry(text: str) -> dict[str, str]:
    """Extract ``{code: category}`` from the contracts §13 table."""
    registry: dict[str, str] = {}
    inside = False

    for raw in text.splitlines():
        if _SECTION_START.match(raw):
            inside = True
            continue
        if inside and _SECTION_END.match(raw):
            break
        if not inside or not raw.lstrip().startswith("|"):
            continue

        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        min_cells = 2
        if len(cells) < min_cells:
            continue
        codes = _BACKTICK_RE.findall(cells[0])
        if not codes:
            continue
        categories = [
            part.strip().strip("*`")
            for part in cells[1].split("/")
            if part.strip().strip("*`") in _VALID_CATEGORIES
        ]
        if not categories:
            continue
        if len(categories) == 1 and len(codes) > 1:
            categories = categories * len(codes)
        if len(codes) != len(categories):
            raise ValueError(
                f"§13 row declares {len(codes)} codes but {len(categories)} categories: {codes}"
            )
        registry.update(dict(zip(codes, categories, strict=True)))

    return registry


def run(contracts_path: Path) -> Report:
    report = Report()
    if not contracts_path.exists():
        report.violations.append(
            f"contracts document not found at {contracts_path}. "
            "Set ADOPT_CONTRACTS_PATH or pass --contracts: §13 is the registry and this "
            "gate cannot run without it."
        )
        return report

    documented = parse_registry(contracts_path.read_text(encoding="utf-8"))
    implemented = {str(code): str(ERROR_CATEGORIES[code]) for code in ErrorCode}
    report.notes.append(
        f"§13 documents {len(documented)} codes; the module implements {len(implemented)}."
    )

    for code in sorted(set(documented) - set(implemented)):
        report.violations.append(
            f"{code} is in contracts §13 but not in adopt_obs.errors.ErrorCode. "
            "A documented code with no implementation is behaviour a caller will handle "
            "and never receive."
        )
    for code in sorted(set(implemented) - set(documented)):
        report.violations.append(
            f"{code} is in adopt_obs.errors.ErrorCode but not in contracts §13. "
            "An undocumented code is one a client can receive with no published meaning."
        )
    for code in sorted(set(documented) & set(implemented)):
        if documented[code] != implemented[code]:
            report.violations.append(
                f"{code}: contracts §13 says category {documented[code]!r}, the module says "
                f"{implemented[code]!r}. The category decides the process exit code, so the "
                "document and the binary currently disagree about what a caller sees."
            )

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit non-zero on any violation")
    parser.add_argument(
        "--contracts",
        type=Path,
        default=Path(os.environ.get("ADOPT_CONTRACTS_PATH", DEFAULT_CONTRACTS_PATH)),
    )
    args = parser.parse_args(argv)

    report = run(args.contracts)
    for note in report.notes:
        print(f"note: {note}")
    for violation in report.violations:
        print(f"VIOLATION: {violation}")

    if report.ok:
        print("error-registry-sync: OK")
        return 0
    print(f"error-registry-sync: {len(report.violations)} violation(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
