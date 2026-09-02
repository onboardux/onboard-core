"""`adopt ci-sense` actually posts, and refuses when there is nowhere to post to.

*Fails when* the payload never reaches the wire, when the token is not carried,
or when an unconfigured store degrades to a silent local success. *Matters
because* this verb's whole job is delivery: everything it computes is worthless
if it does not arrive, and a step that "succeeded" while posting nothing is the
sensing silence Build 8 exists to end — the customer's pipeline goes green
forever while their knowledge rots unwatched. *No other instrument catches it
because* the local half is pure and would keep passing its own tests: extraction
works, the payload is well-formed, and nothing local can tell delivered from
undelivered.

**The transport is real**, on `test_remote_mode`'s and `test_pull`'s precedent:
a loopback `ThreadingHTTPServer` answers, so `urllib`, the `Authorization`
header, the timeout and the JSON round trip all run. Patching the poster would
assert that this module calls a function, which is not in doubt.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar, Final

import pytest

from adopt_cli.commands._ci_sense_support import SenseOutcome, post_payload
from adopt_cli.commands._remote_support import configured_remote
from adopt_cli.remote import PLANE_SYSTEM_KEY, PLANE_TOKEN_ENV_KEY, PLANE_URL_KEY, Remote

pytestmark = pytest.mark.unit

_TOKEN_VARIABLE: Final[str] = "ADOPT_PLANE_TOKEN"
_TOKEN: Final[str] = "handle.secret-not-a-real-token"
_SYSTEM: Final[str] = "sys_orders"


class _Handler(BaseHTTPRequestHandler):
    """Records what arrived and replies with whatever the test set."""

    reply: ClassVar[bytes] = json.dumps(
        {
            "accepted": True,
            "replayed": False,
            "batch_key": "sense:run_01JCISENSE",
            "counts": {"BINDING_INTACT_SEMANTICS_CHANGED": 2, "BINDING_DEAD": 1},
        }
    ).encode("utf-8")
    seen: ClassVar[dict[str, Any]] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _Handler.seen = {
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": json.loads(self.rfile.read(length).decode("utf-8")) if length else None,
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_Handler.reply)))
        self.end_headers()
        self.wfile.write(_Handler.reply)

    def log_message(self, *_args: Any) -> None:
        """Silence. The suite's output is not this server's log."""


@pytest.fixture
def plane() -> Iterator[str]:
    _Handler.seen = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_payload_reaches_the_plane_with_its_token(plane: str) -> None:
    remote = Remote(url=plane, system_id=_SYSTEM, token=_TOKEN)
    payload = {"payload_version": 1, "run_id": "run_01JCISENSE", "observed": []}

    outcome = post_payload(remote, payload)

    assert _Handler.seen["path"] == f"/v1/systems/{_SYSTEM}/sense"
    assert _Handler.seen["authorization"] == f"Bearer {_TOKEN}"
    assert _Handler.seen["body"] == payload, "the payload must arrive unaltered"
    assert isinstance(outcome, SenseOutcome)


def test_the_plane_s_verdict_is_read_back_rather_than_assumed(plane: str) -> None:
    """The step prints what the plane concluded, not what it hoped."""
    remote = Remote(url=plane, system_id=_SYSTEM, token=_TOKEN)

    outcome = post_payload(remote, {"payload_version": 1})

    assert outcome.accepted is True
    assert outcome.replayed is False
    assert outcome.batch_key == "sense:run_01JCISENSE"
    assert outcome.findings == 3, "counts are summed for the CI log's one-line summary"


def test_a_replayed_run_is_reported_as_a_replay(plane: str) -> None:
    """Idempotency is visible to the operator, not silent.

    A retried pipeline step that quietly reported a fresh batch would make a
    replay indistinguishable from a second real change.
    """
    _Handler.reply = json.dumps(
        {"accepted": True, "replayed": True, "batch_key": "sense:run_earlier", "counts": {}}
    ).encode("utf-8")
    try:
        remote = Remote(url=plane, system_id=_SYSTEM, token=_TOKEN)
        outcome = post_payload(remote, {"payload_version": 1})
        assert outcome.replayed is True
        assert outcome.batch_key == "sense:run_earlier"
        assert outcome.findings == 0
    finally:
        _Handler.reply = json.dumps(
            {
                "accepted": True,
                "replayed": False,
                "batch_key": "sense:run_01JCISENSE",
                "counts": {"BINDING_INTACT_SEMANTICS_CHANGED": 2, "BINDING_DEAD": 1},
            }
        ).encode("utf-8")


def test_an_unconfigured_store_has_no_remote_at_all() -> None:
    """Absence is `None` here; the verb turns it into a typed refusal.

    The negative control for the whole feature: without this, a CI step in a
    repository nobody configured would extract, post nowhere and exit 0.
    """
    assert configured_remote({PLANE_URL_KEY: None}, environ={}) is None


def test_a_partly_configured_remote_is_refused_rather_than_guessed() -> None:
    """A URL with no token is an operator who meant to route and will not."""
    from adopt_obs import AdoptError, ErrorCode

    with pytest.raises(AdoptError) as raised:
        configured_remote(
            {
                PLANE_URL_KEY: "https://plane.example",
                PLANE_SYSTEM_KEY: _SYSTEM,
                PLANE_TOKEN_ENV_KEY: _TOKEN_VARIABLE,
            },
            environ={},
        )

    assert raised.value.code is ErrorCode.PLANE_REMOTE_NOT_CONFIGURED
