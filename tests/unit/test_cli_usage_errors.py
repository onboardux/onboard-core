"""A usage error reaches the caller as contracts §13 says, and cannot stop doing so.

*Fails when* a refusal or a parser-level usage error escapes `adopt_cli.main`
unhandled -- because the installed typer vendors its own click under
`typer._click`, so `typer.BadParameter` is **not** a `click.ClickException` and
never reached `main._exit_code_of`'s usage branch. *Matters because* the escape
is silent in the worst way: exit `1` where §13 says `2` or `3`, a rich traceback
on stderr, and nothing at all on the stdout a `--json` caller was promised, so
every integrator's script reads "it broke" for "you asked for something I cannot
do". *No other instrument catches it because* every existing CLI test asserts a
successful payload or an `AdoptError` envelope, and this class produces neither
-- `grep -rn BadParameter tests/ tools/ scripts/` returned nothing at all before
this file existed.

**The class has already returned once.** Two `typer.BadParameter` sites in
`commands/pack.py` were diagnosed, and a third was added by a later build to the
same file while `commands/knowledge.py` -- one directory away -- carried a
docstring explaining precisely why it must not be done. That is what
`test_no_command_module_refuses_with_a_parser_exception` exists to prevent, and
`scripts/plant_violation.py --kind bad-parameter` is how it is watched failing.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import click
import pytest
import typer

from adopt_cli.main import _USAGE_ERROR_CLASSES, _exit_code_of, main
from adopt_obs import ExitCode

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINT = REPO_ROOT / "packages" / "adopt-cli" / "src" / "adopt_cli" / "main.py"
CLI_SOURCE = REPO_ROOT / "packages" / "adopt-cli" / "src"

#: Every click exception whose meaning is "the caller asked for something I
#: cannot accept". Raising one of these from a command body is the defect: it is
#: a refusal wearing the parser's clothes, and which copy of click it comes from
#: decides whether anything catches it. `Exit` and `Abort` are deliberately
#: absent -- they are control flow, they carry no message, and they work.
FORBIDDEN_REFUSALS = frozenset(
    {
        "BadArgumentUsage",
        "BadOptionUsage",
        "BadParameter",
        "ClickException",
        "FileError",
        "MissingParameter",
        "NoSuchOption",
        "UsageError",
    }
)


def _dotted(node: ast.expr) -> str:
    """`typer.BadParameter` from the AST of a raise, as text."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def raised_refusals(source: Path) -> list[str]:
    """Every `raise <a click usage exception>` in one module, as `file:line: name`."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        name = _dotted(raised)
        if name.rsplit(".", 1)[-1] in FORBIDDEN_REFUSALS:
            found.append(f"{source.relative_to(REPO_ROOT)}:{node.lineno}: {name}")
    return found


@pytest.mark.unit
def test_no_command_module_refuses_with_a_parser_exception() -> None:
    """The recurrence gate. Watched failing by `--kind bad-parameter`.

    The scan is over the AST rather than over lines, so the docstrings in
    `commands/knowledge.py` and in this file that *name* `typer.BadParameter` in
    order to explain the rule do not fail the rule -- `no-revision-update`'s
    `scannable_text` makes the same distinction for the same reason. Only an
    actual `raise` counts.
    """
    offenders = [
        finding
        for module in sorted(CLI_SOURCE.rglob("*.py"))
        for finding in raised_refusals(module)
    ]

    assert not offenders, (
        "a command refuses with a click/typer parser exception:\n  "
        + "\n  ".join(offenders)
        + "\nRaise an `AdoptError` with a registered code instead. The installed typer "
        "vendors its own click, so a `typer.BadParameter` is not a `click.ClickException`: "
        "it escapes `main()` as exit 1 with a traceback and no envelope."
    )


@pytest.mark.unit
def test_the_gate_would_see_a_planted_refusal_in_a_submodule() -> None:
    """The gate's own control: a scan that finds nothing must be able to find something.

    Planted in memory against a **submodule-shaped** path, which is CR-67's
    lesson: `escape_coverage`'s self-test planted at a package root and would
    have passed against a discovery bug that could not see submodules at all. A
    gate whose broken state is indistinguishable from its passing state is not a
    gate, and `--kind bad-parameter` plants into `commands/pack.py` for the same
    reason.
    """
    planted = CLI_SOURCE / "adopt_cli" / "commands" / "pack.py"
    assert planted.exists(), "the plant's victim moved; update the gate and the plant together"

    tree = ast.parse('raise typer.BadParameter("planted")')
    node = next(item for item in ast.walk(tree) if isinstance(item, ast.Raise))
    assert node.exc is not None
    raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc

    assert _dotted(raised).rsplit(".", 1)[-1] in FORBIDDEN_REFUSALS


@pytest.mark.unit
def test_typers_usage_error_is_recognised_whichever_click_it_comes_from() -> None:
    """The resolution in `main`, pinned so its fallback cannot become the norm.

    `_typer_exception_base` walks `typer.BadParameter.__mro__` rather than
    importing `typer._click.exceptions`, and falls back to the standalone click
    class if the hierarchy ever changes shape. That fallback is correct and
    silent -- which is exactly why it needs an assertion: without this, a typer
    upgrade could restore the original defect and every other test would still
    pass.
    """
    assert isinstance(typer.BadParameter("x"), _USAGE_ERROR_CLASSES)
    assert isinstance(click.UsageError("x"), _USAGE_ERROR_CLASSES)


@pytest.mark.unit
def test_a_typer_usage_error_maps_to_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    """§13's mapping, at the one function that owns it."""
    code = _exit_code_of(typer.BadParameter("no such thing"))

    assert code == ExitCode.USAGE_ERROR
    assert "no such thing" in capsys.readouterr().err


@pytest.mark.unit
def test_a_missing_parameter_exits_two_through_main(capsys: pytest.CaptureFixture[str]) -> None:
    """The same thing through the entry point, raised by the parser rather than by hand."""
    code = main(["identity", "build", "--json"])

    captured = capsys.readouterr()
    assert code == ExitCode.USAGE_ERROR
    assert captured.out == ""
    assert "--scope" in captured.err


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRY_POINT), *argv],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def initialised_store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real store with one four-level scope, built through the CLI.

    `--archetype` is passed so detection never runs: an empty temporary
    directory detects nothing, and this fixture is about `pack`'s refusals, not
    about detection.
    """
    root = tmp_path_factory.mktemp("usage-errors")
    answers = root / "answers.json"
    answers.write_text(
        json.dumps({"artifact_access": True, "deploy_signal": True, "safe_interaction": True}),
        encoding="utf-8",
    )
    store = root / "adopt.db"
    done = _run(
        "init",
        str(root),
        "--scope",
        "northwind/acme-erp/orders-api/prod",
        "--answers",
        str(answers),
        "--store",
        str(store),
        "--archetype",
        "web",
        "--json",
    )
    assert done.returncode == ExitCode.SUCCESS, done.stderr
    return store


@pytest.mark.unit
@pytest.mark.parametrize(
    ("argv", "expected_code", "expected_in_stderr"),
    [
        pytest.param(
            (
                "pack",
                "--scope",
                "northwind/acme-erp",
                "--json",
                "--store",
                "{store}",
                "--out",
                "{out}",
            ),
            ExitCode.POLICY_REFUSAL,
            "SCOPE_VIOLATION",
            id="a-pack-with-no-system-in-scope-is-a-policy-refusal",
        ),
        pytest.param(
            ("pack", "--sections", ",", "--json", "--store", "{store}", "--out", "{out}"),
            ExitCode.USAGE_ERROR,
            "PACK_SECTIONS_EMPTY",
            id="an-empty-section-selection-is-a-usage-error",
        ),
        pytest.param(
            ("pack", "--no-such-flag", "--json", "--store", "{store}", "--out", "{out}"),
            ExitCode.USAGE_ERROR,
            "No such option",
            id="an-unknown-option-is-a-usage-error",
        ),
        pytest.param(
            ("identity", "build", "--json"),
            ExitCode.USAGE_ERROR,
            "Missing option",
            id="a-missing-parameter-is-a-usage-error",
        ),
    ],
)
def test_the_four_reproductions_honour_the_error_contract(
    initialised_store: Path,
    tmp_path: Path,
    argv: tuple[str, ...],
    expected_code: int,
    expected_in_stderr: str,
) -> None:
    """R8/N14's four commands, run as a real process.

    Every one of them exited `1` with an empty stdout and a rich traceback
    before T1.1. The traceback assertion is the one that would have caught it:
    the exit code alone reads as an ordinary failure, and `--json`'s empty stdout
    is what a refusal is *supposed* to look like.
    """
    resolved = [item.format(store=initialised_store, out=tmp_path / "pack") for item in argv]

    done = _run(*resolved)

    assert done.returncode == expected_code, done.stderr
    assert done.stdout == "", done.stdout
    assert expected_in_stderr in done.stderr
    assert "Traceback" not in done.stderr
    # The control for the assertion above: a stderr that is empty would satisfy
    # "Traceback is absent" perfectly, and say nothing at all (Build 9's rule).
    assert done.stderr.strip() != ""
