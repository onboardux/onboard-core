"""The recorded fake. AI spec §2, kind `test`.

**This is the adapter the conformance suite can always run**, and the one that
makes the suite a test of *the contract* rather than of a model. It replays
recorded turns in order and opens no connection of any kind.

**It is typed `test`, not `local`,** and that is deliberate: `conformance-matrix`
requires at least one *local* adapter green, and a fake counting as local would
let a pipeline exercising no real model satisfy the gate whose whole purpose is
to prove one was exercised.

**It fails loudly when it runs out of recorded turns.** A fake that returned an
empty response after its script ended would make a tool loop that ran one turn
too long look like a passing test.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from adopt_agent.api import AdapterResponse, ToolSpec
from adopt_obs import AdoptError, ErrorCode

__all__ = ["FakeRecordedAdapter", "build"]

#: A fake has no real model. The value is a label that appears in traces and in
#: `agent_run.model`, and it is deliberately not a real model identifier -- a
#: recorded run must never be mistaken in an audit for a run against a provider.
_RECORDED_MODEL: Final[str] = "recorded"


class FakeRecordedAdapter:
    """Replays `turns` in order. Realizes `api.Adapter` structurally."""

    id: str = "fake_recorded"
    kind: str = "test"

    def __init__(self, turns: Sequence[AdapterResponse] | None = None) -> None:
        self._turns = list(turns or [])
        self._index = 0
        #: Every call is counted so a test can assert **zero** provider calls on
        #: an idempotent replay (AI spec §7.1 case 12). Counting is the only way
        #: that claim is checkable: a replay that returned the right answer
        #: *after* calling the provider looks identical from the outside.
        self.calls = 0

    def model(self) -> str:
        return _RECORDED_MODEL

    def params_hash(self) -> str:
        # A fake sends nothing, so there are no parameters to hash. A stable
        # constant keeps `Trace.params_hash` non-empty, which the model requires
        # and case 11 asserts.
        # const-sync: ok -- a SHA-256 hexdigest is 64 characters, not SKILL_NAME_MAX_CHARS.
        return "0" * 64

    def complete(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolSpec],
        tool_results: list[Mapping[str, Any]],
        max_tokens: int | None,
    ) -> AdapterResponse:
        self.calls += 1
        if self._index >= len(self._turns):
            raise AdoptError(
                ErrorCode.AGENT_PROVIDER_ERROR,
                message=(
                    f"the recorded fake has no turn {self._index + 1}; "
                    f"{len(self._turns)} were recorded"
                ),
                hint=(
                    "The seam asked for more turns than the fixture records. Either the "
                    "loop ran longer than the case intends, or the fixture is short."
                ),
            )
        turn = self._turns[self._index]
        self._index += 1
        return turn


def build(*, model: str | None, endpoint: str | None) -> FakeRecordedAdapter:
    """Registry entry point. `endpoint` is the recorded fixture file.

    AI spec §2 gives this adapter's Endpoint as "recorded fixtures", and this is
    what that means in code: a JSON file holding the turns to replay, in order.
    Reading the script from a file rather than from module state is what lets the
    conformance suite parameterize over adapters at all -- a fake seeded by
    reaching into its module would need a different construction path from every
    other adapter, and the suite would stop being one suite.
    """
    if endpoint is None:
        return FakeRecordedAdapter()
    raw = json.loads(Path(endpoint).read_text(encoding="utf-8"))
    return FakeRecordedAdapter([AdapterResponse.model_validate(turn) for turn in raw["turns"]])
