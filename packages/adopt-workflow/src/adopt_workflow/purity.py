"""`WORKFLOW_BODY_IMPURE`: a `@workflow` body is deterministic.

A workflow body is **replayed**. On resume the engine re-executes the body and
expects it to make the same decisions it made the first time. A clock reading, a
random draw, a network call or a model call inside the body makes the replay
diverge from the original run -- and the failure does not appear in testing,
because testing rarely crashes a workflow midway. It appears in production, once,
as a workflow that takes a different branch after a restart.

Everything non-deterministic belongs in a `@step`, whose result is persisted and
replayed rather than recomputed.

**Why the rules live here and not in the lint contract.** Implementation spec
§4.14 requires purity "checked at import **and** by the lint contract" -- two
enforcement points. `tools/contracts/purity.py` imports the tables below rather
than restating them, because two copies of a banned-call list drift, and the
copy that drifts is the one nobody is looking at when a new `datetime` alias is
added. This module is the declaration; the contract is one consumer and
`@workflow` is the other.

The check is AST-based rather than textual so that `datetime.now()` is caught
whether it is written as `datetime.now()`, `dt.now()` or `mod.datetime.now()`.
"""

import ast
import inspect
import textwrap
from collections.abc import Callable
from typing import Any, Final

from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "BANNED_ATTRIBUTE_ROOTS",
    "BANNED_CALLS",
    "BANNED_NAMES",
    "WORKFLOW_DECORATORS",
    "assert_pure",
    "find_impure_workflow_bodies",
    "impurities_in_source",
]

#: Decorators that mark a workflow body. Both the bare and the qualified form.
WORKFLOW_DECORATORS: Final[frozenset[str]] = frozenset(
    {"workflow", "adopt_workflow.workflow", "api.workflow", "decorators.workflow"}
)

#: Attribute chains that are never deterministic on replay.
BANNED_CALLS: Final[dict[str, str]] = {
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
    "socket.create_connection": "a network call",
    "subprocess.run": "a subprocess call",
    "subprocess.Popen": "a subprocess call",
    "AgentRunner.run": "a model call",
    "new_id": "a random draw",
}

#: Bare names that are equally non-deterministic wherever they come from.
BANNED_NAMES: Final[dict[str, str]] = {
    "open": "file I/O",
    "input": "console I/O",
    "urandom": "a random draw",
    "uuid4": "a random draw",
    "now": "a clock reading",
    "utcnow": "a clock reading",
}

#: Modules whose mere use inside a body means I/O.
BANNED_ATTRIBUTE_ROOTS: Final[frozenset[str]] = frozenset({"os.environ"})


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
            if dotted in BANNED_ATTRIBUTE_ROOTS:
                found.append((node.lineno, f"{dotted} is an environment read"))
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        matched = next(
            (reason for chain, reason in BANNED_CALLS.items() if _matches(dotted, chain)),
            None,
        )
        if matched is not None:
            found.append((node.lineno, f"{dotted}() is {matched}"))
        elif isinstance(node.func, ast.Name) and node.func.id in BANNED_NAMES:
            found.append((node.lineno, f"{node.func.id}() is {BANNED_NAMES[node.func.id]}"))
    return found


def impurities_in_source(source: str, *, filename: str = "<workflow>") -> list[tuple[int, str]]:
    """Every impurity in `source`, treated as a bare function body.

    Used by `assert_pure`, where the source is one decorated function rather
    than a module.
    """
    tree = ast.parse(textwrap.dedent(source), filename=filename)
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for statement in node.body:
                results.extend(_impurities(statement))
    return results


def find_impure_workflow_bodies(source: str, filename: str) -> list[tuple[int, str]]:
    """Return ``(line, reason)`` for every impurity in a `@workflow` body.

    Module-level: only functions carrying a workflow decorator are inspected,
    which is what the lint contract needs when it walks the whole tree.
    """
    tree = ast.parse(source, filename=filename)
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = {_decorator_name(d) for d in node.decorator_list}
        if not (names & WORKFLOW_DECORATORS):
            continue
        for statement in node.body:
            results.extend(_impurities(statement))
    return results


def assert_pure(fn: Callable[..., Any]) -> None:
    """Raise `WORKFLOW_BODY_IMPURE` if `fn`'s body is not replay-safe.

    Called by `@workflow` at decoration time, which is import time -- so a module
    declaring an impure workflow **cannot be imported**, and the failure lands on
    whoever wrote it rather than on whoever resumes it six months later.

    A function whose source cannot be recovered -- defined in a REPL, or built by
    `exec` -- is **not** silently accepted: it is refused with the same code. A
    workflow body nobody can read is a body nobody can prove pure, and the lint
    contract cannot see it either.
    """
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError) as exc:
        raise AdoptError(
            ErrorCode.WORKFLOW_BODY_IMPURE,
            message=f"the source of workflow body {fn.__name__!r} could not be read, so its "
            f"purity cannot be checked",
            hint=(
                "Declare workflow bodies in a module on disk. A body built at runtime "
                "is invisible to both the import-time check and the lint contract."
            ),
        ) from exc

    impurities = impurities_in_source(source, filename=getattr(fn, "__module__", "<workflow>"))
    if not impurities:
        return
    detail = "; ".join(f"line {line}: {reason}" for line, reason in impurities)
    raise AdoptError(
        ErrorCode.WORKFLOW_BODY_IMPURE,
        message=f"workflow body {fn.__name__!r} is not deterministic -- {detail}",
        hint=(
            "Move the non-deterministic work into a @step. A workflow body is "
            "replayed on resume; anything that reads a clock, draws randomness or "
            "performs I/O takes a different branch the second time, and the "
            "divergence shows up in production rather than in tests."
        ),
    )
