"""`adopt probe` -- the Behaviour Baseline. Build 5, v6.1 §6.

The mystery shopper for delivered systems, AI systems foremost: systems whose
behaviour changes without a commit. Builds 1-4 capture what the repository
*says*; this package observes what the system *does*.

**The invariants this package carries**, each of them structural rather than
remembered:

1. **Only `adopt_probe.runner` performs probe I/O.** Enforced by the `probe-io`
   import contract and proven by `scripts/plant_violation.py --kind probe-io`.
   This is the whole safety argument for dropping v4's sandbox apparatus (v6.1
   D2, upheld by the F8 audit): a probe carries no executable content, and the
   one module that can reach the network is the one module that checks the
   allow-list.
2. **The host allow-list is enforced at connection time**, not merely at `add`.
   `add`'s check is a courtesy; the runner's is the invariant, because a revision
   can arrive without passing through `add`.
3. **Secrets resolve by reference and never appear in an observation.**
   `probe_observation.output` is an exportable column, so a leaked secret would
   travel in the client's bundle forever.
4. **A probe declares its own budgets and the runner enforces all of them** --
   wall clock, request count, and model spend through the existing seam's meter
   rather than a second implementation of one.
5. **Nothing here judges cleanup.** v6.1 F8 states the residual explicitly:
   `probe_run.cleanup_verified` is written `false` and verified by nobody until
   the first real probe class whose safe path performs writes.

**This package holds no dialect and opens no store.** `ProbeRunRecords` is
declared in `ports` and realized in `adopt-store`, which is the CR-34/CR-37
pattern `adopt_export` established -- and the reason `adopt-store` is absent from
this package's dependencies is that importing it would pull `sqlite3` into the
graph and break `no-raw-sqlite`.
"""

from adopt_probe.baseline import (
    BASELINE_ELIGIBLE_OUTCOMES,
    Baseline,
    baseline_from_row,
    build_baseline,
    canonical_recorded_output,
    split_recorded_output,
)
from adopt_probe.conflict import ConflictIntent, conflicting_intents
from adopt_probe.diff import (
    DRIFT,
    NO_BASELINE,
    PROBE_CHANGED,
    UNCHANGED,
    WITHIN_THRESHOLD,
    ProbeComparison,
    StepComparison,
    compare_run,
)
from adopt_probe.manifest import (
    HttpStep,
    ProbeSpec,
    PromptStep,
    Step,
    load_probe,
    parse_probe,
    render_interaction,
)
from adopt_probe.ports import ProbeRunRecords, SensorSink
from adopt_probe.runner import ProbeOutcome, RunReport, StepResult, execute_probe, summarize

__all__ = [
    "BASELINE_ELIGIBLE_OUTCOMES",
    "DRIFT",
    "NO_BASELINE",
    "PROBE_CHANGED",
    "UNCHANGED",
    "WITHIN_THRESHOLD",
    "Baseline",
    "ConflictIntent",
    "HttpStep",
    "ProbeComparison",
    "ProbeOutcome",
    "ProbeRunRecords",
    "ProbeSpec",
    "PromptStep",
    "RunReport",
    "SensorSink",
    "Step",
    "StepComparison",
    "StepResult",
    "baseline_from_row",
    "build_baseline",
    "canonical_recorded_output",
    "compare_run",
    "conflicting_intents",
    "execute_probe",
    "load_probe",
    "parse_probe",
    "render_interaction",
    "split_recorded_output",
    "summarize",
]
