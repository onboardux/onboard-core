"""The six-condition pre-model gate, and the sample it hands a prompt -- `04` §2, §7.

**Nothing in this module calls a model.** It decides whether one may be called,
picks what would be sent, and meters what a pass may spend; `adopt_map.quarantine`
drives the call through the `GlueRunner` port. The split is deliberate: `04` §2
calls this *"the first thing reviewed in any PR touching this area"*, and a
reviewer can read the whole decision here without reading a provider round-trip.

**A refused pass is a successful run.** `04` §2's closing sentence -- *"A skipped
pass is a normal, successful run -- exit 0, complete deterministic output"* -- is
the reason `evaluate` returns a decision instead of raising. An exception would
have made every caller decide whether a skip is an error, and one of them would
eventually have decided yes.

**G-3 is implemented over presence, not over either ratio this pack offers, and
that is B1-CR-84.** `04` §2 G-3 reads *"at least one identity kind has
deterministic coverage below `MAP_PLUGIN_COVERAGE_FLOOR`"*, and this pack now
contains two different measurements that sentence could name -- **both of which
make the gate fire always**:

* `recompute_coverage()` coverage is **0.000 for every identity this build
  writes**, permanently, because Build 0's coverage rule requires an
  `audience_tag` that `00` §5 forbids Build 1 to write (B1-CR-62). Reading G-3
  that way means every kind is below every floor on every run.
* `01` §6 M2 -- `facts[method in {grammar, reflection}] / facts[*]` -- reads
  **0.000 for the `data` pack while its recall is 1.000**, because it measures the
  evidence rung and a dbt project is YAML a person wrote (B1-CR-78). Reading G-3
  that way calls a model about a family that was fully recovered.

A gate that always opens is not a gate, so neither reading is taken. G-3 asks the
question `04` §1 actually poses -- *"where deterministic extractors do not
reach"* -- over the kinds `MAP_STAGE1_REQUIRED_FAMILIES` names as constituting a
usable map: a required kind with **zero deterministic facts** scores `0.0` and is
unreached; one with any deterministic fact scores `1.0`. Both constants are used
for their stated purposes, and the condition is falsifiable in both directions --
which neither ratio was.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from adopt_const import (
    MAP_AGENT_MAX_COST_USD,
    MAP_AGENT_MAX_FILE_BYTES,
    MAP_AGENT_MAX_FILES_SAMPLED,
    MAP_AGENT_MAX_WALL_S,
    MAP_PLUGIN_COVERAGE_FLOOR,
    MAP_STAGE1_REQUIRED_FAMILIES,
)
from adopt_map.fileindex import FileIndex, read_text
from adopt_map.schemas.attributes import SECRET_NAMESPACE_PREFIX
from adopt_obs import AdoptError, ErrorCode, get_logger

__all__ = [
    "GATE_CONDITIONS",
    "NEVER_AGENT_KINDS",
    "AgentBudget",
    "GateDecision",
    "GateInputs",
    "SampledFile",
    "evaluate",
    "family_coverage",
    "select_samples",
]

_log = get_logger(__name__)

#: `04` §2's six conditions, in order, as the identifiers a skipped run records.
#: A tuple rather than six `if` statements' worth of string literals, so the
#: `05` S1.7 requirement of *"one test per gate condition"* can be parameterized
#: over the vocabulary itself and cannot silently stop covering one.
GATE_CONDITIONS: Final[tuple[str, ...]] = ("G-1", "G-2", "G-3", "G-4", "G-5", "G-6")

#: `04` §2 G-6's never-agent list. `ui_component` is Bet 1: a selector reaching a
#: URI is a P0 defect (B1-CR-11), and a model proposing `ui_component` extraction
#: is a model proposing exactly that. Secret namespaces are `02` §4.2's
#: no-value-field rule arriving one layer earlier -- there is nothing to sample
#: that is safe to send. Anything requiring client-code execution is `01` F7.2.
NEVER_AGENT_KINDS: Final[frozenset[str]] = frozenset({"ui_component"})

#: The `T0` tier, which is *"there is no engagement here"* rather than a degraded
#: mode (`adopt_detect.negotiate`). G-5's question -- may the files that would be
#: sampled be read at all -- is answered by `artifact_access`, and that is exactly
#: what separates `T0` from every other tier.
_NO_ARTIFACT_ACCESS: Final[frozenset[str | None]] = frozenset({None, "T0"})


@dataclass(frozen=True, slots=True)
class SampledFile:
    """One file's contribution to a prompt, already bounded and disclosed.

    `truncated` is carried rather than inferred from `len(text)`: a file that is
    exactly `MAP_AGENT_MAX_FILE_BYTES` long and one that was cut at that length
    are different facts about the repository, and `04` §7 requires the second to
    be *"head-truncate with a visible marker"* rather than silently equal to the
    first.
    """

    path: str
    text: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class GateInputs:
    """Everything the six conditions read, and nothing they do not.

    Assembled by the caller from the run it has already completed. A frozen
    record rather than six parameters because `04` §2 requires **all six** to
    hold, and a signature nobody can misorder is cheaper than a rule about
    ordering.
    """

    #: G-1, the flag half.
    agent_flag: bool
    #: G-1, the configuration half. `01` F12.1: both, neither alone.
    config_enabled: bool
    #: G-2. The deterministic pass completed and wrote its artifacts.
    deterministic_complete: bool
    #: G-3's numerator source: `identity_kind -> deterministic fact count`.
    facts_by_kind: Mapping[str, int]
    #: G-3's other arm: a family the operator named explicitly.
    requested_families: tuple[str, ...] = ()
    #: G-4. What the pass would have left to spend.
    budget: "AgentBudget | None" = None
    #: G-5. The negotiated tier, straight from `observability_boundary`.
    tier: str | None = None
    #: Carried for the prompts rather than for a condition: `04` §4.1 sends the
    #: archetype so triage can tell a Django tree from a dbt project, and the
    #: sandbox reconstructs the same context the deterministic pass used.
    archetype: str = "web"
    #: G-6. The kinds a candidate family would emit.
    target_kinds: tuple[str, ...] = ()
    #: G-6. The namespaces a candidate family would emit into.
    target_namespaces: tuple[str, ...] = ()
    #: G-6. Whether the family can only be read by running client code.
    requires_client_execution: bool = False


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Whether a model may be called, and -- when not -- exactly which condition said no.

    `failed` names one condition rather than collecting all of them. `04` §2 needs
    *all six* to hold, so the first refusal is sufficient and evaluating the rest
    would mean reading a tier and a budget the run has already been told not to
    spend.
    """

    allowed: bool
    failed: str | None = None
    detail: str | None = None
    unreached_families: tuple[str, ...] = ()

    def record(self) -> None:
        """Emit `agent_gate_skipped` with the failing condition. `04` §2.

        A structured event and **not** an error code: `02` §1.4 registers fourteen
        `MAP_*` codes and a skipped pass is not one of them, because it is not a
        failure. `01` F12's acceptance signal is about what a pass *writes*, and a
        pass that never ran wrote the deterministic map, correctly, at exit 0.
        """
        if self.allowed:
            return
        _log.info("agent_gate_skipped", condition=self.failed, gate_detail=self.detail)


@dataclass
class AgentBudget:
    """The `04` §7 budgets for one glue pass, and what exhausting one means.

    Mutable by design and the only mutable thing in this module: it is a meter.
    `spend` is called after each completed step rather than before, because a
    budget checked only in advance cannot notice the call that overran it.

    **Exhaustion is `MAP_AGENT_BUDGET_EXHAUSTED` and exit 6**, which `02` §8
    guarantees leaves *"deterministic artifacts and transaction intact"*. That is
    why this raises here rather than at the top of the run: everything
    deterministic has already been written by the time a pass may start.
    """

    max_cost_usd: float = MAP_AGENT_MAX_COST_USD
    max_wall_s: float = MAP_AGENT_MAX_WALL_S
    max_files: int = MAP_AGENT_MAX_FILES_SAMPLED
    spent_usd: float = 0.0
    elapsed_s: float = 0.0
    files_sampled: int = 0
    #: Set when a budget was hit, so a caller that catches the error can still
    #: report *which* one without parsing a message.
    exhausted_by: str | None = field(default=None)

    def has_headroom(self) -> bool:
        """G-4: is there room for a prompt's declared minimum -- one file, one call."""
        return (
            self.spent_usd < self.max_cost_usd
            and self.elapsed_s < self.max_wall_s
            and self.files_sampled < self.max_files
        )

    def spend(self, *, usd: float = 0.0, seconds: float = 0.0, files: int = 0) -> None:
        """Record what a step cost, then refuse the next one if a ceiling is past."""
        self.spent_usd += usd
        self.elapsed_s += seconds
        self.files_sampled += files
        for name, used, ceiling in (
            ("cost", self.spent_usd, self.max_cost_usd),
            ("wall_clock", self.elapsed_s, self.max_wall_s),
            ("files_sampled", float(self.files_sampled), float(self.max_files)),
        ):
            if used >= ceiling:
                self.exhausted_by = name
                raise AdoptError(
                    ErrorCode.MAP_AGENT_BUDGET_EXHAUSTED,
                    message=f"the glue pass exhausted its {name} budget",
                    hint="The deterministic map is complete and committed; this is "
                    "exit 6, a successful run with less output (`02` §8). Raise "
                    "the budget or narrow the family, then re-run with --agent.",
                )


def family_coverage(facts_by_kind: Mapping[str, int]) -> dict[str, float]:
    """Per-required-kind deterministic reach, in `[0.0, 1.0]`. See the module note.

    The denominator is `MAP_STAGE1_REQUIRED_FAMILIES` -- the constant whose stated
    consumer is *"defines 'usable map'"* -- and not every kind in the enum. A
    repository with no state machine should not summon a model to write a
    state-transition extractor for it, and a kind-enum denominator would say it
    should on every run of every archetype.
    """
    return {
        kind: (1.0 if facts_by_kind.get(kind, 0) > 0 else 0.0)
        for kind in MAP_STAGE1_REQUIRED_FAMILIES
    }


def _g6_refusal(inputs: GateInputs) -> str | None:
    """G-6's never-agent list, as a reason or `None`."""
    if inputs.requires_client_execution:
        return "the family can only be read by executing client code"
    for kind in inputs.target_kinds:
        if kind in NEVER_AGENT_KINDS:
            return f"{kind} is on the never-agent list"
    for namespace in inputs.target_namespaces:
        if namespace.startswith(SECRET_NAMESPACE_PREFIX):
            return "a secret namespace has no value a prompt may carry"
    return None


def evaluate(inputs: GateInputs) -> GateDecision:
    """`04` §2's six conditions, in order. All six hold, or no model is called."""
    if not (inputs.agent_flag and inputs.config_enabled):
        return GateDecision(
            allowed=False,
            failed="G-1",
            detail="--agent and agent.enabled are both required; neither alone",
        )
    if not inputs.deterministic_complete:
        return GateDecision(
            allowed=False,
            failed="G-2",
            detail="the deterministic pass has not completed",
        )

    coverage = family_coverage(inputs.facts_by_kind)
    unreached = tuple(
        sorted(kind for kind, value in coverage.items() if value < MAP_PLUGIN_COVERAGE_FLOOR)
    )
    if not unreached and not inputs.requested_families:
        return GateDecision(
            allowed=False,
            failed="G-3",
            detail="every required family was reached deterministically and no family was named",
        )

    if inputs.budget is None or not inputs.budget.has_headroom():
        return GateDecision(
            allowed=False,
            failed="G-4",
            detail="no budget remains for a prompt's declared minimum",
            unreached_families=unreached,
        )
    if inputs.tier in _NO_ARTIFACT_ACCESS:
        return GateDecision(
            allowed=False,
            failed="G-5",
            detail="the negotiated tier does not permit reading the files that would be sampled",
            unreached_families=unreached,
        )
    refusal = _g6_refusal(inputs)
    if refusal is not None:
        return GateDecision(
            allowed=False, failed="G-6", detail=refusal, unreached_families=unreached
        )
    return GateDecision(allowed=True, unreached_families=unreached)


def select_samples(
    index: FileIndex,
    *,
    limit: int = MAP_AGENT_MAX_FILES_SAMPLED,
    max_bytes: int = MAP_AGENT_MAX_FILE_BYTES,
) -> tuple[tuple[SampledFile, ...], int]:
    """The sample a prompt is given, and how many files were left out.

    **Sorted by path and by nothing else** (`04` §7). The temptation is to rank by
    size, by language or by "interestingness", and every one of those makes the
    sample a function of a heuristic that can change without anyone noticing --
    which would make two runs of the same tree send different evidence and give
    `04` §8's fixed-sample-order rule nothing to fix. Sorting by path is the only
    order the repository itself supplies.

    Truncation is **disclosed twice**: the count returned here reaches the review
    row, and each cut file carries a visible marker in its own text.
    """
    ordered = sorted(index.files, key=lambda entry: entry.path)
    chosen = ordered[:limit]
    samples: list[SampledFile] = []
    for entry in chosen:
        text = read_text(index, entry)
        truncated = len(text.encode("utf-8")) > max_bytes
        if truncated:
            text = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
            text = f"{text}\n[adopt: truncated at {max_bytes} bytes]"
        samples.append(SampledFile(path=entry.path, text=text, truncated=truncated))
    return tuple(samples), max(0, len(ordered) - len(chosen))


def sample_digest(samples: Sequence[SampledFile]) -> str:
    """A digest over what was sent, for the review row.

    The review row records *which* files were sampled; this records that their
    **contents** were the ones a reviewer is looking at. Without it, a reviewer
    comparing a quarantined module against the tree has no way to tell whether the
    tree moved underneath the run.
    """
    digest = hashlib.blake2b(digest_size=16)
    for sample in samples:
        digest.update(sample.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sample.text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
