"""`no-destructive-sql`: the store package contains no statement that loses data.

Implementation spec §4.7 invariant: *no destructive statement (`DROP`,
`ALTER … DROP`, unpredicated `DELETE`) exists anywhere in the package.* The rule
is absolute rather than situational because the store has **no down-migration and
never will** — recovery from a bad deploy is older code opening a newer store
(§7.4), and that only works while every store is in a state some version of the
binary produced. A `DROP` in shipped code deletes that guarantee for whoever runs
it, and no review catches the one that arrives inside a bug fix at 5pm.

**Only string literals are scanned, and docstrings are excluded.** A regex over
raw source flags the sentence you are reading, and a gate that fires on its own
documentation is a gate somebody switches off — which is worse than not having
it (CR-24 makes the same argument for `no-foreign-tables`). Parsing with `ast`
and looking only at non-docstring string constants means the finding is always a
statement the program could actually execute.

Usage:
    python scripts/no_destructive_sql.py            # scan packages/
    python scripts/no_destructive_sql.py --self-test  # prove it still rejects
"""

import argparse
import ast
import re
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Final, NamedTuple

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_SCAN: Final[tuple[str, ...]] = ("packages",)

#: Each rule is (name, pattern, why it destroys). Whitespace is collapsed before
#: matching, so a statement broken across lines is caught like any other.
_RULES: Final[tuple[tuple[str, re.Pattern[str], str], ...]] = (
    (
        "drop-object",
        re.compile(r"\bDROP\s+(TABLE|INDEX|VIEW|SCHEMA|DATABASE|TRIGGER)\b", re.I),
        "removes a schema object; removal is `retired_in_version` in the manifest, "
        "which keeps the object and writes it NULL",
    ),
    (
        "alter-drop",
        re.compile(r"\bALTER\s+TABLE\b.*\bDROP\b", re.I),
        "removes a column; the additive-only rule has no exception for this",
    ),
    (
        "truncate",
        re.compile(r"\bTRUNCATE\s+TABLE\b|\bTRUNCATE\s+[a-z_]+", re.I),
        "empties a table with no predicate and no audit trail",
    ),
    (
        "unpredicated-delete",
        re.compile(r"\bDELETE\s+FROM\s+[a-z_.\"]+\s*(?:;|$)", re.I),
        "deletes every row; revision families retire by appending a terminal-status "
        "revision and never by deleting",
    ),
)

_MARKER: Final[str] = "# no-destructive-sql: ok --"


#: How much of a matched statement to quote back in a finding. Long enough to
#: recognise, short enough not to fill a CI log with generated DDL.
# const-sync: ok -- a display width, not AGENT_DEFAULT_MAX_WALL_SECONDS.
_QUOTE_CHARS: Final[int] = 120


class Finding(NamedTuple):
    path: Path
    line: int
    rule: str
    reason: str
    statement: str

    def render(self, root: Path) -> str:
        where = self.path.relative_to(root) if self.path.is_relative_to(root) else self.path
        return f"{where}:{self.line}: {self.rule} -- {self.reason}\n    {self.statement}"


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """`id()` of every string constant that is a docstring, so prose is skipped."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                constant = body[0].value
                if isinstance(constant.value, str):
                    found.add(id(constant))
    return found


def _scan_source(path: Path, waived_lines: frozenset[int]) -> Iterator[Finding]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings or node.lineno in waived_lines:
            continue
        collapsed = " ".join(node.value.split())
        for rule, pattern, reason in _RULES:
            if pattern.search(collapsed):
                yield Finding(path, node.lineno, rule, reason, collapsed[:_QUOTE_CHARS])


def _scan_sql(path: Path) -> Iterator[Finding]:
    text = path.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("--"):
            continue
        collapsed = " ".join(line.split())
        for rule, pattern, reason in _RULES:
            if pattern.search(collapsed):
                yield Finding(path, number, rule, reason, collapsed[:_QUOTE_CHARS])


def _waived_lines(path: Path) -> frozenset[int]:
    """Lines carrying an inline waiver, and the line after it.

    Every waiver is printed on every run by the caller, so a waiver is a standing
    visible decision rather than a silent escape — the same discipline
    `constants-sync` uses (§7.2).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    waived: set[int] = set()
    for number, line in enumerate(lines, start=1):
        if _MARKER in line:
            waived.update({number, number + 1})
    return frozenset(waived)


def scan(paths: list[Path]) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    waivers: list[str] = []
    for root in paths:
        for path in sorted(root.rglob("*.py")):
            waived = _waived_lines(path)
            if waived:
                waivers.append(f"{path}: {sorted(waived)[0]}")
            findings.extend(_scan_source(path, waived))
        for path in sorted(root.rglob("*.sql")):
            findings.extend(_scan_sql(path))
    return findings, waivers


def _self_test() -> int:
    """Prove the gate still rejects, and still tolerates its own prose."""
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        (root / "planted.py").write_text(
            'def wipe(connection: object) -> None:\n    _ = "DROP TABLE knowledge_item"\n',
            encoding="utf-8",
        )
        (root / "prose.py").write_text(
            '"""This module explains that DROP TABLE is forbidden."""\n\nVALUE = 1\n',
            encoding="utf-8",
        )
        (root / "planted.sql").write_text("DELETE FROM binding;\n", encoding="utf-8")

        findings, _ = scan([root])
        rules = sorted({finding.rule for finding in findings})
        expected = ["drop-object", "unpredicated-delete"]
        if rules != expected:
            print(f"SELF-TEST FAILED: detected {rules}, expected {expected}")
            return 1
        if any(finding.path.name == "prose.py" for finding in findings):
            print("SELF-TEST FAILED: a docstring was reported as a statement")
            return 1
    print(f"self-test passed: {len(findings)} planted violations detected, prose ignored")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--self-test", action="store_true", help="Prove the gate rejects.")
    parser.add_argument(
        "--path",
        action="append",
        default=None,
        help="Directory to scan; repeatable. Defaults to packages/.",
    )
    arguments = parser.parse_args(argv)

    if arguments.self_test:
        return _self_test()

    targets = [Path(p) for p in (arguments.path or DEFAULT_SCAN)]
    roots = [t if t.is_absolute() else REPO_ROOT / t for t in targets]
    missing = [root for root in roots if not root.exists()]
    if missing:
        print(f"no such path: {', '.join(str(m) for m in missing)}")
        return 1

    findings, waivers = scan(roots)
    for waiver in waivers:
        print(f"waived: {waiver}")

    if findings:
        print(f"\n{len(findings)} destructive statement(s) found:\n")
        for finding in findings:
            print(finding.render(REPO_ROOT))
        print(
            "\nThe store has no down-migration and never will. Removal is "
            "`retired_in_version` in schema/canonical.yaml; retirement of a revision "
            "family is a terminal-status revision, never a delete."
        )
        return 1

    scanned = ", ".join(str(r.relative_to(REPO_ROOT)) for r in roots)
    print(f"no destructive SQL in {scanned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
