"""`constants-sync`: the constants table and the constants modules must agree.

Three failures are caught here, and each one has been shipped by a real team
that had only two of the three checks.

1. **Drift.** A value in ``03-implementation-spec-build0.md`` §2 differs from
   the module that declares it. The document is what a reviewer reads; the
   module is what runs. When they disagree, every review after that point is
   reviewing fiction.
2. **A shared name.** A name declared in both ``adopt_const`` and
   ``plane_const``. Two modules, one name: every call site is ambiguous and the
   winner is decided by import order.
3. **An inlined tunable.** A numeric literal in non-test source that equals a
   declared tunable. This is the one that bites in production -- someone types
   the number instead of importing it, the constant is later retuned, and one
   copy silently keeps the old behaviour.

**The literal rule, stated so it can be argued with.** A numeric literal in
non-test source is a violation when it equals a declared tunable value and is
not one of the structurally unavoidable values ``0``, ``1``, ``2`` and ``-1``.
Those four appear in indexing, arity and sign arithmetic in every codebase, and
flagging them would produce noise that ends with the gate being switched off --
which is worse than not having it. A genuine exception is waived inline with
``# const-sync: ok -- <reason>``; waivers are **reported on every run**, so a
waiver is a visible standing decision rather than a silent escape.

Prose and docstrings are scanned more narrowly, for values that cannot be
coincidental: any float tunable, and any integer tunable of 1000 or more.
"Item 10" in a sentence is not a duplicated tunable; "0.92" is.
"""

import argparse
import ast
import contextlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: The pack is the source of truth and lives outside either product repository.
#: In CI both are checked out side by side; `ADOPT_IMPL_SPEC_PATH` overrides.
DEFAULT_SPEC_PATH: Final[Path] = (
    REPO_ROOT.parent / "builds" / "build_0" / "03-implementation-spec-build0.md"
)

CORE_MODULE: Final[Path] = (
    REPO_ROOT / "packages" / "adopt-const" / "src" / "adopt_const" / "__init__.py"
)
DEFAULT_PLANE_MODULE: Final[Path] = (
    REPO_ROOT.parent
    / "adopt-plane"
    / "packages"
    / "plane-const"
    / "src"
    / "plane_const"
    / "__init__.py"
)

CORE_SECTIONS: Final[tuple[str, ...]] = ("2.1", "2.2", "2.3")
PLANE_SECTIONS: Final[tuple[str, ...]] = ("2.4",)

#: Values too structurally common to flag. See the module docstring.
STRUCTURAL_VALUES: Final[frozenset[int]] = frozenset({-1, 0, 1, 2})

#: Above this, an integer in prose cannot plausibly be a coincidence.
PROSE_INTEGER_FLOOR: Final[int] = 1000

WAIVER: Final[str] = "const-sync: ok"

_SECTION_RE: Final[re.Pattern[str]] = re.compile(r"^###\s+(\d+\.\d+)\s")
_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^#{1,6}\s")
_BACKTICK_RE: Final[re.Pattern[str]] = re.compile(r"`([^`]+)`")
_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"(?<![\w.])(\d[\d_]*\.\d+|\d[\d_]*)(?![\w.])")

ScalarValue = int | float | str


@dataclass
class Declaration:
    name: str
    value: ScalarValue
    section: str
    source_line: int


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    waivers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


# ---------------------------------------------------------------------------
# Parsing the specification tables
# ---------------------------------------------------------------------------


def _coerce(token: str) -> ScalarValue:
    """Read a table cell as int, then float, then string.

    A ladder, not a swallow: each rung is a legitimate declared type, and the
    string rung is where `SLUG_PATTERN` lands.
    """
    cleaned = token.strip()
    numeric = cleaned.replace("_", "")
    with contextlib.suppress(ValueError):
        return int(numeric)
    with contextlib.suppress(ValueError):
        return float(numeric)
    return cleaned


def _expand_shorthand(previous: str | None, token: str) -> str:
    """Expand the ``NAME_BASE_MS / _MAX_MS`` shorthand used in the tables."""
    if not token.startswith("_") or previous is None:
        return token
    trailing = len(token.strip("_").split("_"))
    stem = previous.split("_")
    if trailing >= len(stem):
        return token
    return "_".join(stem[:-trailing]) + token


def parse_spec(text: str, sections: tuple[str, ...]) -> list[Declaration]:
    """Extract every ``(name, value)`` pair from the named §2 tables."""
    declarations: list[Declaration] = []
    current: str | None = None
    previous_name: str | None = None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        section_match = _SECTION_RE.match(raw)
        if section_match:
            current = section_match.group(1)
            previous_name = None
            continue
        if _HEADING_RE.match(raw) and not section_match:
            if current is not None and not raw.startswith("###"):
                current = None
            continue
        if current not in sections or not raw.lstrip().startswith("|"):
            continue

        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        min_cells = 2
        if len(cells) < min_cells:
            continue
        # Shorthand expands against the previous name **in the same cell** first
        # (`FOO_BASE_MS / _MAX_MS`), falling back to the previous row.
        names: list[str] = []
        anchor = previous_name
        for token in _BACKTICK_RE.findall(cells[0]):
            expanded = _expand_shorthand(anchor, token)
            names.append(expanded)
            anchor = expanded
        values = _BACKTICK_RE.findall(cells[1])
        # Header rows, separator rows and the bold sub-heading rows in §2.4
        # legitimately carry no backticked name/value pair.
        if not names or not values:
            continue
        if len(values) == 1 and len(names) > 1:
            values = values * len(names)
        if len(names) != len(values):
            raise ValueError(
                f"§{current} line {lineno}: {len(names)} names but {len(values)} values"
            )
        for name, value in zip(names, values, strict=True):
            declarations.append(Declaration(name, _coerce(value), current or "", lineno))
            previous_name = name

    return declarations


# ---------------------------------------------------------------------------
# Parsing a constants module
# ---------------------------------------------------------------------------


def parse_module(path: Path) -> dict[str, ScalarValue]:
    """Read every module-level ``NAME: Final[...] = literal`` declaration."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, ScalarValue] = {}
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or node.value is None:
            continue
        if not isinstance(node.target, ast.Name):
            continue
        annotation = ast.unparse(node.annotation)
        if not annotation.startswith("Final"):
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError:
            continue
        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
            found[node.target.id] = value
    return found


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_module_matches_spec(
    declarations: list[Declaration], module: dict[str, ScalarValue], module_name: str
) -> list[str]:
    violations: list[str] = []
    declared = {d.name: d for d in declarations}

    for name, spec in sorted(declared.items()):
        if name not in module:
            violations.append(
                f"{module_name}: §{spec.section} declares {name} but the module does not. "
                "Add it, or remove the row -- the table and the module change together."
            )
        elif module[name] != spec.value:
            violations.append(
                f"{module_name}: {name} is {module[name]!r} in the module but "
                f"{spec.value!r} in §{spec.section}."
            )
    for name in sorted(set(module) - set(declared)):
        violations.append(
            f"{module_name}: {name} is declared in the module but appears in no §2 table. "
            "Every tunable has exactly one documented home."
        )
    return violations


def check_uniqueness(core: list[Declaration], plane: list[Declaration]) -> list[str]:
    shared = {d.name for d in core} & {d.name for d in plane}
    return [
        f"{name} is declared in both adopt_const and plane_const. "
        "A name in both modules makes every call site ambiguous."
        for name in sorted(shared)
    ]


def _iter_sources(roots: list[Path]) -> list[Path]:
    excluded_parts = {"tests", ".venv", "__pycache__", "node_modules", ".git"}
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if excluded_parts & set(path.parts):
                continue
            if path.resolve() in {CORE_MODULE.resolve()}:
                continue
            files.append(path)
    return files


def _display_path(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise.

    The gate's own tests scan a temporary directory, which is not under the
    repository root; a hard `relative_to` there raises instead of reporting.
    """
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def check_inlined_literals(
    values: set[ScalarValue], roots: list[Path]
) -> tuple[list[str], list[str]]:
    """Flag numeric literals in non-test source that duplicate a tunable."""
    violations: list[str] = []
    waivers: list[str] = []
    numeric = {v for v in values if isinstance(v, (int, float))}
    flagged = {v for v in numeric if not (isinstance(v, int) and v in STRUCTURAL_VALUES)}
    if not flagged:
        return violations, waivers

    for path in _iter_sources(roots):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:  # pragma: no cover -- ruff catches these first
            violations.append(f"{path}: cannot parse ({exc})")
            continue
        rel = _display_path(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                continue
            if node.value not in flagged:
                continue
            # A waiver may sit on the literal's own line or on the line above,
            # so a long declaration can carry its reason without wrapping.
            window = lines[max(0, node.lineno - 2) : node.lineno]
            entry = f"{rel}:{node.lineno}: literal {node.value!r} duplicates a tunable"
            if any(WAIVER in line for line in window):
                waivers.append(entry + " (waived)")
            else:
                violations.append(
                    entry + ". Import it from the constants module, or waive it "
                    f"inline with `# {WAIVER} -- <reason>` if it is genuinely a "
                    "different number that happens to share a value."
                )
    return violations, waivers


def check_prose(values: set[ScalarValue], roots: list[Path]) -> list[str]:
    """Flag a tunable's value written out in a docstring, comment or prompt.

    Scoped to text **we** author -- source docstrings, source comments and
    prompt files. The pack's own §2 tables are the declaration site and are not
    scanned: flagging the table that declares the value would make the gate
    unrunnable, and prose elsewhere in the pack legitimately cites the table.

    Only distinctive values are checked (any float tunable, any integer tunable
    of `PROSE_INTEGER_FLOOR` or more). "Item 10" in a sentence is not a
    duplicated tunable; "0.92" is.
    """
    distinctive = {
        v
        for v in values
        if isinstance(v, float) or (isinstance(v, int) and abs(v) >= PROSE_INTEGER_FLOOR)
    }
    if not distinctive:
        return []

    violations: list[str] = []
    candidates = _iter_sources(roots)
    prompts_root = REPO_ROOT / "prompts"
    if prompts_root.exists():
        candidates += [p for p in sorted(prompts_root.rglob("*")) if p.is_file()]

    for path in candidates:
        text = path.read_text(encoding="utf-8")
        rel = _display_path(path)
        for lineno, raw in enumerate(text.splitlines(), start=1):
            if WAIVER in raw:
                continue
            if path.suffix == ".py" and not _is_prose_line(raw):
                continue
            for token in _NUMBER_RE.findall(raw):
                if _coerce(token) in distinctive:
                    violations.append(
                        f"{rel}:{lineno}: prose states {token}, which is a tunable value. "
                        "Name the constant instead -- prose does not get updated when the "
                        "constant is retuned."
                    )
    return violations


def _is_prose_line(raw: str) -> bool:
    """True for a comment or a line that is plainly docstring prose.

    Deliberately conservative rather than a full tokenizer: a missed docstring
    line costs a stale sentence, whereas a false positive on real code costs
    the gate's credibility.
    """
    stripped = raw.strip()
    if stripped.startswith("#"):
        return True
    return "#" in raw and not stripped.startswith(('"', "'"))


def run(spec_path: Path, plane_module_path: Path, *, scan_prose: bool) -> Report:
    report = Report()
    if not spec_path.exists():
        report.violations.append(
            f"implementation spec not found at {spec_path}. "
            "Set ADOPT_IMPL_SPEC_PATH or pass --spec: the §2 tables are the source of truth "
            "and this gate cannot run without them."
        )
        return report

    text = spec_path.read_text(encoding="utf-8")
    core_spec = parse_spec(text, CORE_SECTIONS)
    plane_spec = parse_spec(text, PLANE_SECTIONS)
    report.notes.append(
        f"§2.1-2.3 declares {len(core_spec)} constants; §2.4 declares {len(plane_spec)}."
    )

    report.violations += check_uniqueness(core_spec, plane_spec)
    report.violations += check_module_matches_spec(
        core_spec, parse_module(CORE_MODULE), "adopt_const"
    )

    if plane_module_path.exists():
        plane_module = parse_module(plane_module_path)
        report.violations += check_module_matches_spec(plane_spec, plane_module, "plane_const")
        shared_modules = set(parse_module(CORE_MODULE)) & set(plane_module)
        report.violations += [
            f"{name} exists in both constants modules." for name in sorted(shared_modules)
        ]
    else:
        report.notes.append(
            f"plane_const not present at {plane_module_path}; skipped its module comparison. "
            "Cross-module uniqueness was still enforced against the §2 tables."
        )

    core_values = {d.value for d in core_spec}
    scan_roots = [
        REPO_ROOT / "packages",
        REPO_ROOT / "scripts",
        REPO_ROOT / "tools",
        REPO_ROOT / "bench",
    ]
    literal_violations, waivers = check_inlined_literals(core_values, scan_roots)
    report.violations += literal_violations
    report.waivers += waivers

    if scan_prose:
        report.violations += check_prose(core_values, scan_roots)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit non-zero on any violation")
    parser.add_argument(
        "--spec",
        type=Path,
        default=Path(os.environ.get("ADOPT_IMPL_SPEC_PATH", DEFAULT_SPEC_PATH)),
    )
    parser.add_argument("--plane-module", type=Path, default=DEFAULT_PLANE_MODULE)
    parser.add_argument("--no-prose", action="store_true", help="skip the prose duplication scan")
    args = parser.parse_args(argv)

    report = run(args.spec, args.plane_module, scan_prose=not args.no_prose)

    for note in report.notes:
        print(f"note: {note}")
    for waiver in report.waivers:
        print(f"waived: {waiver}")
    for violation in report.violations:
        print(f"VIOLATION: {violation}")

    if report.ok:
        print("constants-sync: OK")
        return 0
    print(f"constants-sync: {len(report.violations)} violation(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
