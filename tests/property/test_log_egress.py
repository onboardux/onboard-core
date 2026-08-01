"""No planted secret from the fixture tree reaches an emitted log line.

This is NFR N11 as an executable property, and it is a **permanent
zero-violation gate**: the promise "no client source, item body, prompt text or
model output ever reaches a log line or telemetry payload" is one this product
makes to a client's security reviewer, and a promise that is only spot-checked
is a promise nobody should have made.

Defect sentence: *fails when* any planted secret appears in an emitted line for
any payload shape. *Matters because* the offline and no-content-leaves-the-
environment claims are the product's security posture, and one leak invalidates
them retroactively for every store already written. *No other instrument catches
it because* a unit test asserts one shape, while the leak arrives through the
shape nobody thought of.
"""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from adopt_obs import DENIED_FIELDS, Logger, ManualClock, redact
from adopt_obs.clock import Clock

FIXTURE_TREE = Path(__file__).resolve().parent.parent / "fixtures" / "secrets"

SAFE_KEYS = ["path", "bytes", "count", "digest", "rule", "archetype", "elapsed_ms"]


def _planted_secrets() -> list[str]:
    """Every planted secret in the tree.

    Globbed rather than enumerated: a new fixture file strengthens this
    property without anyone remembering to update the test.
    """
    secrets: list[str] = []
    for path in sorted(FIXTURE_TREE.glob("*.txt")):
        secrets += [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    return secrets


SECRETS = _planted_secrets()


@pytest.mark.property
def test_the_fixture_tree_is_not_empty() -> None:
    """Guard the guard: an empty tree would make the property vacuously true.

    Marked, because an unmarked test is selected by neither the `unit` job nor
    the `property` job and therefore runs nowhere in CI -- which would leave the
    one assertion protecting the planted-secret corpus watching from outside the
    building.
    """
    assert len(SECRETS) >= 6, "the planted-secret fixture tree lost its fixtures"


def _bury(secret: str, depth: int, key: str) -> dict[str, object]:
    payload: dict[str, object] = {key: secret}
    for level in range(depth):
        payload = {f"level_{level}": payload}
    return payload


@st.composite
def _payload_with_planted_secrets(draw: st.DrawFn) -> tuple[dict[str, object], int]:
    chosen = draw(st.lists(st.sampled_from(SECRETS), min_size=1, max_size=4, unique=True))
    payload: dict[str, object] = {
        draw(st.sampled_from(SAFE_KEYS)): draw(st.integers(min_value=0, max_value=10**6))
    }
    for index, secret in enumerate(chosen):
        denied_key = draw(st.sampled_from(sorted(DENIED_FIELDS)))
        depth = draw(st.integers(min_value=0, max_value=5))
        payload[f"branch_{index}"] = _bury(secret, depth, denied_key)
    return payload, len(chosen)


class _Sink:
    """A stand-in for the log sink that records everything written to it."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.lines.append(text)

    def flush(self) -> None:
        return None


def _emit_capturing(payload: dict[str, object], clock: Clock) -> str:
    """Emit one line through the real Logger, capturing the exact bytes written.

    Uses the production `Logger` with an injected sink rather than
    reimplementing its assembly: a property test that rebuilds the thing it is
    testing proves only that the test is self-consistent.
    """
    sink = _Sink()
    Logger("egress", "run_TEST", clock, sink).info("fixture.processed", **payload)
    return "".join(sink.lines)


@pytest.mark.property
@given(case=_payload_with_planted_secrets())
@settings(max_examples=250, deadline=None)
def test_no_planted_secret_reaches_an_emitted_line(case: tuple[dict[str, object], int]) -> None:
    import datetime as dt

    payload, planted = case
    line = _emit_capturing(payload, ManualClock(dt.datetime(2026, 7, 30, tzinfo=dt.UTC)))

    for secret in SECRETS:
        assert secret not in line, f"planted secret escaped into a log line: {payload!r}"
    assert f'"redacted_fields": {planted}' in line, (
        "the drop was not counted; a silent drop is indistinguishable from a caller "
        "that never passed the field"
    )


@pytest.mark.property
@given(case=_payload_with_planted_secrets())
@settings(max_examples=100, deadline=None)
def test_redaction_is_idempotent(case: tuple[dict[str, object], int]) -> None:
    """Redacting an already-redacted payload drops nothing further.

    If it dropped more on a second pass, the first pass had left something
    behind -- which is the leak this suite exists to prevent, arriving one
    round-trip late.
    """
    payload, _ = case
    once = redact(payload)
    twice = redact(once.value)

    assert twice.dropped == 0
    assert twice.value == once.value
