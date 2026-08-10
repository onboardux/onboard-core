"""The OpenAI chat-completions wire shape, shared by two adapters.

`openai` and `local_openai` differ in exactly three things -- the base URL, where
the credential comes from, and whether offline mode permits them -- and in
nothing about the request or the response. One implementation is therefore
correct and two would be one of them drifting; AI spec §2 calls the local
adapter "OpenAI-compatible", and this module is what that phrase means in code.

**`temperature=0` is not a tunable and is not in `adopt_const`.** AI spec §2
fixes it per adapter as a *determinism requirement*: the conformance suite tests
the adapter contract, and a suite run at a sampling temperature would be
flaky for reasons that have nothing to do with the contract.
"""

from collections.abc import Mapping
from typing import Any, Final

from adopt_agent.adapters._wire import post_json
from adopt_agent.api import AdapterKind, AdapterResponse, ToolCall, ToolSpec
from adopt_obs import AdoptError, ErrorCode

__all__ = ["OpenAICompatibleAdapter"]

# const-sync: ok -- a determinism requirement from AI spec §2, not a tunable.
_TEMPERATURE: Final[float] = 0.0
_PATH: Final[str] = "/chat/completions"


class OpenAICompatibleAdapter:
    """Realizes `api.Adapter` structurally over the chat-completions shape."""

    def __init__(
        self,
        *,
        adapter_id: str,
        kind: AdapterKind,
        base_url: str,
        model: str,
        api_key: str | None,
    ) -> None:
        self.id = adapter_id
        self.kind = kind
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        #: The previous assistant message, verbatim, kept so a `role: "tool"`
        #: message has the `tool_calls` turn it answers. Per-run state: the
        #: runner builds one adapter per `run()`, so this never spans runs.
        self._previous_assistant: dict[str, Any] | None = None

    def model(self) -> str:
        return self._model

    def params_hash(self) -> str:
        """A digest of what this adapter will send, excluding the credential.

        Excluding it is not squeamishness: `params_hash` lands in `agent_run`
        and in every trace, and a hash of a secret is a lookup away from the
        secret (`adopt_obs.redact` records the same reasoning).
        """
        import hashlib

        material = f"{self.id}|{self._base_url}|{self._model}|{_TEMPERATURE}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def complete(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolSpec],
        tool_results: list[Mapping[str, Any]],
        max_tokens: int | None,
    ) -> AdapterResponse:
        # A `role: "tool"` message must answer an assistant message carrying the
        # matching `tool_calls`. Appending the results with no such antecedent --
        # which this adapter did until the first real-model run -- is a 400 every
        # time, and it is the same defect the Anthropic adapter carried in its own
        # dialect. The seam's signature is untouched: `build_adapter` runs once
        # per `run()`, so the adapter spans the turns and remembers the assistant
        # message it just received. Conversation history is a provider-wire
        # concern, and `02` §10.1's Protocol stays exactly as declared.
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if tool_results and self._previous_assistant is not None:
            messages.append(self._previous_assistant)
            for result in tool_results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(result["id"]),
                        "content": _render(result["content"]),
                    }
                )

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": _TEMPERATURE,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]

        headers = {"authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        body = post_json(self._base_url + _PATH, payload, headers, adapter_id=self.id)
        # Kept verbatim rather than rebuilt: the API matches `tool_call_id`
        # against the `tool_calls` it sent, and a reconstructed message is a
        # different message.
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message")
            self._previous_assistant = message if isinstance(message, dict) else None
        else:
            self._previous_assistant = None
        return _to_response(body, adapter_id=self.id)


def _render(content: object) -> str:
    import json

    return content if isinstance(content, str) else json.dumps(content, sort_keys=True)


def _to_response(body: Mapping[str, Any], *, adapter_id: str) -> AdapterResponse:
    """Normalize one provider turn, refusing a shape we cannot read.

    Refusing rather than defaulting matters: a response silently read as empty
    text with no tool calls terminates the seam's loop and returns `ok`, which
    is a wrong answer dressed as a successful run.
    """
    import json

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AdoptError(
            ErrorCode.AGENT_PROVIDER_ERROR,
            message=f"{adapter_id} returned no choices",
            hint="The endpoint answered with a shape this adapter cannot read.",
        )
    message = choices[0].get("message", {})
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function", {})
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        calls.append(
            ToolCall(
                id=str(raw.get("id") or "call"),
                name=str(function.get("name") or "unknown"),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )

    usage = body.get("usage") or {}
    return AdapterResponse(
        text=str(message.get("content") or ""),
        tool_calls=calls,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
    )
