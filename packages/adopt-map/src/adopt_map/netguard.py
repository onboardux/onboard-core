"""The egress guard -- `03` §6, PRD N7 and CUJ-5. **B1-CR-53.**

`03` §6 said *"Build 0's egress guard, deny by default … every blocked attempt is
recorded with the calling extractor id; in tests the guard is strict-fail."*
**Build 0 has no such thing.** What Build 0 has is `AgentRunner` refusing to
*construct* a hosted adapter when `offline=True`, plus an `--allow-network` flag
on the CLI context. Neither denies a socket, neither can attribute an attempt to
an extractor, and neither can strict-fail in a test -- so `05` S1.3's validation
line *"zero egress with the guard strict-failing"* had no subject at all.

That is the same family as B1-CR-22, B1-CR-33 and B1-CR-37: the pack consumed a
Build 0 primitive that was never built. Under `00` §3 rider 1 the pack document is
repaired, and on B1-CR-28's precedent the mechanism lands **here**, in Build 1's
own package, because `adopt-agent` is protected (`03` §4) and this build may not
edit it.

**Why a socket-level deny and not a code review.** PRD N7 is *"the deterministic
pass completes with no network; zero egress attempts"* and `01` §1.6 makes "no
phone-home" a non-negotiable invariant a client security reviewer is invited to
check. A rule that lives in the import allowlist catches the extractor that
imports `socket`; it does not catch the one that reaches a transport through a
library that was allowed for another reason. The deny is at the last common
point before the kernel, which is `socket.socket.connect`.

**One exemption, named, and it is not in this build.** `03` §6: the `AgentRunner`
adapter under `--agent`. F12 is S1.7's, so this module ships with the exemption
declared and unused -- declared now because a guard whose exemption arrives later
is a guard somebody widens under deadline pressure.

**Attempts are recorded, not merely refused.** `01` CUJ-5's failure branch is
*"blocked by the guard, recorded, degraded; in CI this fails the suite"*. A
refusal nobody counts reads exactly like a run that never tried.
"""

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Final

from adopt_obs import get_logger

__all__ = ["EgressAttempt", "EgressGuard", "guarded"]

_log = get_logger(__name__)

#: Loopback is not egress. A client tree indexed over a locally-bound service --
#: and, in tests, any fixture that binds a port -- is not a phone-home, and
#: denying it would make the guard fail runs it has no opinion about. The
#: decision is on the *address*, so it cannot be widened by a hostname.
_LOCAL_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True, slots=True)
class EgressAttempt:
    """One blocked connection, attributed.

    `host` is recorded because an operator has to be able to tell a telemetry
    endpoint from a package index. It is a destination we were asked to reach,
    never client content, so it is safe in a log line -- and `port` plus
    `extractor` are what turn "something tried to phone home" into a defect
    somebody can fix.
    """

    extractor: str
    host: str
    port: int


@dataclass(slots=True)
class EgressGuard:
    """Deny by default; record every attempt; optionally raise on one.

    Args:
        strict: When true, a blocked attempt raises `OSError` at the call site
            rather than only being recorded. **CI runs strict** (`03` §6), so an
            extractor that tries to reach the network fails the suite instead of
            leaving a line in a report nobody reads.
        exempt: Extractor ids permitted to connect. Empty in this build; `03` §6
            names exactly one future member, the `AgentRunner` adapter under
            `--agent` (S1.7).
    """

    strict: bool = False
    exempt: frozenset[str] = field(default_factory=frozenset)
    attempts: list[EgressAttempt] = field(default_factory=list)
    _current: str = "framework"

    @property
    def attempted(self) -> int:
        """`run_report.json`'s `network_attempted` (`02` §9.3)."""
        return len(self.attempts)

    @contextmanager
    def attributed_to(self, extractor_id: str) -> Iterator[None]:
        """Attribute every attempt inside the block to one extractor."""
        previous = self._current
        self._current = extractor_id
        try:
            yield
        finally:
            self._current = previous

    def refuse(self, address: object) -> None:
        host, port = _destination(address)
        if host in _LOCAL_HOSTS:
            return
        attempt = EgressAttempt(extractor=self._current, host=host, port=port)
        if self._current in self.exempt:
            return
        self.attempts.append(attempt)
        _log.error(
            "map_egress_blocked",
            extractor=attempt.extractor,
            egress_host=attempt.host,
            egress_port=attempt.port,
        )
        if self.strict:
            raise OSError(
                f"egress to {attempt.host}:{attempt.port} refused by the adopt egress guard "
                f"(extractor {attempt.extractor}). `adopt map` is offline by default: the "
                "deterministic pass completes with no network (PRD N7)."
            )


def _destination(address: object) -> tuple[str, int]:
    """`(host, port)` from any of the address shapes `connect` accepts."""
    if isinstance(address, tuple) and len(address) >= 2:
        host, port = address[0], address[1]
        return str(host), int(port) if isinstance(port, int) else 0
    return str(address), 0


@contextmanager
def guarded(guard: EgressGuard) -> Iterator[EgressGuard]:
    """Install `guard` for the duration of the block.

    Patches `socket.socket.connect` and `connect_ex` rather than replacing the
    module, so a library holding its own reference to `socket` is still guarded --
    the reference points at the same class. Restored on the way out, including on
    an exception, because a process that leaves the guard installed would refuse
    the CLI's own future work for reasons nobody could trace.
    """
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(self: socket.socket, address: Any) -> None:
        guard.refuse(address)
        original_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        guard.refuse(address)
        return original_connect_ex(self, address)

    socket.socket.connect = connect  # type: ignore[assignment,method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[assignment,method-assign]
    try:
        yield guard
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
