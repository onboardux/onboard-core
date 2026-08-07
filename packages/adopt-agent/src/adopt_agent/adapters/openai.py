"""The hosted OpenAI adapter. AI spec §2, kind `hosted`, offline **denied**.

Thin by construction: the wire shape is `openai_compatible`'s and everything
hard belongs to the seam. What is here is the three things that differ from the
local adapter -- the base URL, the credential, and being hosted.

**No model identifier appears in this file**, including as a default.
`ADOPT_MODEL` names it and construction fails fast without it (AI spec §2): a
hard-coded default is how a tool aimed at every lab acquires a house lab.
"""

import os
from typing import Final

from adopt_agent.adapters.openai_compatible import OpenAICompatibleAdapter
from adopt_obs import AdoptError, ErrorCode

__all__ = ["build"]

_BASE_URL: Final[str] = "https://api.openai.com/v1"
_KEY_ENV: Final[str] = "OPENAI_API_KEY"


def build(*, model: str | None, endpoint: str | None) -> OpenAICompatibleAdapter:
    if not model:
        raise AdoptError(
            ErrorCode.AGENT_ADAPTER_UNKNOWN,
            message="ADOPT_MODEL is unset and this adapter has no default model",
            hint="Model selection is configuration, never code (AI spec §2).",
        )
    return OpenAICompatibleAdapter(
        adapter_id="openai",
        kind="hosted",
        # `endpoint` is honoured so a client's inline OpenAI-compatible gateway
        # can be targeted (AI spec §1). That is a deployment choice, never a
        # code path, and it is still a hosted adapter for offline purposes.
        base_url=endpoint or _BASE_URL,
        model=model,
        api_key=os.environ.get(_KEY_ENV),
    )
