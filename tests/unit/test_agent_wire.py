"""The provider wire: what leaves, what is read back, and what is renegotiated.

**Its own file, and the justification matters** because `test-generation-discipline`
defaults to extending an existing one. Two things live here that belong together
and belong nowhere else: the **privacy allowlist** on a provider's error body,
which is a T1 egress boundary, and the **parameter negotiation** that answers a
400. `test_agent_runner.py` is about the seam's control flow; this is about the
one module that touches a socket.

Every test here fakes `urlopen` and nothing else. That is the boundary we do not
own -- mocking anything below it would be testing our mocks, and mocking
anything above it would stop exercising the code that was actually wrong.
"""

import io
import json
import urllib.error
from typing import Any

import pytest

from adopt_agent import ToolSpec
from adopt_agent.adapters import _wire, openai_compatible
from adopt_obs import AdoptError


def _http_error(status: int, body: object) -> urllib.error.HTTPError:
    """A provider rejection with a JSON body, as `urlopen` would raise it."""
    encoded = json.dumps(body).encode("utf-8")
    return urllib.error.HTTPError(
        url="https://provider.example/v1/chat/completions",
        code=status,
        msg="rejected",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(encoded),
    )


class _Response:
    """A minimal `urlopen` context manager returning one JSON body."""

    def __init__(self, payload: object) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


@pytest.mark.unit
class TestProviderErrorAllowlist:
    """Only enumerated identifiers leave a provider's error body.

    *Fails when* the allowlist widens. *Matters because* `04` §8.3 says prompt
    text is not retrievable from our artifacts and a provider's error payload
    routinely echoes the request back -- so this is the one place a refusal can
    carry the prompt into our logs. *No other instrument catches it because* the
    planted-secret property covers the trace and the logger, not an exception
    message built from a response body.
    """

    def test_the_identifiers_are_reported(self) -> None:
        error = _http_error(
            400,
            {
                "error": {
                    "type": "invalid_request_error",
                    "code": "unsupported_value",
                    "param": "temperature",
                }
            },
        )

        rendered = _wire._identifiers(error)

        assert "type=invalid_request_error" in rendered
        assert "code=unsupported_value" in rendered
        assert "param=temperature" in rendered

    def test_the_free_text_message_never_leaves(self) -> None:
        """The assertion this file exists for.

        `message` is where a content-policy refusal quotes the content that
        triggered it, so it is excluded by name rather than by truncation.
        """
        secret = "the-client-prompt-that-must-not-escape"
        error = _http_error(400, {"error": {"code": "content_policy", "message": secret}})

        rendered = _wire._identifiers(error)

        assert secret not in rendered
        assert "code=content_policy" in rendered, "the identifier is still reported"

    def test_an_oversized_identifier_cannot_become_a_body_dump(self) -> None:
        error = _http_error(400, {"error": {"param": "x" * 5_000}})

        rendered = _wire._identifiers(error)

        assert len(rendered) < 200

    @pytest.mark.parametrize(
        ("body", "why"),
        [
            ("<html>502 Bad Gateway</html>", "a provider under load returns HTML"),
            ('{"not_an_error_object": 1}', "a shape with no `error` key"),
        ],
    )
    def test_an_unreadable_body_yields_nothing_rather_than_raising(
        self, body: str, why: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An error path that raises while explaining an error is worse than one
        that says less."""
        error = urllib.error.HTTPError(
            url="https://provider.example/v1",
            code=502,
            msg="bad gateway",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(body.encode("utf-8")),
        )

        assert _wire._identifiers(error) == "", why


@pytest.mark.unit
class TestNegotiation:
    """A 400 naming a parameter costs one adjusted retry, and is bounded.

    *Fails when* negotiation stops retrying, retries unchanged, or loops.
    *Matters because* the first live run against a current OpenAI model was
    rejected three separate times for three different parameters, and a seam
    that cannot adapt is a seam that cannot reach that vendor at all. *No other
    instrument catches it because* the pure rule function is tested elsewhere;
    what is tested here is that the loop applies it and then stops.
    """

    def test_a_named_parameter_is_adjusted_and_the_request_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[dict[str, Any]] = []

        def _fake_urlopen(request: Any, **kwargs: object) -> _Response:
            sent.append(json.loads(request.data.decode("utf-8")))
            if len(sent) == 1:
                raise _http_error(
                    400, {"error": {"code": "unsupported_value", "param": "temperature"}}
                )
            return _Response({"ok": True})

        monkeypatch.setattr(_wire.urllib.request, "urlopen", _fake_urlopen)
        payload = {"model": "m", "temperature": 0.0}

        result = _wire.post_json(
            "https://provider.example/v1",
            payload,
            {},
            adapter_id="openai",
            negotiate=openai_compatible.negotiate,
        )

        assert result == {"ok": True}
        assert len(sent) == 2, "the request must be retried, once"
        assert "temperature" in sent[0], "the first attempt asks for what the spec says"
        assert "temperature" not in sent[1], "the second drops what the provider refused"

    def test_negotiation_terminates_when_the_provider_keeps_refusing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """*Fails when* the loop is unbounded. A provider naming a different
        field every time would otherwise spend money forever."""
        attempts = 0

        def _always_refuse(request: Any, **kwargs: object) -> _Response:
            nonlocal attempts
            attempts += 1
            raise _http_error(400, {"error": {"code": "unsupported_value", "param": "temperature"}})

        monkeypatch.setattr(_wire.urllib.request, "urlopen", _always_refuse)

        with pytest.raises(AdoptError) as raised:
            _wire.post_json(
                "https://provider.example/v1",
                {"model": "m", "temperature": 0.0},
                {},
                adapter_id="openai",
                negotiate=openai_compatible.negotiate,
            )

        assert attempts < 10, "negotiation must terminate"
        assert "HTTP 400" in str(raised.value)

    def test_a_400_naming_nothing_negotiable_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bad tool schema must surface as itself, not as a parameter problem
        retried into the same refusal."""
        attempts = 0

        def _refuse(request: Any, **kwargs: object) -> _Response:
            nonlocal attempts
            attempts += 1
            raise _http_error(400, {"error": {"code": "invalid_request_error", "param": "tools"}})

        monkeypatch.setattr(_wire.urllib.request, "urlopen", _refuse)

        with pytest.raises(AdoptError):
            _wire.post_json(
                "https://provider.example/v1",
                {"model": "m", "tools": []},
                {},
                adapter_id="openai",
                negotiate=openai_compatible.negotiate,
            )

        assert attempts == 1, "an unnegotiable 400 buys no second call"

    def test_plaintext_to_a_non_loopback_host_is_refused_before_any_socket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AI spec §8 has no exception for convenience."""

        def _never(request: Any, **kwargs: object) -> _Response:
            raise AssertionError("a socket was opened to a plaintext remote endpoint")

        monkeypatch.setattr(_wire.urllib.request, "urlopen", _never)

        with pytest.raises(AdoptError) as raised:
            _wire.post_json("http://provider.example/v1", {}, {}, adapter_id="openai")

        assert "loopback" in str(raised.value)


@pytest.mark.unit
class TestToolResultTurnCarriesItsAntecedent:
    """A tool result is sent with the assistant turn it answers.

    *Fails when* an adapter sends a tool result on its own. *Matters because*
    that is exactly what both hosted adapters did until the first live run:
    Anthropic requires a `tool_result` to answer a `tool_use` the API has
    already seen, OpenAI requires a `role:"tool"` message to follow an assistant
    message carrying `tool_calls`, and neither sent the antecedent -- so every
    follow-up turn was a 400 and conformance cases 4 and 6 failed *after* the
    tool had demonstrably run. *No other instrument catches it because*
    `fake_recorded` replays its script whatever it is sent, so anything that
    depends on **what we send** is invisible to it. Three CI rounds and two
    vendors' credentials were spent discovering this; it is an offline test now.
    """

    def _capture(
        self, monkeypatch: pytest.MonkeyPatch, module: Any, reply: object
    ) -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []

        def _fake_post(
            url: str, payload: dict[str, Any], headers: dict[str, str], **kwargs: object
        ) -> object:
            sent.append(json.loads(json.dumps(payload)))
            return reply

        monkeypatch.setattr(module, "post_json", _fake_post)
        return sent

    def test_openai_replays_the_assistant_message_before_the_tool_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assistant = {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function"}]}
        reply = {"choices": [{"message": assistant}]}
        sent = self._capture(monkeypatch, openai_compatible, reply)
        adapter = openai_compatible.OpenAICompatibleAdapter(
            adapter_id="openai", kind="hosted", base_url="https://p.example", model="m", api_key="k"
        )

        adapter.complete(system="s", user="u", tools=[], tool_results=[], max_tokens=None)
        adapter.complete(
            system="s",
            user="u",
            tools=[],
            tool_results=[{"id": "c1", "name": "t", "content": {"result": 1}}],
            max_tokens=None,
        )

        roles = [message["role"] for message in sent[1]["messages"]]
        assert roles == ["system", "user", "assistant", "tool"], (
            "a role:'tool' message must follow the assistant turn carrying tool_calls"
        )
        assert sent[1]["messages"][2] == assistant, "replayed verbatim, not rebuilt"

    def test_anthropic_replays_the_tool_use_block_before_the_tool_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from adopt_agent.adapters import anthropic

        blocks = [{"type": "tool_use", "id": "c1", "name": "t", "input": {}}]
        sent = self._capture(monkeypatch, anthropic, {"content": blocks, "usage": {}})
        adapter = anthropic.AnthropicAdapter(model="m", api_key="k")

        adapter.complete(system="s", user="u", tools=[], tool_results=[], max_tokens=None)
        adapter.complete(
            system="s",
            user="u",
            tools=[],
            tool_results=[{"id": "c1", "name": "t", "content": {"result": 1}}],
            max_tokens=None,
        )

        messages = sent[1]["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert messages[1]["content"] == blocks, "the tool_use block is replayed verbatim"
        assert messages[2]["content"][0]["type"] == "tool_result"
        assert messages[2]["content"][0]["tool_use_id"] == "c1"

    def test_a_tool_payload_is_json_not_a_python_repr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`str(dict)` renders single quotes, which is not JSON and is not what
        the model was shown."""
        from adopt_agent.adapters import anthropic

        blocks = [{"type": "tool_use", "id": "c1", "name": "t", "input": {}}]
        sent = self._capture(monkeypatch, anthropic, {"content": blocks, "usage": {}})
        adapter = anthropic.AnthropicAdapter(model="m", api_key="k")

        adapter.complete(system="s", user="u", tools=[], tool_results=[], max_tokens=None)
        adapter.complete(
            system="s",
            user="u",
            tools=[],
            tool_results=[{"id": "c1", "name": "t", "content": {"a": 1}}],
            max_tokens=None,
        )

        rendered = sent[1]["messages"][2]["content"][0]["content"]
        assert rendered == '{"a": 1}'
        assert "'" not in rendered

    def test_openai_sends_reasoning_off_and_the_new_cap_name_when_tools_are_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both are what the first live run was rejected for."""
        sent = self._capture(
            monkeypatch, openai_compatible, {"choices": [{"message": {"content": "x"}}]}
        )
        adapter = openai_compatible.OpenAICompatibleAdapter(
            adapter_id="openai", kind="hosted", base_url="https://p.example", model="m", api_key="k"
        )
        tool = ToolSpec(
            name="t", description="d", input_schema={"type": "object"}, handler=lambda _: None
        )

        adapter.complete(system="s", user="u", tools=[tool], tool_results=[], max_tokens=64)

        assert sent[0]["reasoning_effort"] == "none"
        assert sent[0]["max_completion_tokens"] == 64
        assert "max_tokens" not in sent[0]
