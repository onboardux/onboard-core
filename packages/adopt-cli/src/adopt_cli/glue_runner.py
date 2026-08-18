"""The `GlueRunner` realization -- Build 0's seam, satisfying Build 1's port.

`adopt_map.quarantine.GlueRunner` is a protocol with one method; this is the one
implementation that reaches a provider, and it does so only through
`adopt_agent.Runner` (`04` §3). The composition lives in `adopt_cli` for the same
reason the storage realizations do: `adopt-map` declares what it needs and is
handed something that satisfies it, so the package holding the rules depends on
neither a store nor a provider (`docs/pack/OPEN-DECISIONS.md` OD-1/OD-2).

**No provider is named here, in code or in a default.** `04` §3: *"the adapter
comes from `agent.adapter`"*, resolved through `adopt_cli.config` so
`adopt doctor` can say where it came from. A default would make one vendor the
one a run silently used.

**Validation is this side of the port**, because `04` §5's single reparse retry is
Build 0's runner's and a caller that validated would either duplicate the retry or
skip it. What arrives back from the seam is a `dict`; what leaves this module is a
validated model or an `AdoptError`.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from adopt_agent import AgentRequest, Budget, Runner
from adopt_const import MAP_AGENT_MAX_COST_USD, MAP_AGENT_MAX_WALL_S
from adopt_obs import AdoptError, ErrorCode, get_logger

__all__ = ["SeamGlueRunner"]

_log = get_logger(__name__)

_JSON: Final[dict[str, Any]] = {"sort_keys": True, "separators": (",", ":")}


class SeamGlueRunner:
    """Drives one prompt through `AgentRunner` and validates its reply.

    Args:
        runner: Build 0's seam, already constructed with its adapter, prompts root
            and offline posture by the caller.
        adapter: The adapter id, carried for the review row rather than used here.
    """

    def __init__(self, runner: Runner, *, adapter: str | None) -> None:
        self._runner = runner
        self.adapter = adapter

    def run(
        self, prompt_ref: str, inputs: dict[str, Any], *, model: type[BaseModel]
    ) -> tuple[BaseModel, float, float]:
        """`(validated output, cost_usd, elapsed_s)`. Raises on an unusable reply."""
        result = self._runner.run(
            AgentRequest(
                skill_ref=prompt_ref,
                inputs=inputs,
                budget=Budget(
                    max_usd=MAP_AGENT_MAX_COST_USD,
                    max_wall_seconds=MAP_AGENT_MAX_WALL_S,
                ),
                # Keyed on the prompt and its inputs, so a re-run over an unchanged
                # tree replays the recorded run rather than paying for it twice --
                # and a tree that changed asks a genuinely new question.
                idempotency_key=_request_key(prompt_ref, inputs),
            )
        )
        elapsed_s = result.cost.wall_ms / _MS_PER_S
        if result.status != "ok" or not isinstance(result.output, dict):
            raise AdoptError(
                ErrorCode.AGENT_OUTPUT_SCHEMA,
                message=f"{prompt_ref} returned status {result.status!r} and no object",
                hint="`04` §5: a second schema failure aborts that prompt's "
                "contribution and leaves the deterministic output untouched. The map "
                "you already have is complete.",
            )
        try:
            return model.model_validate(result.output), result.cost.usd, elapsed_s
        except ValidationError as error:
            _log.error("agent_output_invalid", prompt=prompt_ref, errors=error.error_count())
            raise AdoptError(
                ErrorCode.AGENT_OUTPUT_SCHEMA,
                message=f"{prompt_ref} returned an object its schema rejects",
                hint="The reply parsed as JSON and is not the shape `04` §5 declares. "
                "A prompt whose replies keep failing its own schema is a prompt "
                "change, which is a new id -- never an edit (`00` §9 rule 3).",
            ) from error


#: Milliseconds per second. A unit, not a decision anybody may revise.
_MS_PER_S: Final[float] = 1000.0  # const-sync: ok -- a unit conversion


def _request_key(prompt_ref: str, inputs: dict[str, Any]) -> str:
    """A stable idempotency key for one prompt asked one way.

    Bounded well inside `IDEMPOTENCY_KEY_MAX_CHARS` by construction rather than by
    truncation, exactly as `adopt_detect.disambiguate` does: a key silently cut to
    a column width makes two different questions look like replays of each other.
    """
    digest = hashlib.sha256(json.dumps(inputs, **_JSON).encode("utf-8")).hexdigest()
    return f"{prompt_ref.replace('/', '-')}-{digest}"


def prompts_dir(default: Path | None = None) -> Path:
    """Where the four `map-*-001` prompt directories live.

    Deliberately the same resolution `adopt agent` uses, so one `ADOPT_PROMPTS_DIR`
    moves every prompt this CLI loads. Two roots would be two answers to "which
    prompt ran".
    """
    from adopt_cli.commands.agent import prompts_root

    return prompts_root() if default is None else default
