"""What "how the system behaves today" is written down as, and read back from.

A baseline is one `baseline_version` row per probe revision. It holds the
observations of one run, rendered once, so that a later run can be compared
against it without re-reading that run -- and so that the comparison is a
function of two strings rather than of a join somebody has to get right twice.

**The recorded output is a JSON array of the run's observation outputs, in step
order**, rendered with the export writer's discipline (sorted keys, no spaces).
An array rather than a concatenation, and that is the whole design of this
module: `diff` compares **per step**, so the stored form has to be splittable
back into steps. A concatenation would either need a separator that cannot occur
in a body -- there is none -- or would force every comparison to be whole-run,
which reports "something changed" about a probe with six steps and names none of
them.

**Only a run that observed something may become a baseline.** `success` and
`diff` qualify; `failure` and `blocked_by_manifest` never do. A failed run
recorded a transport error or a violated invariant, and versioning that as *how
the system behaves* would make the next clean run read as drift away from a
fault. A `diff` run qualifies **because a human typed `--set`**: re-baselining
after drift is exactly what the verb is for, and the report names the outcome it
took so the human can see they accepted a change rather than confirmed a state.

**There is no `approved_by`.** The sprint plan's interface names one, and
`baseline_version` has no such column -- the schema carries `approved_at` alone,
and v6.1 §8's budget for Builds 1-10 (one table, one column) was spent before
this build. The actor is not silently dropped: `--set` is a human act at a
terminal, the run it versioned is identified by its own row, and the row that
would hold the name does not exist. Adding a column to record it would be a
schema change this build is explicitly forbidden from making.
"""

import datetime as _dt
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from adopt_model import BaselineVersion, ProbeObservation, ProbeRun
from adopt_obs import new_id

__all__ = [
    "BASELINE_ELIGIBLE_OUTCOMES",
    "Baseline",
    "baseline_from_row",
    "build_baseline",
    "canonical_recorded_output",
    "fingerprint_of",
    "split_recorded_output",
]

#: The run outcomes `baseline --set` may version. See the module docstring.
BASELINE_ELIGIBLE_OUTCOMES: frozenset[str] = frozenset({"success", "diff"})


@dataclass(frozen=True, slots=True)
class Baseline:
    """A baseline as the runner and the differ consume it.

    `revision_id` travels with it deliberately. A baseline recorded against one
    probe revision says nothing about a run of a *different* revision, and the
    only way to refuse to confuse the two is for both sides to carry the
    revision they belong to. Every consumer compares them before comparing
    anything else.
    """

    id: str
    revision_id: str
    outputs: tuple[str, ...]


def canonical_recorded_output(outputs: Sequence[str]) -> str:
    """The run's observations as one string, splittable back into steps.

    Sorted keys and no spaces are inherited from `adopt_export`'s rendering
    discipline for its reason: two baselines of the same observations must be
    the same bytes, or the fingerprint means nothing.
    """
    return json.dumps(list(outputs), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def split_recorded_output(recorded: str | None) -> tuple[str, ...]:
    """The step outputs a baseline holds.

    A baseline written by something that stored a bare string -- an older
    binary, a hand-edited row -- yields a single step rather than raising: the
    comparison it supports is degraded, not impossible, and refusing to diff
    at all would be a worse answer than diffing one step.
    """
    if not recorded:
        return ()
    try:
        parsed = json.loads(recorded)
    except (ValueError, TypeError):
        return (recorded,)
    if isinstance(parsed, list) and all(isinstance(entry, str) for entry in parsed):
        return tuple(parsed)
    return (recorded,)


def fingerprint_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def baseline_from_row(row: BaselineVersion) -> Baseline:
    """The stored row as the value the runner and differ use."""
    return Baseline(
        id=row.id,
        revision_id=row.probe_definition_revision_id,
        outputs=split_recorded_output(row.recorded_output),
    )


def build_baseline(
    run: ProbeRun,
    observations: Sequence[ProbeObservation],
    *,
    environment_id: str,
    now: _dt.datetime,
    redaction_policy: str | None = None,
    model_provider_version: str | None = None,
) -> BaselineVersion:
    """One `baseline_version` row from one run's observations.

    Args:
        run: The run being versioned. Already checked eligible by the caller --
            this function builds a row and judges nothing, so that the
            eligibility rule lives in exactly one place with a test on it.
        observations: That run's observations, in step order.
        environment_id: The environment the probe ran against.
        now: `created_at` and `approved_at`. Injected rather than read, because
            a module that reached for a clock could not be tested for the one
            property that matters here -- that two baselines of the same run
            differ only in their id and their timestamps.
        redaction_policy: Copied from the probe's manifest, so a baseline states
            the policy its recorded text was redacted under. A comparison
            against a baseline captured under a different policy is comparing
            two different renderings of the same behaviour, and the field is
            what lets a later build notice.
        model_provider_version: The adapter that answered a `prompt` step, when
            one ran. `None` for a probe with only `http` steps -- there was no
            model, and naming one would be inventing an environment fact.

    Returns:
        The row, unwritten. Persisting it is the caller's, in its transaction.
    """
    recorded = canonical_recorded_output([row.output or "" for row in observations])
    return BaselineVersion(
        id=new_id("bv"),
        probe_definition_revision_id=run.probe_definition_revision_id,
        environment_id=environment_id,
        model_provider_version=model_provider_version,
        # v1 fills what it knows and leaves the rest NULL. A fixture version and
        # a feature-flag set are facts about the client's deployment that nothing
        # in this build observes, and writing a placeholder into an exportable
        # column would put a claim we cannot support into the client's bundle.
        retrieval_dataset_version=None,
        tool_schema_version=None,
        feature_flags=None,
        judge_model=None,
        judge_policy=None,
        fixture_version=None,
        redaction_policy=redaction_policy,
        recorded_output=recorded,
        fingerprint=fingerprint_of(recorded),
        created_at=now,
        # `--set` *is* the approval. A baseline written by a human running this
        # verb is approved at the moment they ran it; there is no second step
        # and no pending state, so leaving this NULL would record an approval
        # queue that does not exist.
        approved_at=now,
    )
