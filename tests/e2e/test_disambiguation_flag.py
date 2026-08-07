"""The flag, the exit code, and the human accept -- through the real commands.

*Fails when* the flag stops gating the pass, when a proposal starts changing an
exit code, or when an archetype reaches a store without an operator naming it.
*Matters because* `01` §8 allows **no confidence exemption** for writing an
archetype: the whole autonomy claim for Build 0's one model caller is that it
proposes and a human decides, and a command that quietly persisted a proposal
would break that with no visible symptom. *No other instrument catches it because*
`tests/unit/test_disambiguation_gate.py` asserts what the pass sends and returns,
and says nothing about whether the CLI writes it.

**Driven through `main()`, not through the functions.** The claim is about exit
codes and what lands in a store, and both are the composition root's -- the S4-era
defect where `main()` discarded a `typer.Exit` is exactly the class of bug a
function-level test cannot see.
"""

import json
from pathlib import Path

import pytest

from adopt_cli.main import main
from adopt_obs import ExitCode

pytestmark = pytest.mark.e2e

REPOS = Path(__file__).resolve().parent.parent / "fixtures" / "repos"
ANSWERS = Path(__file__).resolve().parent.parent / "fixtures" / "answers"
AMBIGUOUS = REPOS / "_mixed" / "django_with_dbt"


def test_the_flag_off_makes_no_model_call_and_reports_no_proposal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`04` §4 step 3: with the flag off, ambiguity returns and the model is never
    reached.

    The payload carrying no `proposal` key at all is the assertion -- an absent key
    is the difference between "the pass did not run" and "the pass ran and failed",
    and an operator reading a run needs to know which.
    """
    monkeypatch.delenv("ADOPT_FEATURE_AGENT_DISAMBIGUATION", raising=False)

    exit_code = main(["detect", str(AMBIGUOUS), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.USAGE_ERROR
    assert payload["archetype"] is None
    assert "proposal" not in payload


def test_the_flag_on_without_an_adapter_degrades_and_still_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`04` §3's last row: the deterministic path is the product.

    With the flag on and no adapter configured, the pass cannot run -- and the
    command still reports the ranked scores and still exits `2`. A model outage
    must not turn an honest refusal into a failure of a different kind.
    """
    monkeypatch.setenv("ADOPT_FEATURE_AGENT_DISAMBIGUATION", "1")
    monkeypatch.setenv("ADOPT_RUNTIME_PATH", str(tmp_path / "runtime.db"))
    monkeypatch.delenv("ADOPT_ADAPTER", raising=False)

    exit_code = main(["detect", str(AMBIGUOUS), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.USAGE_ERROR
    assert payload["proposal"]["available"] is False
    assert payload["proposal"]["reason"] == "AGENT_ADAPTER_UNKNOWN"
    assert payload["proposal"]["accepted"] is False
    # The deterministic evidence survives the failed pass, which is the point.
    assert payload["scores"]
    assert payload["rules_fired"]


def test_init_refuses_an_ambiguous_tree_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """No `--archetype`, no write. The store is not even created."""
    store = tmp_path / ".adopt" / "store.db"
    monkeypatch.setenv("ADOPT_STORE_PATH", str(store))

    exit_code = main(
        [
            "init",
            str(AMBIGUOUS),
            "--scope",
            "northwind/acme-erp/orders-api/prod",
            "--answers",
            str(ANSWERS / "t2.json"),
            "--json",
        ]
    )

    capsys.readouterr()
    assert exit_code == ExitCode.USAGE_ERROR
    assert not store.exists(), "an ambiguous init left a store behind"


def test_an_operator_accepting_an_archetype_is_what_writes_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The human-accept step, end to end.

    The same ambiguous tree that was refused above now initializes, because an
    operator named the archetype -- and the payload records that the value came
    from them rather than from detection. `archetype_source` is the audit answer to
    "who decided this", which is the question worth asking of any system that can
    propose.
    """
    store = tmp_path / ".adopt" / "store.db"
    monkeypatch.setenv("ADOPT_STORE_PATH", str(store))

    exit_code = main(
        [
            "init",
            str(AMBIGUOUS),
            "--scope",
            "northwind/acme-erp/orders-api/prod",
            "--answers",
            str(ANSWERS / "t2.json"),
            "--archetype",
            "web",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert payload["archetype"] == "web"
    assert payload["archetype_source"] == "operator"
    assert store.exists()


def test_an_archetype_outside_the_vocabulary_is_refused_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A typo is a different referent, not a slightly wrong answer.

    `system.archetype` decides which extractors a downstream item runs, so an
    unrecognised value stored would send every later pass at the wrong set -- the
    argument CR-32 made for scope, applied to the one other field typed by hand.
    """
    store = tmp_path / ".adopt" / "store.db"
    monkeypatch.setenv("ADOPT_STORE_PATH", str(store))

    exit_code = main(
        [
            "init",
            str(AMBIGUOUS),
            "--scope",
            "northwind/acme-erp/orders-api/prod",
            "--answers",
            str(ANSWERS / "t2.json"),
            "--archetype",
            "wbe",
            "--json",
        ]
    )

    capsys.readouterr()
    assert exit_code == ExitCode.USAGE_ERROR
    assert not store.exists()
