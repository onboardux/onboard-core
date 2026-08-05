"""N10 -- zero sockets opened across `init`, `detect`, `boundary` and `export`.

*Fails when* any command on the OSS path opens a socket. *Matters because*
"offline by default, zero telemetry, permanently" is the claim the whole OSS
posture rests on and the first thing a client security review tests: an FDE runs
this inside a client environment against code they do not own, and a single
outbound connection would end that. *No other instrument catches it because*
every other test asserts what a command *returns*, and a command can return
exactly the right answer while phoning home.

**This is a measurement, not an assertion of intent.** Until this file existed
the offline claim was checked by reading imports. The harness below replaces
`socket.socket.connect`, `connect_ex` and `create_connection` with functions that
record and refuse, so a connection attempt is a test failure naming the address
rather than a silent success with a network round trip.

The three replaced entry points are the ones every Python networking library
funnels through -- `urllib`, `requests`, `httpx` and every provider SDK included.
A library that bypassed all three would be using raw syscalls, which nothing in
this dependency tree does.
"""

import json
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from adopt_cli.main import main
from adopt_obs import ExitCode

REPOS = Path(__file__).resolve().parent.parent / "fixtures" / "repos"
ANSWERS = Path(__file__).resolve().parent.parent / "fixtures" / "answers"


class _SocketOpened(AssertionError):
    """Raised at the point of the attempt, so the traceback names the caller."""


@pytest.fixture
def no_sockets(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Refuse every outbound connection and record what was attempted."""
    attempts: list[str] = []

    def _refuse(*args: object, **_kwargs: object) -> object:
        target = args[1] if len(args) > 1 else args
        attempts.append(repr(target))
        raise _SocketOpened(f"a socket connection was attempted to {target!r}")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield attempts


@pytest.fixture
def offline_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ADOPT_STORE_PATH", str(tmp_path / ".adopt" / "store.db"))
    monkeypatch.setenv("ADOPT_OFFLINE", "1")
    return tmp_path


@pytest.mark.property
def test_the_four_offline_commands_open_no_socket(
    no_sockets: list[str], offline_workspace: Path
) -> None:
    """`05` S6's offline invariant, over the exact four commands it names."""
    bundle = offline_workspace / "bundle"

    assert (
        main(
            [
                "init",
                str(REPOS / "ai" / "langgraph_support"),
                "--scope",
                "northwind/acme-erp/support-agent/prod",
                "--answers",
                str(ANSWERS / "t4.json"),
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )

    assert main(["detect", str(REPOS / "web"), "--json"]) == ExitCode.SUCCESS

    assert main(["boundary", "--answers", str(ANSWERS / "t4.json"), "--json"]) == ExitCode.SUCCESS

    assert main(["export", str(bundle), "--json"]) == ExitCode.SUCCESS

    assert attempts_are_empty(no_sockets)


def attempts_are_empty(attempts: list[str]) -> bool:
    """Named so the assertion above reads as the claim rather than as a list check."""
    assert attempts == [], f"connection attempted to {attempts}"
    return True


@pytest.mark.property
def test_the_harness_itself_catches_a_connection(no_sockets: list[str]) -> None:
    """*Fails when* the harness stops harnessing.

    A blocked-socket test that would pass even if the block were removed is the
    worst instrument in the suite: it reports the invariant holding whether or
    not it does. Planting the violation is the only way to know it is watching --
    the same reason every CI gate here is proven by `plant_violation.py`.
    """
    with pytest.raises(AssertionError):
        socket.create_connection(("example.invalid", 80))
    assert no_sockets, "the harness recorded nothing for a connection it refused"


@pytest.mark.property
def test_init_on_an_ambiguous_tree_writes_nothing(
    no_sockets: list[str], offline_workspace: Path
) -> None:
    """Not a network claim, and it belongs here anyway.

    The offline run above is the only place the four commands execute end to end
    against a real workspace, which makes it the cheapest place to assert that a
    refused `init` leaves no store behind -- the empty database beside the real
    one that `store_option` is written to avoid.
    """
    store_path = offline_workspace / ".adopt" / "store.db"
    assert (
        main(
            [
                "init",
                str(REPOS / "_mixed" / "django_with_dbt"),
                "--scope",
                "northwind/acme-erp/monolith/prod",
                "--answers",
                str(ANSWERS / "t4.json"),
                "--json",
            ]
        )
        == ExitCode.USAGE_ERROR
    )
    assert not store_path.exists()
    assert attempts_are_empty(no_sockets)


@pytest.mark.property
def test_detect_output_is_json_only_under_the_json_flag(
    no_sockets: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """A caller piping into `jq` must not have to strip a banner.

    Here rather than in a CLI test because the offline harness is already the
    place the commands run for real, and `--json` purity is the other thing an
    integrator finds out the hard way.
    """
    assert main(["detect", str(REPOS / "data"), "--json"]) == ExitCode.SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["archetype"] == "data"
