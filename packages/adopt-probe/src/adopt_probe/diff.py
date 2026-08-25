"""What changed, named -- and never *the system changed* when the probe did.

The comparison is deterministic, pure, and does no I/O: two sets of recorded
observation outputs in, one verdict per step out. `difflib` is the whole of the
similarity machinery (sprint plan §5), because a probe's recorded output is short
text and a ratio over it is a number an FDE can argue with.

**The revision guard comes before every other comparison, and it is the reason
this build is worth having.** A baseline recorded against revision A and a run of
revision B are two different questions asked of the system, and the difference
between their answers is *the question changing*. Reporting that as drift would
be the tool's worst possible lie: it would send an FDE to look for a deployment
change that never happened, and after the second false alarm they stop looking at
all. So a revision mismatch is reported as `probe_changed`, with both revision
ids, and **no similarity number is computed at all** -- not computed and
suppressed, not computed. A number that is meaningless should not exist, because
a number that exists gets read.

**Structural differences are named as themselves.** A status that went 200 -> 500
is reported as a status change, not as a similarity of 0.71. The declared
invariants come first, in the order a human reads them, and the ratio is what is
left when nothing structural can explain the difference. A diagnosis nobody can
act on is not a diagnosis.

**Latency is absent here on purpose.** The sprint plan's ordering names it
alongside status and `json_fields`, and it is checked -- by the runner, against
`expect.latency_under_ms`, at the moment of the request, where it makes the run
`failure`. It is deliberately not in the recorded observation (see
`runner._canonical_output`): a millisecond of network variance in the
fingerprinted payload would make every rerun of every probe report drift, which
is v6.1 H5's false-staleness failure. So latency is enforced where it is
measurable and is not re-derived here, because there is nothing recorded to
re-derive it from.
"""

import difflib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from adopt_probe.baseline import Baseline
from adopt_probe.manifest import Expectation, Step

__all__ = [
    "DRIFT",
    "NO_BASELINE",
    "PROBE_CHANGED",
    "UNCHANGED",
    "WITHIN_THRESHOLD",
    "ProbeComparison",
    "StepComparison",
    "compare_outputs",
    "compare_run",
]

#: Byte-identical to the baseline.
UNCHANGED: Final[str] = "unchanged"
#: Different, but similar enough that the probe's own declared threshold accepts
#: it. **Reported, never hidden**: a reviewer watching a value creep toward the
#: threshold across a month is watching drift arrive, and a verdict that printed
#: nothing until the day it crossed would hide exactly that.
WITHIN_THRESHOLD: Final[str] = "within_threshold"
#: Different beyond the probe's declared tolerance.
DRIFT: Final[str] = "drift"
#: The run and the baseline are of different probe revisions. See the docstring.
PROBE_CHANGED: Final[str] = "probe_changed"
#: Nothing has been versioned for this probe yet.
NO_BASELINE: Final[str] = "no_baseline"

#: A step whose counterpart is absent. Only reachable across a revision boundary,
#: which the guard above already refuses -- or from a baseline written by
#: something that recorded a different number of steps than it ran.
_MISSING: Final[str] = "(no counterpart in the baseline)"
#: A step the baseline has and the run did not produce -- the probe stopped
#: getting an answer where it used to get one, which is a finding rather than a
#: gap in the report.
_UNOBSERVED: Final[str] = "the run recorded no output for this step; the baseline has one"


@dataclass(frozen=True, slots=True)
class StepComparison:
    """One step's verdict against its baseline counterpart."""

    index: int
    kind: str
    verdict: str
    #: `None` when no ratio was computed -- an unchanged step needs none, and a
    #: structural difference is named rather than scored.
    similarity: float | None = None
    #: What changed, in the words a reader can act on.
    detail: str | None = None

    @property
    def drifted(self) -> bool:
        return self.verdict == DRIFT


@dataclass(frozen=True, slots=True)
class ProbeComparison:
    """One probe's whole comparison: the verdict, and the steps behind it."""

    probe: str
    verdict: str
    baseline_id: str | None = None
    baseline_revision_id: str | None = None
    run_revision_id: str | None = None
    run_id: str | None = None
    steps: tuple[StepComparison, ...] = ()

    @property
    def drifted(self) -> bool:
        """**Only a same-revision comparison can drift.**

        `probe_changed` and `no_baseline` are answers, not findings: neither is
        evidence the system did anything, and neither may make `diff` exit 4.
        """
        return self.verdict == DRIFT


def _parsed(output: str) -> dict[str, Any]:
    """One recorded observation as its canonical mapping, or an empty one."""
    try:
        parsed = json.loads(output)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _payload_text(parsed: Mapping[str, Any], raw: str) -> str:
    """The part of an observation whose *text* is compared.

    The body of an HTTP response or the text of a model reply -- never the
    envelope. Comparing the whole canonical JSON would dilute every ratio with
    the constant keys around it, so two entirely different bodies would score
    similar because `{"kind":"http","status":200,"body":` is the same in both.
    """
    for key in ("body", "text"):
        value = parsed.get(key)
        if isinstance(value, str):
            return value
    return raw


def _normalized(text: str) -> str:
    """Whitespace-collapsed text, for the ratio only.

    Never applied to anything recorded or fingerprinted: an observation is kept
    exactly as it was observed. This normalization exists so that a response
    reflowed by a proxy is not reported as a behaviour change, and it is applied
    to both sides of one comparison or to neither.
    """
    return " ".join(text.split())


def _json_field_present(body: Any, path: str) -> bool:
    current = body
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return False
        current = current[segment]
    return True


def _structural_difference(
    baseline: Mapping[str, Any], current: Mapping[str, Any], expect: Expectation | None
) -> str | None:
    """The named difference, if a declared invariant explains the change.

    Read in the order a human reads them: the status first, because a status
    change explains everything below it, then the fields the probe declared it
    cares about. A body that merely reworded is not a structural difference and
    falls through to the ratio, which is the right instrument for it.
    """
    baseline_status = baseline.get("status")
    current_status = current.get("status")
    if baseline_status != current_status:
        return f"status {current_status} (the baseline recorded {baseline_status})"

    if expect is None or not expect.json_fields:
        return None

    try:
        current_body = json.loads(_payload_text(current, ""))
    except (ValueError, TypeError):
        return None
    try:
        baseline_body = json.loads(_payload_text(baseline, ""))
    except (ValueError, TypeError):
        baseline_body = None

    absent = [
        field
        for field in expect.json_fields
        if not _json_field_present(current_body, field)
        # Only a field the baseline *had* is a change. One the probe declares and
        # neither side ever carried is a probe that has been failing its own
        # invariant since it was written -- which the runner already reports as a
        # failed run, and reporting it here as well would say the system changed
        # when it has been this way all along.
        and (baseline_body is None or _json_field_present(baseline_body, field))
    ]
    if absent:
        return f"missing JSON field(s) {absent} that the baseline carried"
    return None


def compare_outputs(
    baseline_output: str,
    current_output: str,
    *,
    index: int,
    kind: str,
    expect: Expectation | None = None,
) -> StepComparison:
    """One step against its baseline counterpart. The whole comparison rule.

    Byte-equality first -- the cheap, exact answer, and the one that must not be
    reachable through a ratio: two identical strings score 1.0, but so do some
    that differ, and "unchanged" is a claim worth being exact about.
    """
    if baseline_output == current_output:
        return StepComparison(index=index, kind=kind, verdict=UNCHANGED, similarity=1.0)

    baseline_parsed = _parsed(baseline_output)
    current_parsed = _parsed(current_output)

    named = _structural_difference(baseline_parsed, current_parsed, expect)
    ratio = difflib.SequenceMatcher(
        None,
        _normalized(_payload_text(baseline_parsed, baseline_output)),
        _normalized(_payload_text(current_parsed, current_output)),
    ).ratio()

    if named is not None:
        return StepComparison(index=index, kind=kind, verdict=DRIFT, similarity=ratio, detail=named)

    threshold = expect.min_similarity if expect is not None else 0.0
    if ratio >= threshold:
        return StepComparison(
            index=index,
            kind=kind,
            verdict=WITHIN_THRESHOLD,
            similarity=ratio,
            detail=f"text differs, similarity {ratio:.4f} >= {threshold:.4f}",
        )
    return StepComparison(
        index=index,
        kind=kind,
        verdict=DRIFT,
        similarity=ratio,
        detail=f"text differs, similarity {ratio:.4f} < {threshold:.4f}",
    )


def compare_run(
    *,
    probe: str,
    baseline: Baseline | None,
    run_revision_id: str,
    run_id: str | None,
    outputs: Sequence[str],
    steps: Sequence[Step] = (),
) -> ProbeComparison:
    """One probe's run against its baseline.

    Args:
        probe: The probe's name, for the report.
        baseline: What the run is compared against. `None` reports
            `no_baseline`, which is an answer and not a finding.
        run_revision_id: The revision the run executed.
        run_id: The run, for the report.
        outputs: That run's observation outputs, in step order.
        steps: The revision's steps, whose `expect` blocks supply each
            comparison's declared threshold and fields. Empty is legal and means
            every step is compared on its text alone against a zero threshold --
            which reports differences and calls none of them drift, the honest
            reading of a probe that declared no tolerance.

    Returns:
        A `ProbeComparison`. Nothing is written and nothing is raised: a missing
        baseline is one probe's answer, and `run --all`'s sibling `diff --all`
        must report every probe rather than stopping at the first.
    """
    if baseline is None:
        return ProbeComparison(
            probe=probe, verdict=NO_BASELINE, run_revision_id=run_revision_id, run_id=run_id
        )

    if baseline.revision_id != run_revision_id:
        # The guard. No similarity is computed -- see the module docstring.
        return ProbeComparison(
            probe=probe,
            verdict=PROBE_CHANGED,
            baseline_id=baseline.id,
            baseline_revision_id=baseline.revision_id,
            run_revision_id=run_revision_id,
            run_id=run_id,
        )

    compared: list[StepComparison] = []
    # Over the union of both sides, not over the run: a run that stopped
    # producing a step the baseline has is a system that stopped answering, and
    # iterating the shorter side would report that as a clean comparison.
    for index in range(max(len(outputs), len(baseline.outputs))):
        current = outputs[index] if index < len(outputs) else None
        recorded = baseline.outputs[index] if index < len(baseline.outputs) else None
        step = steps[index] if index < len(steps) else None
        kind = "?" if step is None else step.kind
        if recorded is None:
            compared.append(StepComparison(index=index, kind=kind, verdict=DRIFT, detail=_MISSING))
            continue
        if current is None:
            compared.append(
                StepComparison(index=index, kind=kind, verdict=DRIFT, detail=_UNOBSERVED)
            )
            continue
        compared.append(
            compare_outputs(
                recorded,
                current,
                index=index,
                kind=kind,
                expect=None if step is None else step.expect,
            )
        )

    return ProbeComparison(
        probe=probe,
        verdict=DRIFT if any(step.drifted for step in compared) else UNCHANGED,
        baseline_id=baseline.id,
        baseline_revision_id=baseline.revision_id,
        run_revision_id=run_revision_id,
        run_id=run_id,
        steps=tuple(compared),
    )
