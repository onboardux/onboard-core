"""`adopt review` against an operated plane: the queue reads, the answer posts.

Build 8 S8.2, F4. Every case here is aimed at a failure that leaves a reviewer
believing their decision landed when it did not -- the same risk `adopt answer`
carries in remote mode, with a sharper edge: a resolution written to a replica
is clobbered by the next `adopt pull`, and the entry stays open on the plane
where nobody is looking at it any more.

**The transport is real.** A loopback `ThreadingHTTPServer` answers, so `urllib`,
the `Authorization` header, the method and the JSON round trip all actually run.
Patching `urlopen` would assert that this module calls a function, which is not
in doubt, and would pass just as happily against a GET that resolved something.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest

from adopt_cli.commands._review_remote import remote_queue, resolve_remote_item
from adopt_cli.remote import Remote
from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit

_TOKEN = "handle.secret-not-a-real-token"
_SYSTEM = "sys_orders"


class _Handler(BaseHTTPRequestHandler):
    """Records what arrived and replies with whatever the test set."""

    status: ClassVar[int] = 200
    reply: ClassVar[bytes] = b'{"ok": true}'
    seen: ClassVar[dict[str, Any]] = {}

    def _record(self, method: str, body: dict[str, Any] | None) -> None:
        _Handler.seen = {
            "method": method,
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": body,
        }
        self.send_response(_Handler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_Handler.reply)))
        self.end_headers()
        self.wfile.write(_Handler.reply)

    def do_GET(self) -> None:
        self._record("GET", None)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        self._record("POST", json.loads(raw) if raw else None)

    def log_message(self, *_args: Any) -> None:
        """Silence. The suite's output is not this server's log."""


@pytest.fixture
def plane() -> Iterator[Remote]:
    """A loopback stand-in for `plane-api`, as a configured `Remote`."""
    _Handler.status = 200
    _Handler.reply = b'{"ok": true}'
    _Handler.seen = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
    thread.daemon = True
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield Remote(url=f"http://{host}:{port}", system_id=_SYSTEM, token=_TOKEN)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_listing_the_queue_is_a_get_that_names_the_configured_system(plane: Remote) -> None:
    """*Fails when* the listing stops being a read, or reads the wrong system.

    *Matters because* a listing sent as a POST would hit the resolve route, and
    a listing that named no system would ask the plane for a queue it cannot
    identify. *No other instrument catches it because* both mistakes come back
    as a plane-side error an operator reads as "the plane is broken".
    """
    _Handler.reply = json.dumps({"batch_count": 1, "batches": []}).encode("utf-8")

    answered = remote_queue(plane)

    assert answered["batch_count"] == 1
    assert _Handler.seen["method"] == "GET"
    assert _Handler.seen["path"] == f"/v1/systems/{_SYSTEM}/reviews"
    assert _Handler.seen["authorization"] == f"Bearer {_TOKEN}"


def test_a_resolution_posts_the_action_the_actor_and_the_system(plane: Remote) -> None:
    """*Fails when* any field the plane requires is dropped on the way out.

    *Matters because* the plane refuses a resolution that names no actor, and
    that refusal reads as a plane-side rule to whoever ran the command --
    sending them to read the API's docs for a field the CLI simply did not send.
    """
    _Handler.reply = json.dumps({"resolution": "confirmed", "revision": "krev_1"}).encode("utf-8")

    answered = resolve_remote_item(
        plane,
        review_item_id="ri_1",
        action="confirm-current",
        actor="act_dana",
    )

    assert answered["resolution"] == "confirmed"
    assert _Handler.seen["method"] == "POST"
    assert _Handler.seen["path"] == "/v1/reviews/ri_1/resolve"
    assert _Handler.seen["body"] == {
        "action": "confirm-current",
        "actor": "act_dana",
        "system_id": _SYSTEM,
    }


def test_optional_fields_are_omitted_rather_than_sent_as_null(plane: Remote) -> None:
    """*Fails when* absent optional fields travel as `null`.

    *Matters because* the plane refuses a body on an action that does not take
    one, so a `body_md: null` on a confirm would be refused for carrying a value
    the caller never supplied -- a refusal nobody could act on, because the flag
    they are told to drop is one they never passed.
    """
    resolve_remote_item(plane, review_item_id="ri_1", action="reject", actor="act_dana")
    assert "body_md" not in _Handler.seen["body"]
    assert "to" not in _Handler.seen["body"]

    resolve_remote_item(
        plane,
        review_item_id="ri_2",
        action="rebind",
        actor="act_dana",
        to_uri="onboard-v1://f/e/s/prod/endpoint/-/x",
    )
    assert _Handler.seen["body"]["to"] == "onboard-v1://f/e/s/prod/endpoint/-/x"
    assert "body_md" not in _Handler.seen["body"]


def test_the_planes_typed_refusal_survives_the_wire(plane: Remote) -> None:
    """*Fails when* a plane refusal is rewritten into a local error code.

    *Matters because* a reviewer resolving an entry somebody already answered
    must read `REVIEW_ITEM_RESOLVED` -- the same code their own store would have
    raised -- rather than a transport error that sends them to check the
    network. *No other instrument catches it because* any error at all makes the
    command fail, and only the code says whether the operator learned anything.
    """
    _Handler.status = 409
    _Handler.reply = json.dumps(
        {
            "error": {
                "code": ErrorCode.REVIEW_ITEM_RESOLVED.value,
                "message": "review item ri_1 is already confirmed",
                "hint": "A disposition is recorded once.",
            }
        }
    ).encode("utf-8")

    with pytest.raises(AdoptError) as refused:
        resolve_remote_item(plane, review_item_id="ri_1", action="reject", actor="act_dana")

    assert refused.value.code is ErrorCode.REVIEW_ITEM_RESOLVED
    assert "already confirmed" in refused.value.message


def test_an_unreachable_plane_names_the_url_and_never_the_token() -> None:
    """*Fails when* a transport failure puts the credential in a terminal.

    *Matters because* an unreachable plane is the single place an operator most
    wants the URL printed, and the single place a careless format string leaks a
    long-lived token into scrollback, a screenshot or a support ticket.
    """
    unreachable = Remote(url="http://127.0.0.1:9", system_id=_SYSTEM, token=_TOKEN)

    with pytest.raises(AdoptError) as failed:
        remote_queue(unreachable)

    assert failed.value.code is ErrorCode.ADOPT_OFFLINE_DENIED
    assert "127.0.0.1:9" in failed.value.message
    assert _TOKEN not in failed.value.message
    assert _TOKEN not in (failed.value.hint or "")
