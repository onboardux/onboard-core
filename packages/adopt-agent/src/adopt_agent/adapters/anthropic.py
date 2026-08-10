"""The hosted Anthropic adapter. AI spec §2, kind `hosted`, offline **denied**.

Its own module rather than a flag on the OpenAI-compatible one, because the
Messages API differs where it matters: `system` is a top-level parameter rather
than a message, `max_tokens` is **required**, tool results come back as content
blocks rather than as a separate field, and usage is reported under different
keys. Folding two wire shapes into one class with branches is how an adapter
stops being a translation and starts being a place bugs hide.

**No model identifier appears in this file**, including as a default
(AI spec §2). **No credential is logged, traced or returned** (`03` §3).
"""

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any, Final

from adopt_agent.adapters._wire import post_json
from adopt_agent.api import AdapterResponse, ToolCall, ToolSpec
from adopt_obs import AdoptError, ErrorCode

__all__ = ["AnthropicAdapter", "build"]

_URL: Final[str] = "https://api.anthropic.com/v1/messages"
_KEY_ENV: Final[str] = "ANTHROPIC_API_KEY"
_VERSION: Final[str] = "2023-06-01"
# const-sync: ok -- a determinism requirement from AI spec §2, not a tunable.
_TEMPERATURE: Final[float] = 0.0
#: The Messages API requires `max_tokens`. When the caller's budget sets no
#: token cap there is still a number to send, so the request is bounded rather
#: than open-ended -- an unbounded generation against a per-token price is the
#: failure the budget exists to prevent.
_FALLBACK_MAX_TOKENS: Final[int] = 4_096


class AnthropicAdapter:
    """Realizes `api.Adapter` structurally over the Messages API."""

    id: str = "anthropic"
    kind: str = "hosted"

    def __init__(self, *, model: str, api_key: str | None) -> None:
        self._model = model
        #: The content blocks of the previous assistant turn, kept so a
        #: `tool_result` has the `tool_use` it answers. Per-run state: the
        #: runner builds one adapter per `run()`, so this never spans runs.
        self._previous_assistant: list[dict[str, object]] | None = None
        self._api_key = api_key

    def model(self) -> str:
        return self._model

    def params_hash(self) -> str:
        """Excludes the credential: a hash of a secret is a lookup away from it."""
        material = f"{self.id}|{_URL}|{self._model}|{_TEMPERATURE}|{_VERSION}"
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
        # A `tool_result` must answer a `tool_use` the API has already seen, in
        # an assistant turn of the same conversation. Sending the result on its
        # own -- which this adapter did until the first real-model run -- is a
        # 400 every time, and it is why conformance cases 4 and 6 reported
        # `status='error'` after the tool had demonstrably been invoked.
        #
        # The seam's signature does not carry conversation history and does not
        # need to: `build_adapter` is called once per `run()`, so the adapter
        # instance spans every turn of that run and can remember the assistant
        # turn it just received. Keeping it here rather than widening `complete`
        # is deliberate -- the history is a *provider wire* concern, and `02`
        # §10.1's Protocol stays exactly as declared.
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"type": "text", "text": user}]}
        ]
        if tool_results and self._previous_assistant is not None:
            messages.append({"role": "assistant", "content": self._previous_assistant})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(result["id"]),
                            # `json.dumps`, not `str`: the payload is a dict, and
                            # `str` renders Python repr with single quotes, which
                            # is not JSON and is not what the model was shown.
                            "content": _render(result["content"]),
                        }
                        for result in tool_results
                    ],
                }
            )

        payload: dict[str, Any] = {
            "model": self._model,
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens or _FALLBACK_MAX_TOKENS,
            "temperature": _TEMPERATURE,
        }
        if tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ]

        headers = {"anthropic-version": _VERSION}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        body = post_json(_URL, payload, headers, adapter_id=self.id)
        blocks = body.get("content")
        # Kept verbatim rather than rebuilt from the normalized response: a
        # `tool_use` block echoed back with its `id` and `input` reconstructed by
        # us is a different block, and the API matches on what it sent.
        self._previous_assistant = blocks if isinstance(blocks, list) else None
        return _to_response(body)


def _render(content: object) -> str:
    """A tool payload as JSON text, never as a Python repr."""
    return content if isinstance(content, str) else json.dumps(content, sort_keys=True)


def _to_response(body: Mapping[str, Any]) -> AdapterResponse:
    """Normalize one turn, refusing a shape this adapter cannot read.

    Refusing rather than defaulting to empty text matters: an unreadable
    response silently read as "no text, no tool calls" ends the seam's loop and
    returns `ok`, which is a wrong answer dressed as a successful run.
    """
    blocks = body.get("content")
    if not isinstance(blocks, list):
        raise AdoptError(
            ErrorCode.AGENT_PROVIDER_ERROR,
            message="anthropic returned no content blocks",
            hint="The endpoint answered with a shape this adapter cannot read.",
        )

    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in blocks:
        if block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            arguments = block.get("input")
            calls.append(
                ToolCall(
                    id=str(block.get("id") or "call"),
                    name=str(block.get("name") or "unknown"),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )

    usage = body.get("usage") or {}
    return AdapterResponse(
        text="".join(texts),
        tool_calls=calls,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )


def build(*, model: str | None, endpoint: str | None) -> AnthropicAdapter:
    if not model:
        raise AdoptError(
            ErrorCode.AGENT_ADAPTER_UNKNOWN,
            message="ADOPT_MODEL is unset and this adapter has no default model",
            hint="Model selection is configuration, never code (AI spec §2).",
        )
    return AnthropicAdapter(model=model, api_key=os.environ.get(_KEY_ENV))
