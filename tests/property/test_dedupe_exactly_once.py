"""For any interleaving of crash points, a deduped effect commits exactly once.

*Fails when* a crash between two durable writes makes an effect happen twice or
not at all. *Matters because* at-least-once step execution is the guarantee the
engine actually offers, and "exactly once" for the *effect* is what turns that
into something a client can be billed against. *No other instrument catches it
because* the kill-and-resume drill fixes one crash point -- the interesting one --
and this asserts the claim over every point at which a run can die.

**Crashes are raised, not simulated by a mock.** A `BaseException` escapes the
retry loop and `_drive`'s `except AdoptError` exactly as a killed process leaves
a run: the journal keeps whatever was already durable, the run stays
non-terminal, and nothing gets a chance to clean up. That is the same state the
process-killing drill produces, reached cheaply enough to run hundreds of times.

**Both phases are generated.** Crashing *before* an effect commits is the easy
half -- the resumed run simply commits it. Crashing *after* is the half where a
backend that writes its dedupe marker at the wrong moment duplicates the effect,
and a property that only generated the first phase would pass against exactly
that bug.
"""

import contextlib
import tempfile
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adopt_workflow import (
    InProcessWorkflowClient,
    Journal,
    RetryPolicy,
    clear_registry,
    step,
    workflow,
)

pytestmark = pytest.mark.property

#: Three effects in one run, so the property covers a crash between two of them
#: and not only at the boundaries of a single-step workflow.
EFFECT_KEYS: Final[tuple[str, ...]] = ("charge", "notify", "record")

#: Crash instructions the steps consume, as `(step_index, phase)`. Module-level
#: because a step is resolved by name after a "crash" and has no closure left --
#: the same constraint the real drill's child process is under.
_CRASH_POINTS: list[tuple[int, str]] = []


class _Crash(BaseException):
    """A process death, not an error. `BaseException` so nothing catches it."""


def _maybe_crash(index: int, phase: str) -> None:
    if (index, phase) in _CRASH_POINTS:
        _CRASH_POINTS.remove((index, phase))
        raise _Crash(f"crash at step {index} {phase} the effect")


def _register() -> Any:
    """Declare the workflow and its three steps. Called once per example."""

    def make(index: int) -> Any:
        @step(name=f"effect-{index}", retries=RetryPolicy(max_attempts=1))
        def run_effect(ctx: Any) -> str:
            _maybe_crash(index, "before")
            first = ctx.dedupe(EFFECT_KEYS[index])
            _maybe_crash(index, "after")
            return "first" if first else "replayed"

        return run_effect

    steps = [make(index) for index in range(len(EFFECT_KEYS))]

    @workflow(name="three-effects")
    def flow(ctx: Any, args: dict[str, Any]) -> list[str]:
        return [str(ctx.step(one)) for one in steps]

    return flow


def _effect_counts(journal_dir: Path) -> dict[str, int]:
    journal = Journal(journal_dir / "workflow.ndjson")
    counts: dict[str, int] = dict.fromkeys(EFFECT_KEYS, 0)
    for record in journal.records():
        if record.get("type") == "effect":
            key = str(record.get("key"))
            if key in counts:
                counts[key] += 1
    return counts


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    crashes=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=len(EFFECT_KEYS) - 1),
            st.sampled_from(["before", "after"]),
        ),
        max_size=6,
        unique=True,
    )
)
def test_a_deduped_effect_commits_exactly_once_across_any_crash_interleaving(
    crashes: list[tuple[int, str]],
) -> None:
    clear_registry()
    _CRASH_POINTS[:] = crashes
    flow = _register()

    with tempfile.TemporaryDirectory() as raw:
        journal_dir = Path(raw)
        client = InProcessWorkflowClient(journal_dir)

        # Start, then recover as many times as there are crash points to consume.
        # Bounded rather than `while`: an unbounded loop against a backend that
        # never made progress would hang instead of failing.
        with contextlib.suppress(_Crash):
            client.start(flow, {}, idempotency_key="k")
        for _ in range(len(crashes) + 1):
            if not client.list(status="running"):
                break
            try:
                client.recover()
            except _Crash:
                continue

        assert not _CRASH_POINTS, "the run finished without consuming every crash point"

        handles = client.list()
        assert len(handles) == 1
        assert handles[0].status == "completed"

        counts = _effect_counts(journal_dir)
        assert counts == dict.fromkeys(EFFECT_KEYS, 1), (
            f"crashes={crashes} produced {counts}, not one commit per effect"
        )

    clear_registry()
