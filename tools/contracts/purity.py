"""`workflow-body-purity`: a `@workflow` body is deterministic.

A workflow body is **replayed**. On resume the engine re-executes the body and
expects it to make the same decisions it made the first time. A clock reading, a
random draw, a network call or a model call inside the body makes the replay
diverge from the original run -- and the failure does not appear in testing,
because testing rarely crashes a workflow midway. It appears in production, once,
as a workflow that takes a different branch after a restart.

Everything non-deterministic belongs in a `@step`, whose result is persisted and
replayed rather than recomputed.

The check is AST-based rather than textual so that `datetime.now()` is caught
whether it is written as `datetime.now()`, `dt.now()` or `from datetime import
now`.
"""

import ast
from typing import Final

from grimp import ImportGraph
from importlinter import Contract, ContractCheck, fields, output

from tools.contracts._scan import Finding, as_str_list, iter_source_files

#: Decorators that mark a workflow body. Both the bare and the qualified form.
_WORKFLOW_DECORATORS: Final[frozenset[str]] = frozenset(
    {"workflow", "adopt_workflow.workflow", "api.workflow"}
)

#: Attribute chains that are never deterministic on replay.
_BANNED_CALLS: Final[dict[str, str]] = {
    "datetime.now": "a clock reading",
    "datetime.utcnow": "a clock reading",
    "datetime.today": "a clock reading",
    "time.time": "a clock reading",
    "time.monotonic": "a clock reading",
    "time.sleep": "a sleep",
    "random.random": "a random draw",
    "random.randint": "a random draw",
    "random.choice": "a random draw",
    "random.shuffle": "a random draw",
    "uuid.uuid1": "a random draw",
    "uuid.uuid4": "a random draw",
    "os.urandom": "a random draw",
    "os.getenv": "an environment read",
    "secrets.token_hex": "a random draw",
    "secrets.token_bytes": "a random draw",
    "requests.get": "a network call",
    "requests.post": "a network call",
    "httpx.get": "a network call",
    "httpx.post": "a network call",
    "socket.socket": "a network call",
    "subprocess.run": "a subprocess call",
    "subprocess.Popen": "a subprocess call",
    "AgentRunner.run": "a model call",
}

#: Bare names that are equally non-deterministic wherever they come from.
_BANNED_NAMES: Final[dict[str, str]] = {
    "open": "file I/O",
    "input": "console I/O",
    "urandom": "a random draw",
    "uuid4": "a random draw",
    "now": "a clock reading",
    "utcnow": "a clock reading",
}

#: Modules whose mere use inside a body means I/O.
_BANNED_ATTRIBUTE_ROOTS: Final[frozenset[str]] = frozenset({"os.environ"})


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_decorator_name(node.value)}.{node.attr}"
    return ""


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _matches(dotted: str, chain: str) -> bool:
    """True when a dotted call matches a banned chain, however it was imported.

    Matching on the tail means ``datetime.now()``, ``dt.datetime.now()`` and
    ``mod.time.time()`` are all caught without enumerating every alias a file
    might use.
    """
    return dotted == chain or dotted.endswith("." + chain)


def _impurities(body: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(body):
        if isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted in _BANNED_ATTRIBUTE_ROOTS:
                found.append((node.lineno, f"{dotted} is an environment read"))
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        matched = next(
            (reason for chain, reason in _BANNED_CALLS.items() if _matches(dotted, chain)),
            None,
        )
        if matched is not None:
            found.append((node.lineno, f"{dotted}() is {matched}"))
        elif isinstance(node.func, ast.Name) and node.func.id in _BANNED_NAMES:
            found.append((node.lineno, f"{node.func.id}() is {_BANNED_NAMES[node.func.id]}"))
    return found


def find_impure_workflow_bodies(source: str, filename: str) -> list[tuple[int, str]]:
    """Return ``(line, reason)`` for every impurity in a `@workflow` body."""
    tree = ast.parse(source, filename=filename)
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = {_decorator_name(d) for d in node.decorator_list}
        if not (names & _WORKFLOW_DECORATORS):
            continue
        for statement in node.body:
            results.extend(_impurities(statement))
    return results


class WorkflowBodyPurityContract(Contract):
    """No clock, randomness, network, model call or I/O in a `@workflow` body."""

    paths = fields.ListField(subfield=fields.StringField())

    def check(self, graph: ImportGraph, verbose: bool) -> ContractCheck:
        findings: list[Finding] = []
        for path in iter_source_files(as_str_list(self.paths)):
            if path.suffix != ".py":
                continue
            source = path.read_text(encoding="utf-8")
            if "workflow" not in source:
                continue
            try:
                impurities = find_impure_workflow_bodies(source, str(path))
            except SyntaxError:  # pragma: no cover -- ruff catches these first
                continue
            findings += [Finding(path, line, reason) for line, reason in impurities]
        output.verbose_print(verbose, f"checked workflow bodies; {len(findings)} finding(s)")
        return ContractCheck(
            kept=not findings, metadata={"findings": [f.render() for f in findings]}
        )

    def render_broken_contract(self, check: ContractCheck) -> None:
        output.print_error("WORKFLOW_BODY_IMPURE: a @workflow body is not deterministic.")
        output.new_line()
        for rendered in check.metadata.get("findings", []):
            output.print_error(f"  {rendered}", bold=False)
        output.new_line()
        output.print_error(
            "Move the non-deterministic work into a @step. A workflow body is replayed "
            "on resume; anything that reads a clock, draws randomness or performs I/O "
            "will take a different branch the second time, and the divergence shows up "
            "in production rather than in tests.",
            bold=False,
        )
