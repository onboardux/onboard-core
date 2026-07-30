"""The three source-text contracts: foreign tables, revision updates, cache writes.

Each is a regular expression over source text rather than an import-graph rule,
because each forbids a *statement*, not a dependency. Each names the file, the
line and the remedy, because a gate that says only "violation" is a gate people
work around instead of with.
"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from grimp import ImportGraph
from importlinter import Contract, ContractCheck, fields, output

from tools.contracts._scan import Finding, as_str_list, is_under, iter_source_files

#: `CREATE TABLE` in any dialect, including `IF NOT EXISTS`.
_CREATE_TABLE_RE: Final[re.Pattern[str]] = re.compile(
    r"\bCREATE\s+(?:TEMP\s+|TEMPORARY\s+)?TABLE\b", re.IGNORECASE
)

#: `UPDATE <anything>_revision`. The four append-only families all end in
#: `_revision`, so the suffix is the rule rather than a list that goes stale.
_REVISION_UPDATE_RE: Final[re.Pattern[str]] = re.compile(
    r"\bUPDATE\s+[\"'`\[]?([a-z_]+_revision)\b", re.IGNORECASE
)

#: A write against the coverage cache: the column named in an UPDATE/SET or an
#: INSERT column list.
_COVERED_CACHE_RE: Final[re.Pattern[str]] = re.compile(r"\bcovered_cache(?:_at)?\b")
_WRITE_CONTEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(UPDATE|SET|INSERT\s+INTO|UPSERT|REPLACE\s+INTO)\b", re.IGNORECASE
)


def _render(check: ContractCheck, headline: str, remedy: str) -> None:
    output.print_error(headline)
    output.new_line()
    for rendered in check.metadata.get("findings", []):
        output.print_error(f"  {rendered}", bold=False)
    output.new_line()
    output.print_error(remedy, bold=False)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Object ids of every docstring expression in the tree."""
    found: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def scannable_text(path: Path) -> Iterator[tuple[int, str]]:
    """Yield ``(line, text)`` for text that could actually execute as SQL.

    For ``.sql`` files that is every line. For ``.py`` files it is **string
    literals that are not docstrings** -- SQL reaching a database from Python
    must be in a string, while prose describing SQL lives in docstrings and
    comments. Scanning raw Python lines instead makes the contract fail on its
    own documentation, which is how a gate acquires an exemption it never
    needed.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".py":
        yield from enumerate(text.splitlines(), 1)
        return
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:  # pragma: no cover -- ruff catches these first
        return
    docstrings = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.lineno, node.value


class NoForeignTablesContract(Contract):
    """`CREATE TABLE` exists only in generated migrations.

    A table created anywhere else is a table the canonical manifest does not
    know about, which means it is absent from the export, from the JSON schema
    and from the generated models -- and the promise that no later build item
    writes a migration is quietly already broken.
    """

    paths = fields.ListField(subfield=fields.StringField())
    allowed_paths = fields.ListField(subfield=fields.StringField())

    def check(self, graph: ImportGraph, verbose: bool) -> ContractCheck:
        findings: list[Finding] = []
        for path in iter_source_files(as_str_list(self.paths)):
            if is_under(path, as_str_list(self.allowed_paths)):
                continue
            for lineno, text in scannable_text(path):
                if _CREATE_TABLE_RE.search(text):
                    findings.append(Finding(path, lineno, text))
        output.verbose_print(verbose, f"table-creating statements: {len(findings)} finding(s)")
        return ContractCheck(
            kept=not findings, metadata={"findings": [f.render() for f in findings]}
        )

    def render_broken_contract(self, check: ContractCheck) -> None:
        _render(
            check,
            "A table-creating statement was found outside the generated migrations.",
            "Declare the table in schema/canonical.yaml and regenerate. A table created "
            "in application code is invisible to the export, the JSON schema and the "
            "generated models.",
        )


class NoRevisionUpdateContract(Contract):
    """No `UPDATE` targets any `*_revision` table, in either repository.

    The four revision families are append-only. A revision chain is the audit
    record: the moment a row is updated in place, "what did it say then" becomes
    permanently unanswerable, and no later repair can recover the answer.
    """

    paths = fields.ListField(subfield=fields.StringField())
    allowed_paths = fields.ListField(subfield=fields.StringField(), required=False)

    def check(self, graph: ImportGraph, verbose: bool) -> ContractCheck:
        allowed = as_str_list(self.allowed_paths)
        findings: list[Finding] = []
        for path in iter_source_files(as_str_list(self.paths)):
            if is_under(path, allowed):
                continue
            for lineno, text in scannable_text(path):
                match = _REVISION_UPDATE_RE.search(text)
                if match:
                    findings.append(Finding(path, lineno, f"targets {match.group(1)}: {text}"))
        output.verbose_print(verbose, f"revision mutations: {len(findings)} finding(s)")
        return ContractCheck(
            kept=not findings, metadata={"findings": [f.render() for f in findings]}
        )

    def render_broken_contract(self, check: ContractCheck) -> None:
        _render(
            check,
            "A mutation targets an append-only *_revision table.",
            "Append a new revision through `append_revision` instead. Retirement is a "
            "terminal-status revision, never an update and never a delete.",
        )


class NoCoveredCacheWriteContract(Contract):
    """Only `adopt_coverage` writes `covered_cache` / `covered_cache_at`.

    The cache is non-authoritative and the recompute function is the truth. A
    writer elsewhere makes the cache authoritative again by accident, which
    reintroduces exactly the silent coverage decay the rebuild removed -- and
    the disagreement alarm can no longer tell a stale cache from a second
    writer.
    """

    paths = fields.ListField(subfield=fields.StringField())
    allowed_paths = fields.ListField(subfield=fields.StringField())

    def check(self, graph: ImportGraph, verbose: bool) -> ContractCheck:
        findings: list[Finding] = []
        for path in iter_source_files(as_str_list(self.paths)):
            if is_under(path, as_str_list(self.allowed_paths)):
                continue
            for lineno, text in scannable_text(path):
                if _COVERED_CACHE_RE.search(text) and _WRITE_CONTEXT_RE.search(text):
                    findings.append(Finding(path, lineno, text))
        output.verbose_print(verbose, f"coverage-cache writes: {len(findings)} finding(s)")
        return ContractCheck(
            kept=not findings, metadata={"findings": [f.render() for f in findings]}
        )

    def render_broken_contract(self, check: ContractCheck) -> None:
        _render(
            check,
            "A coverage-cache write was found outside adopt_coverage.",
            "`covered_cache` is rebuilt only from `recompute_coverage()`. If the cache "
            "looks wrong, the recompute result is the truth and the disagreement must "
            "alarm -- never write the cache to make the symptom go away.",
        )


def scan_paths_for_tests(contract_cls: type[Contract], **options: object) -> list[str]:
    """Instantiate a contract with options and return its rendered findings.

    Exists so the negative tests can drive each contract directly instead of
    shelling out to `lint-imports` and parsing its output.
    """
    contract = contract_cls(name="test", session_options={}, contract_options=dict(options))
    check = contract.check(ImportGraph(), verbose=False)
    return [str(f) for f in check.metadata.get("findings", [])]
