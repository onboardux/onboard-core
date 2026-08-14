"""The egress guard and the exec seam -- `03` §6, PRD N7, CUJ-5. **B1-CR-53/54.**

*Defect sentence.* Fails when an extractor can open a socket, when a blocked
attempt goes unattributed, or when the subprocess seam admits a binary outside
its allowlist; matters because `01` §1.6 makes "no phone-home" a non-negotiable
invariant a client security reviewer is invited to check, and because `03` §2
permits `universal-ctags` **only** as a subprocess -- linking it would breach the
licence policy; no other instrument catches it because a run that phones home
completes normally and produces an identical map.

**The guard is strict here**, which is `03` §6's rule for tests: an extractor
that tries to reach the network fails the suite instead of leaving a line in a
report nobody reads.
"""

import socket

import pytest
from adopt_map.execseam import ALLOWED_TOOLS, run_tool, tool_available
from adopt_map.netguard import EgressGuard, guarded

from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.integration


def test_a_blocked_attempt_is_recorded_with_the_calling_extractor() -> None:
    """`01` CUJ-5's failure branch: blocked, **recorded**, degraded.

    Attribution is the half that matters operationally. "Something tried to phone
    home" is not actionable; "`web.integrations` tried to reach example.com:443"
    is a defect somebody can fix.
    """
    guard = EgressGuard()
    with guarded(guard), guard.attributed_to("web.integrations"):
        connection = socket.socket()
        try:
            connection.connect_ex(("203.0.113.1", 443))
        finally:
            connection.close()

    assert guard.attempted == 1
    attempt = guard.attempts[0]
    assert attempt.extractor == "web.integrations"
    assert attempt.host == "203.0.113.1"
    assert attempt.port == 443


def test_a_strict_guard_raises_at_the_call_site() -> None:
    """`03` §6: in tests the guard is strict-fail.

    A guard that only counted would let a suite pass while an extractor reached
    the network on every run -- the count would be in a report, and the report
    would be green.
    """
    guard = EgressGuard(strict=True)
    with guarded(guard), pytest.raises(OSError, match="refused by the adopt egress guard"):
        connection = socket.socket()
        try:
            connection.connect(("203.0.113.1", 443))
        finally:
            connection.close()


def test_loopback_is_not_egress() -> None:
    """The decision is on the **address**, so it cannot be widened by a hostname.

    A locally-bound service is not a phone-home, and denying it would make the
    guard fail runs it has no opinion about.
    """
    guard = EgressGuard(strict=True)
    with guarded(guard):
        connection = socket.socket()
        try:
            # Nothing is listening; the point is that the guard did not refuse it
            # before the kernel got the chance to.
            connection.settimeout(0.05)
            connection.connect_ex(("127.0.0.1", 9))
        finally:
            connection.close()
    assert guard.attempted == 0


def test_the_guard_is_removed_again_even_when_the_block_raises() -> None:
    """A process that left the guard installed would refuse the CLI's own later
    work for reasons nobody could trace."""
    original = socket.socket.connect
    guard = EgressGuard(strict=True)
    with pytest.raises(RuntimeError), guarded(guard):
        raise RuntimeError("something else failed")
    assert socket.socket.connect is original


def test_an_exempt_caller_is_permitted_and_not_counted() -> None:
    """`03` §6 names exactly one future exemption: the `AgentRunner` adapter
    under `--agent`. It is declared now and unused, because a guard whose
    exemption arrives later is a guard somebody widens under deadline pressure."""
    guard = EgressGuard(strict=True, exempt=frozenset({"adopt_agent.adapter"}))
    with guarded(guard), guard.attributed_to("adopt_agent.adapter"):
        connection = socket.socket()
        try:
            connection.settimeout(0.05)
            connection.connect_ex(("203.0.113.1", 443))
        except OSError:
            pass
        finally:
            connection.close()
    assert guard.attempted == 0


def test_the_exec_seam_refuses_a_binary_outside_its_allowlist() -> None:
    """`03` §6 permits the declared analysis binaries only.

    Refused **before** the filesystem is consulted, so an unknown name cannot
    even be probed for -- and the refusal names the licence consequence, because
    `03` §2 makes adding a row a licence decision rather than a convenience.
    """
    with pytest.raises(AdoptError) as caught:
        run_tool("curl", ["https://example.com"])
    assert caught.value.code is ErrorCode.MAP_EXTRACTOR_FAILED
    assert "allowlist" in caught.value.message
    with pytest.raises(AdoptError):
        tool_available("bash")


def test_ctags_is_the_only_allowlisted_tool_and_carries_its_licence_reason() -> None:
    """The allowlist is small and each row states why it is admitted.

    `universal-ctags` is GPL-2.0+ and admitted **subprocess only**; a row added
    without its `subprocess-deps.toml` entry makes the licence gate treat it as
    `in-binary` and fail closed.
    """
    assert set(ALLOWED_TOOLS) == {"ctags"}
    assert "GPL-2.0+" in ALLOWED_TOOLS["ctags"]
    assert "subprocess" in ALLOWED_TOOLS["ctags"]


def test_a_missing_tool_degrades_to_none_rather_than_raising() -> None:
    """`01` F9.2's tool-absent arm, and the common case on a laptop.

    `None` is the ladder's signal to drop a rung. Raising would turn a machine
    without ctags installed into a failed run.
    """
    if tool_available("ctags"):  # pragma: no cover -- depends on the machine
        pytest.skip("ctags is installed here; the absent arm is covered by the ladder suite")
    assert run_tool("ctags", ["--version"]) is None


def test_the_ctags_extractor_declines_when_the_binary_is_absent() -> None:
    """`applies_to` answers `False`, so the rung is skipped with a recorded
    reason rather than running and finding nothing."""
    from adopt_extractors_common import CtagsExtractor

    assert CtagsExtractor().applies_to(".") is tool_available("ctags")
