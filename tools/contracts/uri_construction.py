"""`uri-construction`: `build_uri()` is the only place a URI is assembled.

Contracts §1.2 forbids, in Build 1 code: *"constructing a URI by concatenation,
f-string, `str.join`, `os.path.join` or `urllib.parse.urlunparse`"*. This is the
gate behind PRD F2's acceptance signal -- *"a mint attempt bypassing `build_uri()`
fails CI"* -- and behind `03` §5.3 invariant 1.

**The rule reads the scheme from `adopt_const.URI_SCHEME`; it never restates it
(B1-CR-26).** `05` S1.1 and `04` §6 both originally scanned for a literal
`adopt://`, and Build 0 CR-06 ratified the scheme as `onboard-v1://` five days
after the pack was written -- so both audits were scanning for a string that can
never appear, and code minting a forged **`onboard-v1://`** URI would have passed
as clean. A rule that hard-codes the value it protects goes blind the next time
that value is ratified. This module imports the constant, so the day
`onboard-v2` is ratified the rule follows it with no edit.

**Two things are forbidden, and they are different.**

1. *A scheme-carrying literal outside the builder.* Any string literal containing
   the scheme label, anywhere except `adopt_identity.uri` itself. This is what
   catches a hand-assembled URI whatever mechanism assembled it.
2. *An assembly call over URI parts.* `str.join`, `os.path.join` and
   `urllib.parse.urlunparse` applied to something whose name says it is a URI or
   a scope. This catches the assembly that has not yet been given a scheme --
   the intermediate that becomes a URI one line later.

Why both: rule 1 alone misses `"/".join(segments)` handed to a helper that adds
the scheme, and rule 2 alone misses an f-string. Together they cover every
mechanism §1.2 names.
"""

import ast
import re
from pathlib import Path
from typing import Final

from grimp import ImportGraph
from importlinter import Contract, ContractCheck, fields, output

from adopt_const import URI_SCHEME
from tools.contracts._scan import Finding, as_str_list, is_under, iter_source_files
from tools.contracts.source_rules import scannable_text

__all__ = ["UriConstructionContract"]

#: The assembly functions contracts §1.2 names by name.
_ASSEMBLY_CALLS: Final[frozenset[str]] = frozenset({"join", "urlunparse", "urlunsplit"})

#: An identifier that says its value is a URI or a scope segment. An assembly
#: call over one of these is the case rule 2 exists for. Narrow on purpose: a
#: blanket ban on `join` would flag every path and every comma-separated message
#: in the tree, and a gate that noisy is a gate people switch off.
_URI_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|_)(uri|uris|scheme|segments?|slugs?|scope_path|key_path)(?:$|_)", re.IGNORECASE
)


def _carries_the_scheme(text: str) -> bool:
    """Whether a string literal carries the scheme label.

    Read from the constant, never restated -- see the module docstring.
    """
    return URI_SCHEME in text


class _AssemblyVisitor(ast.NodeVisitor):
    """Find `join` / `urlunparse` calls whose operand names a URI or scope part."""

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else None
        if name is None and isinstance(function, ast.Name):
            name = function.id
        if name in _ASSEMBLY_CALLS:
            rendered = ast.unparse(node)
            if _URI_NAME_RE.search(rendered):
                self.hits.append((node.lineno, rendered))
        self.generic_visit(node)


def _assembly_findings(path: Path) -> list[Finding]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover -- ruff catches these first
        return []
    visitor = _AssemblyVisitor()
    visitor.visit(tree)
    return [Finding(path, line, f"assembles URI parts: {text}") for line, text in visitor.hits]


class UriConstructionContract(Contract):
    """A URI is built by `build_uri()` and by nothing else.

    An identity URI is `UNIQUE` in the store and is written into every exported
    bundle a client keeps. A URI assembled by hand differs from the builder's
    output by one normalization or one escape, and the store then holds the same
    referent twice under two names -- with no way afterwards to tell which of the
    two rows anything meant.
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
                if _carries_the_scheme(text):
                    findings.append(
                        Finding(path, lineno, f"literal carries {URI_SCHEME!r}: {text}")
                    )
            if path.suffix == ".py":
                findings.extend(_assembly_findings(path))
        output.verbose_print(verbose, f"URI construction sites: {len(findings)} finding(s)")
        return ContractCheck(
            kept=not findings, metadata={"findings": [f.render() for f in findings]}
        )

    def render_broken_contract(self, check: ContractCheck) -> None:
        output.print_error("A URI was assembled outside `build_uri()`.")
        output.new_line()
        for rendered in check.metadata.get("findings", []):
            output.print_error(f"  {rendered}", bold=False)
        output.new_line()
        output.print_error(
            "Call `adopt_identity.build_uri(scope, kind, namespace, key)`. It is the only "
            "site that applies NFC, escapes each segment exactly once and refuses "
            "pre-encoded input -- a hand-assembled URI differs from it by one escape and "
            "the store then holds one referent under two names. To name the scheme in "
            "prose, use a docstring: this rule scans string literals, not comments.",
            bold=False,
        )
