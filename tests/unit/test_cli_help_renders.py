"""Every `--help` page renders, and the walk that proves it cannot go stale.

*Fails when* any command's help text carries markup `rich` refuses to parse --
the observed case being a literal `[/environment]` in an option's `help=`, which
rich reads as a **closing markup tag** with no opening one. *Matters because*
help is the only documentation an installed user has: the page exits `1` with a
traceback on stderr and nothing on stdout, which is the shape contracts §13
exists to prevent, and it took down all seven `adopt handover` verbs at once
because they share one `ScopeOption`. `adopt handover verify --checklist` is the
sharpest case -- the YAML shape it requires is documented in the page that
crashed, and in a `docs/` path a `pip install adopt-cli` user does not have.
*No other instrument catches it because* nothing in the suite or in CI renders a
help page: `scripts/packaged_artifact.py` drives ten verbs from an installed
wheel and never once passes `--help`, and every CLI test asserts a payload or an
envelope, which a help page is neither.

**The enumeration is a walk, not a list.** A hand-written list of command names
is a list that stops covering the CLI the day a verb is added -- the same failure
`REQUIRED_JOBS` was written to end for journey jobs. `_help_pages()` asks the
typer app what commands exist, so a new verb is covered by existing code.

**Rendering happens in process.** 54 subprocesses would cost more than the whole
`unit` budget for a test whose subject is a string in a decorator; `main()`
returns the exit code directly, and the release entry point is already pinned by
`tests/unit/test_cli_exit_codes.py`.

**Groups are pages too.** `adopt handover --help` renders its subcommand table
and worked throughout; the seven pages *below* it did not. Walking only leaves
would have reported this CLI healthy.
"""

import contextlib
import io
from collections.abc import Iterator

import pytest
import typer

from adopt_cli.main import app, main

__all__: list[str] = []


def _subcommands(node: object) -> dict[str, object]:
    """One node's children, asked for without naming typer's vendored click.

    `typer.main.get_command` returns a command object from the click *typer
    vendors* under `typer._click`, so `isinstance(node, click.Group)` against the
    standalone click this repository also imports is `False` for every group --
    a walk written that way finds exactly one page and passes. Reading the
    attribute is the same reasoning `main._typer_exception_base` uses for
    exception classes: ask the object, do not name the private module.
    """
    commands = getattr(node, "commands", None)
    return dict(commands) if isinstance(commands, dict) else {}


def _walk(node: object, prefix: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    for name, sub in sorted(_subcommands(node).items()):
        yield (*prefix, name)
        yield from _walk(sub, (*prefix, name))


def _help_pages() -> list[tuple[str, ...]]:
    """Every argv prefix that has a help page, the root included."""
    root = typer.main.get_command(app)
    return [(), *_walk(root)]


def _render(argv: tuple[str, ...]) -> tuple[int, str]:
    """Render one help page, returning its exit code and what it printed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main([*argv, "--help"])
    return code, out.getvalue()


@pytest.mark.unit
@pytest.mark.parametrize("page", _help_pages(), ids=lambda page: " ".join(page) or "adopt")
def test_every_help_page_renders(page: tuple[str, ...]) -> None:
    """`adopt <command> --help` exits 0 and prints a usage block.

    Parametrized per page so a failure names the command rather than reporting
    that "the CLI" is broken -- with one shared option able to take seven verbs
    down together, which pages failed is the whole diagnosis.
    """
    code, printed = _render(page)
    assert code == 0, f"adopt {' '.join(page)} --help exited {code}"
    assert printed.strip(), f"adopt {' '.join(page)} --help printed nothing"
    assert "Usage:" in printed, f"adopt {' '.join(page)} --help printed no usage block"


@pytest.mark.unit
def test_the_walk_finds_every_group_and_its_children() -> None:
    """The enumeration reaches nested subcommands, not just top-level verbs.

    The control for `_subcommands` returning `{}` for every group: that bug makes
    `test_every_help_page_renders` pass with one page, which is indistinguishable
    from a healthy CLI -- CR-67's `0/0 covered (100%)` in a new costume. Naming
    two verbs that must be reachable is enough to fail it, and neither is at the
    top level.
    """
    pages = {" ".join(page) for page in _help_pages()}
    assert "handover verify" in pages
    assert "probe manifest validate" in pages
    assert len(pages) > 40, f"the walk found only {len(pages)} pages"


@pytest.mark.unit
def test_rich_hostile_markup_in_a_help_string_is_caught() -> None:
    """The planted violation: the instrument fails when the defect is present.

    A gate nobody has seen fail is a gate nobody should trust, and this one is a
    string assertion whose passing state and blind state look alike. Planting
    into a *throwaway* app rather than into `adopt_cli` keeps the plant off the
    real command tree -- `scripts/plant_violation.py`'s kinds edit tracked files
    and must be reverted byte-exactly, which is the wrong shape for a defect that
    lives in a decorator argument.
    """
    planted = typer.Typer()

    @planted.command()
    def demo(  # pragma: no cover -- never invoked; only its help is rendered
        scope: str = typer.Option("", "--scope", help="firm/engagement/system[/environment]."),
    ) -> None:
        """A command whose option help carries an unmatched closing tag."""

    rendered = typer.main.get_command(planted)
    out = io.StringIO()
    with (
        pytest.raises(Exception) as raised,
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        rendered.main(args=["--help"], standalone_mode=False)
    assert "environment" in str(raised.value), (
        "the plant raised something other than the markup error it exists to reproduce: "
        f"{raised.value!r}"
    )
