"""Remote mode: the CLI as a channel, and the three ways that can go wrong.

Build 7 S7.2. Every case here is aimed at a failure that leaves an operator
believing something landed when it did not, because that is the whole risk this
feature carries: a store that is a read replica looks exactly like a store that
is not, and a capture written to the wrong one is invisible until the next
person asks the question and gets UNKNOWN.

**The transport is real.** A loopback `ThreadingHTTPServer` answers, so `urllib`,
the timeout, the `Authorization` header and the JSON round trip all actually run.
Patching `urlopen` would assert that this module calls a function -- which is not
in doubt -- and would have passed just as happily against a request that never
set the header.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest

from adopt_cli.commands._remote_support import configured_remote
from adopt_cli.remote import PLANE_SYSTEM_KEY, PLANE_TOKEN_ENV_KEY, PLANE_URL_KEY, post_json
from adopt_obs import AdoptError, ErrorCategory, ErrorCode

pytestmark = pytest.mark.unit

_TOKEN_VARIABLE = "ADOPT_PLANE_TOKEN"
_TOKEN = "handle.secret-not-a-real-token"


class _Handler(BaseHTTPRequestHandler):
    """Records what arrived and replies with whatever the test set."""

    status: ClassVar[int] = 200
    reply: ClassVar[bytes] = b'{"ok": true}'
    seen: ClassVar[dict[str, Any]] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _Handler.seen = {
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "content_type": self.headers.get("Content-Type"),
            "body": json.loads(self.rfile.read(length).decode("utf-8")) if length else None,
        }
        self.send_response(_Handler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_Handler.reply)))
        self.end_headers()
        self.wfile.write(_Handler.reply)

    def log_message(self, *_args: Any) -> None:
        """Silence. The suite's output is not this server's log."""


@pytest.fixture
def plane() -> Iterator[str]:
    """A loopback stand-in for `plane-api`. Returns its base URL."""
    _Handler.status = 200
    _Handler.reply = b'{"ok": true}'
    _Handler.seen = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    # `poll_interval` shortened for the reason the probe journey gives: the
    # default 0.5s is teardown latency paid per test, and shortening it is not a
    # sleep -- nothing here waits on wall time for an assertion to become true.
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
    thread.daemon = True
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _config(url: str | None = None, system: str | None = "sys_orders") -> dict[str, str | None]:
    return {
        PLANE_URL_KEY: url,
        PLANE_SYSTEM_KEY: system,
        PLANE_TOKEN_ENV_KEY: _TOKEN_VARIABLE,
    }


def test_no_remote_configured_is_local_mode_not_a_refusal() -> None:
    """Fails when an ordinary field store starts refusing `adopt answer`.

    Matters because remote mode is opt-in for one deployment shape and every
    other store in the world is unaffected; an absent `ADOPT_PLANE_URL` has to
    mean "you are a field store" and not "you are misconfigured". No other test
    covers the *absence* path -- every case below configures a remote.
    """
    assert configured_remote(_config(url=None), environ={}) is None


def test_a_partly_configured_remote_is_refused_rather_than_half_used() -> None:
    """Fails when a URL with no token, or no system, silently writes locally.

    **The dangerous case, and the reason absence and partial absence differ.**
    An operator who set `ADOPT_PLANE_URL` has decided this store routes; if the
    token variable is empty and the CLI fell back to a local write, the answer
    lands in a replica nobody else reads and the escalation stays open on the
    plane forever. Every missing piece is named, because the fix is different
    for each.
    """
    with pytest.raises(AdoptError) as refused:
        configured_remote(_config(url="http://plane.invalid"), environ={})
    assert refused.value.code is ErrorCode.PLANE_REMOTE_NOT_CONFIGURED
    assert "token" in refused.value.message

    with pytest.raises(AdoptError) as no_system:
        configured_remote(
            _config(url="http://plane.invalid", system=None), environ={_TOKEN_VARIABLE: _TOKEN}
        )
    assert PLANE_SYSTEM_KEY in no_system.value.message


def test_the_refusal_exits_two_and_not_three() -> None:
    """Fails when an unconfigured remote starts reading as a policy refusal.

    Matters because the two send an operator to different places: exit `3` says
    a rule refused you, exit `2` says you did not tell the command something it
    needs. `PLANE_AUTH_INVALID` and `PLANE_ACTIVATION_UNOWNED` are policy;
    this one is usage, and a script branching on the exit code is what the
    distinction is for.
    """
    from adopt_obs import ERROR_CATEGORIES

    assert ERROR_CATEGORIES[ErrorCode.PLANE_REMOTE_NOT_CONFIGURED] is ErrorCategory.USAGE


def test_the_request_carries_the_token_and_the_body(plane: str) -> None:
    """Fails when the bearer header, the content type or the JSON body is dropped.

    Matters because every one of those failures reads as a *plane* problem from
    the operator's side: a missing header is a `401` that looks like a revoked
    token, and a missing content type is a `422` that looks like a broken
    endpoint. Nothing else in this repository exercises the wire this module
    writes.
    """
    _Handler.reply = json.dumps({"escalation_id": "esc_01"}).encode("utf-8")
    remote = configured_remote(_config(url=plane), environ={_TOKEN_VARIABLE: _TOKEN})
    assert remote is not None

    payload = post_json(remote, "/v1/systems/sys_orders/escalations", {"question": "why?"})

    assert payload == {"escalation_id": "esc_01"}
    assert _Handler.seen["path"] == "/v1/systems/sys_orders/escalations"
    assert _Handler.seen["authorization"] == f"Bearer {_TOKEN}"
    assert _Handler.seen["content_type"] == "application/json"
    assert _Handler.seen["body"] == {"question": "why?"}


def test_a_trailing_slash_on_the_base_url_does_not_double_the_path(plane: str) -> None:
    """Fails when `http://plane/` and `http://plane` route differently.

    Matters because which one an operator typed is not something they will think
    to check, and `//v1/...` is routed by some proxies and 404'd by others -- a
    failure that depends on the deployment rather than on anything they did.
    """
    _Handler.reply = b'{"ok": true}'
    remote = configured_remote(_config(url=plane + "/"), environ={_TOKEN_VARIABLE: _TOKEN})
    assert remote is not None

    post_json(remote, "/v1/health", {})
    assert _Handler.seen["path"] == "/v1/health"


def test_the_planes_typed_error_survives_the_wire(plane: str) -> None:
    """Fails when a refusal from the plane loses its code on the way back.

    **The point of the whole envelope.** An operator who confirms an escalation
    twice must see `ESCALATION_ALREADY_ANSWERED` and exit `3` whether the store
    that refused them was their own or the tenant's -- one error vocabulary, not
    two. A translated code here would exit with a category the plane never chose.
    """
    _Handler.status = 403
    _Handler.reply = json.dumps(
        {
            "error": {
                "code": "ESCALATION_ALREADY_ANSWERED",
                "category": "policy",
                "message": "escalation esc_01 is already promoted",
                "hint": "A question is answered once.",
            }
        }
    ).encode("utf-8")
    remote = configured_remote(_config(url=plane), environ={_TOKEN_VARIABLE: _TOKEN})
    assert remote is not None

    with pytest.raises(AdoptError) as refused:
        post_json(remote, "/v1/escalations/esc_01/confirm", {"text": "x", "actor": "a"})
    assert refused.value.code is ErrorCode.ESCALATION_ALREADY_ANSWERED
    assert "already promoted" in refused.value.message


def test_a_non_envelope_error_body_is_not_mistaken_for_a_plane_refusal(plane: str) -> None:
    """Fails when a proxy's HTML error page is reported as a typed refusal.

    Matters because "the plane refused you" and "something between you and the
    plane answered" send an operator to completely different places, and the
    second is the more common one in a corporate network. Guessing a code from a
    body that carries none would name a rule nobody applied.
    """
    _Handler.status = 502
    _Handler.reply = b"<html><body>Bad Gateway</body></html>"
    remote = configured_remote(_config(url=plane), environ={_TOKEN_VARIABLE: _TOKEN})
    assert remote is not None

    with pytest.raises(AdoptError) as failed:
        post_json(remote, "/v1/health", {})
    assert failed.value.code is ErrorCode.MANIFEST_INVALID
    assert "502" in failed.value.message


def test_an_unreachable_plane_names_the_url_and_never_the_token() -> None:
    """Fails when a transport error puts an engagement token in a terminal.

    **The one line in this module a careless format string would break**, and it
    would break it in the place operators paste into tickets. The URL is what
    they need; the credential is what nothing may print.
    """
    remote = configured_remote(_config(url="http://127.0.0.1:1"), environ={_TOKEN_VARIABLE: _TOKEN})
    assert remote is not None

    with pytest.raises(AdoptError) as failed:
        post_json(remote, "/v1/health", {})
    assert failed.value.code is ErrorCode.ADOPT_OFFLINE_DENIED
    assert "127.0.0.1:1" in failed.value.message
    assert _TOKEN not in failed.value.message
    assert _TOKEN not in (failed.value.hint or "")
