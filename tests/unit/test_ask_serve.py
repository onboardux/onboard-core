"""`adopt serve`: where it binds, what it says when that is not loopback, and
whether `/ask` really is the same answer the CLI gives.

**Parity is the claim worth testing.** v6.1 says serve exposes *the same* paths,
and the cheap way to satisfy that sentence is two implementations that agree on
the day they are written. So the test drives a real server against a real store
and compares the response byte-for-byte against `answer_question`'s own payload
-- the assertion that fails the moment somebody adds a shortcut to one door.

**The bind rule is a pure function on purpose**, so the security-relevant
decision can be asserted without starting anything. `is_loopback` refusing a
hostname is deliberate and is tested as such: `localhost` is loopback on every
sane machine and whatever `/etc/hosts` says on one somebody has edited, and the
cost of the conservative answer is a warning nobody needed.

No sleeps anywhere: the server binds port `0`, the test reads back the port the
OS chose, and `handle_request` serves exactly one request on a worker thread.
"""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest
from adopt_ask.serve import build_server, exposure_warning, is_loopback

from adopt_const import ASK_SERVE_PORT
from adopt_obs import AdoptError, ErrorCode
from adopt_store import open_store


@pytest.mark.unit
class TestWhereItBinds:
    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.53", "::1", "0:0:0:0:0:0:0:1"])
    def test_loopback_addresses_are_recognised(self, host: str) -> None:
        assert is_loopback(host) is True

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "10.0.0.1", "::"])  # noqa: S104
    def test_routable_addresses_are_not_loopback(self, host: str) -> None:
        """*Fails when* a wildcard bind is treated as local.
        *Matters because* `0.0.0.0` is the single most likely `--host` value
        somebody reaches for, and it exposes an unauthenticated endpoint that
        answers from a client's knowledge store to everything that can route to
        the machine. *No other instrument catches it because* the server starts
        and works perfectly; the only signal is the warning."""
        assert is_loopback(host) is False

    @pytest.mark.parametrize("host", ["localhost", "example.com", "", "not-an-address"])
    def test_a_name_is_never_assumed_to_be_loopback(self, host: str) -> None:
        """Conservative by design: a name resolves to whatever the machine says,
        and this function decides whether to warn. Being wrong here costs a
        warning; being wrong the other way costs an open port nobody was told
        about."""
        assert is_loopback(host) is False

    def test_the_default_port_comes_from_the_constant(self) -> None:
        """No literal port anywhere in the serve path: `03` §2 owns the value."""
        assert ASK_SERVE_PORT == 8787

    def test_loopback_earns_no_warning(self) -> None:
        assert exposure_warning("127.0.0.1", ASK_SERVE_PORT) is None

    def test_a_non_loopback_bind_warns_and_names_what_is_exposed(self) -> None:
        """*Fails when* the warning disappears or shrinks to "non-loopback".
        *Matters because* the operator's decision needs the facts: no auth, no
        TLS, no rate limit, and client content in the answers. A warning that
        says only "this is not loopback" tells somebody who already typed
        `--host` nothing they did not know."""
        warning = exposure_warning("0.0.0.0", 9000)  # noqa: S104

        assert warning is not None
        assert "0.0.0.0:9000" in warning
        assert "no authentication" in warning
        assert "TLS" in warning
        assert "127.0.0.1" in warning, "the warning names the safe alternative"


@pytest.fixture
def one_request_server() -> Iterator[Any]:
    """A server on an OS-chosen port, serving requests until the test is done.

    `serve_forever` on a daemon thread rather than `handle_request`, because a
    parity test makes two requests and a `/healthz` test makes one -- and a
    fixture that could only answer once would silently hang the second.
    """
    state: dict[str, Any] = {}

    def ask(question: str, escalate: bool) -> dict[str, Any]:
        state["last"] = (question, escalate)
        answer = state["answer"]
        if isinstance(answer, BaseException):
            raise answer
        return dict(answer)

    server = build_server(ask, host="127.0.0.1", port=0)
    state["port"] = server.server_address[1]
    state["server"] = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(port: int, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _get(port: int, path: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.mark.unit
class TestTheTwoRoutes:
    def test_healthz_answers_without_touching_the_store(
        self, one_request_server: dict[str, Any]
    ) -> None:
        """*Fails when* `/healthz` starts answering through the ask path.
        *Matters because* a health check that opens a store, refreshes an FTS
        index and resolves freshness is a health check that reports unhealthy
        whenever the store is busy -- and something polling it every second
        would rebuild the index all day."""
        one_request_server["answer"] = {}

        status, body = _get(one_request_server["port"], "/healthz")

        assert status == 200
        assert body["status"] == "ok"
        assert "last" not in one_request_server, "/healthz must not reach the ask callable"

    def test_ask_passes_the_question_and_the_escalate_flag_through(
        self, one_request_server: dict[str, Any]
    ) -> None:
        one_request_server["answer"] = {"branch": "unknown", "citations": []}

        status, body = _post(
            one_request_server["port"],
            "/ask",
            {"question": "how do I rotate the API key?", "escalate": True},
        )

        assert status == 200
        assert one_request_server["last"] == ("how do I rotate the API key?", True)
        assert body["branch"] == "unknown"

    def test_escalate_defaults_to_false_when_the_body_omits_it(
        self, one_request_server: dict[str, Any]
    ) -> None:
        """*Fails when* an omitted field starts meaning "yes".
        *Matters because* there is no human on a socket to prompt, so the body
        is the only consent an HTTP caller can give -- and a default that stored
        the text would make every scripted question a stored transcript. *No
        other instrument catches it because* the answer is identical either way;
        only the escalation row differs."""
        one_request_server["answer"] = {"branch": "unknown"}

        _post(one_request_server["port"], "/ask", {"question": "q?"})

        assert one_request_server["last"] == ("q?", False)

    def test_the_response_says_the_shape_is_unstable(
        self, one_request_server: dict[str, Any]
    ) -> None:
        """R4: `POST /ask` is not a public wire contract until B7, and the
        cheapest place to say so is in the thing a caller actually reads."""
        one_request_server["answer"] = {"branch": "known"}

        _status, body = _post(one_request_server["port"], "/ask", {"question": "q?"})

        assert body["contract"] == "unstable"

    def test_a_boundary_refusal_is_reported_with_its_code_rather_than_crashing(
        self, one_request_server: dict[str, Any]
    ) -> None:
        """*Fails when* a typed refusal becomes a 500 or a traceback.
        *Matters because* `ASK_OUTSIDE_BOUNDARY` is the boundary working, and a
        caller has to be able to tell "the boundary said no" from "the server
        broke" -- they are a policy conversation and a bug report."""
        one_request_server["answer"] = AdoptError(
            ErrorCode.ASK_OUTSIDE_BOUNDARY, message="no boundary is declared", hint="run init"
        )

        status, body = _post(one_request_server["port"], "/ask", {"question": "q?"})

        assert status == 403
        assert body["code"] == "ASK_OUTSIDE_BOUNDARY"
        assert "no boundary" in body["error"]

    @pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "   "}, {"question": 7}])
    def test_a_missing_or_empty_question_is_a_bad_request(
        self, one_request_server: dict[str, Any], body: dict[str, Any]
    ) -> None:
        """*Fails when* an empty question reaches retrieval. *Matters because*
        it retrieves nothing, answers UNKNOWN, and -- with `escalate` set --
        opens an unanswerable escalation whose question is the empty string."""
        one_request_server["answer"] = {"branch": "unknown"}

        status, _payload = _post(one_request_server["port"], "/ask", body)

        assert status == 400
        assert "last" not in one_request_server

    def test_an_unknown_route_is_a_404_rather_than_an_answer(
        self, one_request_server: dict[str, Any]
    ) -> None:
        one_request_server["answer"] = {"branch": "known"}

        assert _get(one_request_server["port"], "/")[0] == 404
        assert _post(one_request_server["port"], "/query", {"question": "q?"})[0] == 404


@pytest.mark.unit
class TestParityWithTheCli:
    def test_slash_ask_returns_exactly_what_the_cli_would_print(
        self, s4_store: Any, s4_scope: Any, add_boundary: Any
    ) -> None:
        """*Fails when* serve and the CLI stop sharing one pipeline.
        *Matters because* v6.1 says serve exposes *the same* paths, and two
        implementations would be two places the freshness check could be
        skipped, two boundary guards to keep in step, and two answers to one
        question depending on which door the asker came through. *No other
        instrument catches it because* both doors would keep working -- they
        would simply stop agreeing, and nothing else compares them.
        """
        from adopt_cli.commands._ask_support import answer_question

        assert s4_scope.system is not None
        assert s4_scope.environment is not None
        # Environment-scoped, because `answer_question` asks for the boundary at
        # the environment it resolved -- and `latest_boundary` does not fall back
        # from an environment query to a system-wide row.
        add_boundary(system_id=s4_scope.system.id, environment_id=s4_scope.environment.id)
        s4_store.items().record(
            scope=s4_scope,
            kind="rationale",
            title="Refund approvals",
            body_md="The approval step exists because chargebacks were disputed.",
            authority_class="human_confirmed",
            verification="verified",
        )
        question = "why does the approval step exist on refunds?"
        direct = answer_question(
            s4_store, question, scope=s4_scope.path(), resolved_config={}
        ).payload

        state: dict[str, Any] = {}
        store_path = s4_store.backend.path

        def ask(asked: str, escalate: bool) -> dict[str, Any]:
            # A store **per request**, exactly as `adopt serve` does it -- a
            # SQLite connection belongs to the thread that created it, and
            # handing this test's handle to a worker raises
            # `SQLite objects created in a thread...`. Reproducing the command's
            # own lifetime here is what makes this a parity test rather than a
            # test of a shape production never uses.
            handle = open_store(store_path, read_only=False)
            try:
                return answer_question(
                    handle,
                    asked,
                    scope=s4_scope.path(),
                    escalate_flag=escalate,
                    interactive=False,
                    resolved_config={},
                ).payload
            finally:
                handle.close()

        server = build_server(ask, host="127.0.0.1", port=0)
        state["port"] = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, served = _post(state["port"], "/ask", {"question": question})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        assert status == 200
        assert direct["branch"] == "known", "the fixture must actually produce an answer"
        # `contract` is serve's own addition and is the one documented
        # difference: everything else is identical, including the citations.
        assert {key: value for key, value in served.items() if key != "contract"} == direct
