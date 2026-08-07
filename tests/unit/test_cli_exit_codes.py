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


@pytest.mark.unit
def test_agent_check_refuses_a_hosted_adapter_offline_with_a_policy_exit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """S7's Final Output Validation item 4, at unit speed.

    *Fails when* the refusal stops reaching the caller as exit `3` -- because the
    command swallowed it, or because the code's category drifted out of `policy`.
    *Matters because* an operator's script branches on `3` to distinguish "you
    are not allowed to do that" from "it broke"; a `1` here reads as an outage.
    *No other instrument catches it because* `test_agent_runner.py` asserts
    `build_adapter` raises the code and stops there -- it says nothing about the
    command that exposes it or about the exit mapping in between.
    """
    monkeypatch.setenv("ADOPT_OFFLINE", "1")

    exit_code = main(["agent", "check", "--adapter", "anthropic", "--json"])

    # The envelope goes to **stderr** (`json_out.emit_error`), so stdout stays
    # clean for a caller piping a successful payload into `jq`. Asserting on
    # stdout here would have passed for the wrong reason if the refusal were
    # printed nowhere at all.
    captured = capsys.readouterr()
    assert exit_code == ExitCode.POLICY_REFUSAL
    assert "AGENT_OFFLINE_ADAPTER_DENIED" in captured.err
    assert captured.out == ""


@pytest.mark.unit
def test_agent_adapters_reports_rather_than_refusing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control for the row above, and the §14 division it rests on.

    `adapters` **reports** and exits `0` even when nothing is available: being
    told Anthropic is denied offline is the answer, not a failure. Without this,
    a group that refused on every subcommand would satisfy the refusal test.
    """
    monkeypatch.setenv("ADOPT_OFFLINE", "1")

    exit_code = main(["agent", "adapters", "--json"])

    out = capsys.readouterr().out
    assert exit_code == ExitCode.SUCCESS
    assert '"anthropic"' in out
    assert '"available": false' in out
