"""The local, OpenAI-compatible adapter. AI spec §2, kind `local`, offline **allowed**.

This is the adapter that makes the two-adapter rule affordable: `04` §7.1 says
the conformance suite tests the adapter *contract*, not model quality, so a
small local instruct model passes it -- and a suite only a frontier model could
pass would quietly recreate the single-vendor dependency the seam exists to
prevent.

**Allowed offline, and that is the whole point.** Contracts §1.7 says an offline
process opens no socket "other than to a configured local adapter endpoint".
Implementation spec §7.4 makes this the adapter rollback: `ADOPT_ADAPTER`, with
the note that the local adapter is always available offline.

**The endpoint is required and never defaulted.** Guessing `localhost:11434`
would make the adapter appear available on a machine where nothing is listening,
and the failure would surface as a connection error mid-run rather than as a
refusal at construction.
"""

from typing import Final

from adopt_agent.adapters.openai_compatible import OpenAICompatibleAdapter
from adopt_obs import AdoptError, ErrorCode

__all__ = ["build"]

_ENDPOINT_KEY: Final[str] = "ADOPT_ADAPTER_ENDPOINT"


def build(*, model: str | None, endpoint: str | None) -> OpenAICompatibleAdapter:
    if not endpoint:
        raise AdoptError(
            ErrorCode.ADOPT_CONFIG_UNRESOLVED,
            message=f"{_ENDPOINT_KEY} is unset and the local adapter has no default endpoint",
            hint="Point it at the OpenAI-compatible endpoint you are running. It is not "
            "guessed: a guessed endpoint reports the adapter available on a machine "
            "where nothing is listening.",
        )
    if not model:
        raise AdoptError(
            ErrorCode.AGENT_ADAPTER_UNKNOWN,
            message="ADOPT_MODEL is unset and this adapter has no default model",
            hint="Model selection is configuration, never code (AI spec §2).",
        )
    return OpenAICompatibleAdapter(
        adapter_id="local_openai",
        kind="local",
        base_url=endpoint,
        model=model,
        # A local endpoint usually needs no credential; one is sent when the
        # deployment sets it, because a client's inline gateway may require it.
        api_key=None,
    )
