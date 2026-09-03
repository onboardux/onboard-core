"""`adopt serve` -- the same answers, on loopback, for local tooling.

**Loopback is the default and non-loopback is loud.** `is_loopback` and
`exposure_warning` are separate from the server for one reason: a bind address is
the whole of this module's security posture, so the rule that decides it is a
pure function something can assert, rather than a branch buried in a start-up
path. There is no auth here, no TLS and no rate limit -- an editor plugin talking
to `127.0.0.1` needs none, and a process on an open interface needs all three.
B7's plane API is where a served answer earns those; this is a convenience, and
saying so out loud in a printed warning is the honest version of shipping it.

**Stdlib, and that is a decision rather than a shortcut** (plan §5): two routes
over JSON do not justify a web framework, and choosing one here would pre-empt
B7's stack decision on behalf of a loopback helper.

**`POST /ask` is not a public wire contract** (plan §10, R4). Its body is the
CLI's own `--json` payload, and it is explicitly unstable until B7. That is
recorded in the response itself -- `"contract": "unstable"` -- because a shape
somebody scripts against becomes a contract whether or not a document said so,
and the cheapest place to say otherwise is in the thing they read.

**The handler holds no store.** It calls back into a function the CLI supplied,
which opens the store per request. Threads and SQLite connections do not mix
(`check_same_thread`), and a server that shared one connection across
`ThreadingHTTPServer` workers would fail in whichever thread it was not created
on -- intermittently, under exactly the concurrency that makes serve worth
having.
"""

import ipaddress
import json
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

from adopt_const import ASK_SERVE_MAX_BODY_BYTES, ASK_SERVE_PORT
from adopt_obs import AdoptError

__all__ = [
    "ASK_ROUTE",
    "HEALTH_ROUTE",
    "LOOPBACK_HOST",
    "AskHandler",
    "build_server",
    "exposure_warning",
    "is_loopback",
]

LOOPBACK_HOST: Final[str] = "127.0.0.1"
ASK_ROUTE: Final[str] = "/ask"
HEALTH_ROUTE: Final[str] = "/healthz"

#: What answering a question needs from the caller: the question and whether to
#: escalate, in; the `--json` payload, out. The CLI supplies it closed over the
#: configured store path, so this module never learns a store exists.
AskCallable = Callable[[str, bool], Mapping[str, Any]]


def is_loopback(host: str) -> bool:
    """Whether `host` can only be reached from this machine.

    A name that is not an address is **not** loopback, deliberately. `localhost`
    resolves to a loopback address on every sane machine and to whatever
    `/etc/hosts` says on a machine somebody has edited, and this function is the
    one deciding whether to print a warning -- so it answers on what it can
    verify rather than on what is almost always true. The cost of being wrong in
    this direction is a warning nobody needed.
    """
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def exposure_warning(host: str, port: int) -> str | None:
    """The warning for binding `host`, or `None` when it is loopback.

    Names what is exposed rather than saying "warning: non-loopback": the reader
    needs to know that an unauthenticated endpoint answering from a client's
    knowledge store is now reachable by anything that can route to them.
    """
    if is_loopback(host):
        return None
    return (
        f"WARNING: serving on {host}:{port}, which is not loopback.\n"
        "  `adopt serve` has no authentication, no TLS and no rate limit. Anything "
        "that can reach this address can ask this store questions and read the "
        "answers, including the client content quoted in them.\n"
        "  The observability boundary still refuses answers it does not permit, but "
        "it was negotiated for a local tool -- not for an open port. Bind "
        f"{LOOPBACK_HOST} unless you have decided otherwise deliberately."
    )


class AskHandler(BaseHTTPRequestHandler):
    """`POST /ask` and `GET /healthz`, and nothing else.

    Two routes rather than a router: a third would be a decision about what
    serve is for, and that decision is B7's.
    """

    #: Set by `build_server`. A class attribute because `BaseHTTPRequestHandler`
    #: is instantiated per request by the server and takes no arguments of ours.
    ask: AskCallable
    server_version = "adopt-serve"
    #: Suppress the default `sys_version` disclosure. Not a security control --
    #: nothing here is exposed on purpose -- but a version banner is free to
    #: withhold and never free to give.
    sys_version = ""

    def do_GET(self) -> None:
        if self.path.split("?")[0] != HEALTH_ROUTE:
            self._send(HTTPStatus.NOT_FOUND, {"error": "no such route"})
            return
        self._send(HTTPStatus.OK, {"status": "ok", "contract": "unstable"})

    def do_POST(self) -> None:
        if self.path.split("?")[0] != ASK_ROUTE:
            self._send(HTTPStatus.NOT_FOUND, {"error": "no such route"})
            return

        try:
            body = self._read_body()
        except ValueError as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            self._send(HTTPStatus.BAD_REQUEST, {"error": "'question' must be a non-empty string"})
            return

        # Escalation over HTTP is **flag-only and explicit**: there is nobody to
        # prompt, and `consented` refuses every non-interactive path, so a
        # request that wants the question stored has to say so in the body. That
        # is the same act as `--escalate` -- a caller choosing.
        escalate = bool(body.get("escalate", False))

        try:
            payload = dict(self.ask(question, escalate))
        except AdoptError as refused:
            # A typed refusal is the answer, not a crash: `ASK_OUTSIDE_BOUNDARY`
            # is the boundary doing its job, and the caller needs the code.
            self._send(
                HTTPStatus.FORBIDDEN,
                {"error": refused.message, "code": str(refused.code), "hint": refused.hint},
            )
            return

        payload["contract"] = "unstable"
        self._send(HTTPStatus.OK, payload)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the default stderr access log.

        `BaseHTTPRequestHandler` writes the request line, which for `POST /ask`
        carries no question text -- but the default also has no structure, no
        redaction and no relationship to `adopt_obs`'s deny-list. A silent
        server is better than one logging through a channel this repository's
        privacy rules do not reach.
        """

    def _read_body(self) -> Mapping[str, Any]:
        raw_length = self.headers.get("Content-Length")
        length = int(raw_length) if raw_length and raw_length.isdigit() else 0
        if length > ASK_SERVE_MAX_BODY_BYTES:
            raise ValueError(f"request body exceeds {ASK_SERVE_MAX_BODY_BYTES} bytes")
        try:
            decoded = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError(f"body is not valid JSON: {error}") from error
        if not isinstance(decoded, dict):
            raise ValueError("body must be a JSON object")
        return decoded

    def _send(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def build_server(
    ask: AskCallable, *, host: str = LOOPBACK_HOST, port: int = ASK_SERVE_PORT
) -> ThreadingHTTPServer:
    """A server bound to `host:port`, answering through `ask`. The caller serves it.

    Returned rather than started so a test can bind port `0`, read back the port
    the OS chose and drive one request without a sleep -- which is the only way
    to test a server here at all, since sleeps in tests are banned.
    """
    handler = type("_BoundAskHandler", (AskHandler,), {"ask": staticmethod(ask)})
    return ThreadingHTTPServer((host, port), handler)
