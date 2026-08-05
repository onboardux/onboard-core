"""A command's deliberate non-zero exit reaches the caller.

*Fails when* `main()` discards the value `app(...)` returns. *Matters because*
contracts §14 gives `adopt doctor` and `adopt coverage recompute` exit `4` --
degraded success with findings -- and an operator's script branches on it: a
silent `0` tells them a store with a drifting coverage cache is healthy. *No
other instrument catches it because* every other CLI test asserts the JSON
payload, and the payload was always correct; only the exit code was lost.

**This is a repair, not a new feature.** Click invoked with
`standalone_mode=False` *returns* the code of a `typer.Exit` rather than raising
it, so `main()`'s `except` clause never saw one. `adopt doctor` has been
documented as exiting `4` since S0 and has been exiting `0` since S0; no S0
checkbox asserted it, which is exactly how a contract goes unimplemented while
every test passes.
"""

import pytest

from adopt_cli.main import main
from adopt_obs import ExitCode


@pytest.mark.unit
def test_doctor_exits_degraded_when_it_has_findings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `ADOPT_MODEL` without `ADOPT_ADAPTER` is one of `doctor`'s findings.
    monkeypatch.setenv("ADOPT_MODEL", "some-model")
    monkeypatch.delenv("ADOPT_ADAPTER", raising=False)

    exit_code = main(["doctor", "--json"])

    capsys.readouterr()
    assert exit_code == ExitCode.DEGRADED_WITH_FINDINGS


@pytest.mark.unit
def test_doctor_exits_zero_when_it_has_none(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: without it, a `main()` that returned `4` unconditionally
    would pass the test above."""
    monkeypatch.delenv("ADOPT_MODEL", raising=False)
    monkeypatch.delenv("ADOPT_ADAPTER", raising=False)
    monkeypatch.delenv("ADOPT_OFFLINE", raising=False)

    exit_code = main(["doctor", "--json"])

    capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
