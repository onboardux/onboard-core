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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final

from adopt_obs.errors import ERROR_CATEGORIES, ErrorCategory, ErrorCode

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

DEFAULT_CONTRACTS_PATH: Final[Path] = (
    REPO_ROOT.parent / "builds" / "build_0" / "02-contracts-build0.md"
)

#: Build 1 registers its codes in the **same** module (`adopt_obs.errors`) and
#: declares them in its own contracts document. One registry, many declaring
#: documents -- the same shape as `constants_sync`.
DEFAULT_BUILD1_CONTRACTS_PATH: Final[Path] = (
    REPO_ROOT.parent / "builds" / "build_1" / "02-contracts.md"
)

#: `## 13. …` (build_0) and `### 1.4 …` (build_1) both open a section. Each pack
#: numbers its own, so the gate reads the number and the caller names it.
_SECTION_RE: Final[re.Pattern[str]] = re.compile(r"^#{2,4}\s+(\d+(?:\.\d+)?)[.\s]")
_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^#{1,6}\s")
_BACKTICK_RE: Final[re.Pattern[str]] = re.compile(r"`([A-Z][A-Z0-9_]+)`")

_VALID_CATEGORIES: Final[frozenset[str]] = frozenset(c.value for c in ErrorCategory)


@dataclass(frozen=True)
class RegistryDocument:
    """One pack's contracts document, and which section carries its code table.

    `enforce_completeness` distinguishes a finished build from one in progress:
    a documented-but-unimplemented code is a defect once its build has shipped,
    and merely *pending* while it is being built. Pending codes print on every
    run with a count, so "specified, not yet built" is never a silence.
    """

    path: Path
    label: str
    section: str
    #: Where this document sits under the pack's `builds/` root. CI checks the
    #: pack out at `pack/` rather than as a sibling, so overriding one document's
    #: path must relocate the others -- otherwise CI resolves build_0, silently
    #: skips build_1, and enforces a different rule than a developer's machine.
    relative_hint: Path
    enforce_completeness: bool = True
    required: bool = True


DEFAULT_REGISTRY_DOCUMENTS: Final[tuple[RegistryDocument, ...]] = (
    RegistryDocument(
        path=DEFAULT_CONTRACTS_PATH,
        label="build_0 §13",
        section="13",
        relative_hint=Path("build_0") / "02-contracts-build0.md",
    ),
    RegistryDocument(
        path=DEFAULT_BUILD1_CONTRACTS_PATH,
        label="build_1 §1.4",
        section="1.4",
        relative_hint=Path("build_1") / "02-contracts.md",
        enforce_completeness=False,
        required=False,
    ),
)


def resolve_documents(
    documents: tuple[RegistryDocument, ...], overrides: list[Path]
) -> tuple[RegistryDocument, ...]:
    """Apply overrides positionally, relocating the rest with the pack."""
    if not overrides:
        return documents

    builds_root = overrides[0].resolve().parent.parent
    resolved: list[RegistryDocument] = []
    for index, document in enumerate(documents):
        if index < len(overrides):
            resolved.append(replace(document, path=overrides[index]))
        else:
            resolved.append(replace(document, path=builds_root / document.relative_hint))
    return tuple(resolved)


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Codes a document declares that the module does not implement yet.
    pending: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _in_section(current: str | None, target: str) -> bool:
    """True inside the target section, including any subsection of it.

    `13.1` is inside `13`; `14` is not. Prefix matching without the dot would
    put `1.40` inside `1.4`, which is why the separator is part of the test.
    """
    if current is None:
        return False
    return current == target or current.startswith(f"{target}.")


def parse_registry(text: str, section: str = "13") -> dict[str, str]:
    """Extract ``{code: category}`` from a contracts document's registry table."""
    registry: dict[str, str] = {}
    current: str | None = None

    for raw in text.splitlines():
        section_match = _SECTION_RE.match(raw)
        if section_match:
            current = section_match.group(1)
            continue
        if _HEADING_RE.match(raw):
            current = None
            continue
        if not _in_section(current, section) or not raw.lstrip().startswith("|"):
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


def run(documents: tuple[RegistryDocument, ...]) -> Report:
    report = Report()
    implemented = {str(code): str(ERROR_CATEGORIES[code]) for code in ErrorCode}
    documented: dict[str, str] = {}
    origin: dict[str, str] = {}

    for document in documents:
        if not document.path.exists():
            if document.required:
                report.violations.append(
                    f"contracts document not found at {document.path}. "
                    "Set ADOPT_CONTRACTS_PATH or pass --contracts: the registry is the "
                    "source of truth and this gate cannot run without it."
                )
            else:
                report.notes.append(
                    f"{document.label}: not present at {document.path}; skipped. "
                    "Any code it would declare will be reported as undocumented instead."
                )
            continue

        declared = parse_registry(document.path.read_text(encoding="utf-8"), document.section)
        report.notes.append(f"{document.label}: documents {len(declared)} codes.")

        for code, category in declared.items():
            if code not in implemented and not document.enforce_completeness:
                report.pending.append(f"{document.label}: {code}")
                continue
            if code in documented and documented[code] != category:
                report.violations.append(
                    f"{code} is declared in {origin[code]} as {documented[code]!r} and in "
                    f"{document.label} as {category!r}. One code, one category."
                )
                continue
            documented[code] = category
            origin.setdefault(code, document.label)

    if report.violations:
        return report

    for code in sorted(set(documented) - set(implemented)):
        report.violations.append(
            f"{code} is in {origin[code]} but not in adopt_obs.errors.ErrorCode. "
            "A documented code with no implementation is behaviour a caller will handle "
            "and never receive."
        )
    for code in sorted(set(implemented) - set(documented)):
        searched = ", ".join(d.label for d in documents)
        report.violations.append(
            f"{code} is in adopt_obs.errors.ErrorCode but in no registry table. "
            f"Searched: {searched}. An undocumented code is one a client can receive "
            "with no published meaning."
        )
    for code in sorted(set(documented) & set(implemented)):
        if documented[code] != implemented[code]:
            report.violations.append(
                f"{code}: {origin[code]} says category {documented[code]!r}, the module says "
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
        action="append",
        help=(
            "override a declaring document's path, positionally against the default "
            "list (build_0, then build_1). Repeatable."
        ),
    )
    args = parser.parse_args(argv)

    overrides: list[Path] = list(args.contracts or [])
    if not overrides and "ADOPT_CONTRACTS_PATH" in os.environ:
        overrides = [Path(os.environ["ADOPT_CONTRACTS_PATH"])]
    documents = resolve_documents(DEFAULT_REGISTRY_DOCUMENTS, overrides)

    report = run(documents)
    for note in report.notes:
        print(f"note: {note}")
    for entry in report.pending:
        print(f"pending: {entry}")
    if report.pending:
        print(f"note: {len(report.pending)} declared code(s) not yet implemented.")
    for violation in report.violations:
        print(f"VIOLATION: {violation}")

    if report.ok:
        print("error-registry-sync: OK")
        return 0
    print(f"error-registry-sync: {len(report.violations)} violation(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
