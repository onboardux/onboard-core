"""`adopt coverage recompute` finds its system the way an operator names one.

*Fails when* the only way to name a system is the ULID `--system` used to
require. *Matters because* this command is the documented remedy for
`COVERAGE_CACHE_DISAGREEMENT` -- an alarm that fires on every `adopt gaps` and
`adopt pack` once a binding is confirmed, and never self-heals by design -- and
no verb printed a system id: `store info` reports counts, `store doctor` reports
findings, and only `adopt handover start` ever surfaced one, as a side effect of
opening a handover event. An operator was left reading the SQLite file by hand
to silence an alarm the product raised at them, and the Build 2 how-doc's
remedy line, `adopt coverage recompute --rebuild`, exited `2` as written.
*No other instrument catches it because* every existing test passes the id it
just created, which is exactly the thing an operator does not have.

**The three ways in must agree.** A scope path, a slug and an id name one system
between them; a resolver that disagreed with `adopt handover`'s would let two
verbs hold different opinions about which system a store is about, so both go
through the same `resolve_system`.
"""

import json
from pathlib import Path

import pytest

from adopt_cli.main import main
from adopt_obs import ExitCode
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

__all__: list[str] = []

SCOPE_PATH = "northwind/acme-erp/orders-api/prod"


@pytest.fixture
def store_path(s4_store: SqliteStoreHandle, s4_scope: Scope, tmp_path: Path) -> Path:
    """A migrated store carrying exactly one scope chain."""
    s4_store.close()
    return tmp_path / "store.db"


def _recompute(
    store_path: Path, *argv: str, capsys: pytest.CaptureFixture[str]
) -> dict[str, object]:
    code = main(["coverage", "recompute", "--store", str(store_path), "--json", *argv])
    assert code == ExitCode.SUCCESS, f"exit {code}"
    payload: dict[str, object] = json.loads(capsys.readouterr().out)
    return payload


@pytest.mark.unit
def test_a_scope_path_names_the_system(
    store_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The string `adopt init` already took is accepted here."""
    payload = _recompute(store_path, "--scope", SCOPE_PATH, capsys=capsys)
    assert payload["covered"] == 0
    assert payload["uncovered"] == 0


@pytest.mark.unit
def test_a_slug_names_the_system(store_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--system` takes the slug an operator can read off `adopt init`'s output."""
    payload = _recompute(store_path, "--system", "orders-api", capsys=capsys)
    assert payload["covered"] == 0


@pytest.mark.unit
def test_neither_resolves_the_store_s_own_scope(
    store_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One environment in the store means no flag is needed at all.

    This is what makes the how-doc's remedy line -- `adopt coverage recompute
    --rebuild`, with no target -- true as written, rather than a command that
    exits `2` for a missing required option.
    """
    payload = _recompute(store_path, capsys=capsys)
    assert payload["covered"] == 0


@pytest.mark.unit
def test_both_at_once_is_refused_rather_than_ordered(
    store_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--system` and `--scope` name the same thing, so disagreeing is not resolvable.

    The negative control for the two tests above: they would both pass against a
    resolver that quietly preferred one flag, and a store whose coverage was
    recomputed for the system the operator did *not* mean is a wrong answer that
    reads exactly like a right one.
    """
    code = main(
        [
            "coverage",
            "recompute",
            "--store",
            str(store_path),
            "--system",
            "orders-api",
            "--scope",
            SCOPE_PATH,
            "--json",
        ]
    )
    assert code == ExitCode.POLICY_REFUSAL
    assert "not both" in capsys.readouterr().err


@pytest.mark.unit
def test_a_system_this_store_does_not_hold_is_refused(
    store_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo'd slug names no system, and is refused rather than treated as none."""
    code = main(
        ["coverage", "recompute", "--store", str(store_path), "--system", "no-such", "--json"]
    )
    assert code == ExitCode.POLICY_REFUSAL
    assert "no-such" in capsys.readouterr().err
