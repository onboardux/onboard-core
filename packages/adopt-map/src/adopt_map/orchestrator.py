"""The run lifecycle: staging, budget, the one transaction -- `03` §4, §5.9.

`03` §4's layout names this module and **no sprint checkbox does**. It is the
piece S1.3's staged emission and budget items cannot exist without, so it lands
here rather than being spread through the CLI: `05` S1.1 already recorded that
`adopt map`'s command module is *"composition and nothing else"*, and a lifecycle
living in a command is a lifecycle no test can drive without a process.

**The order is load-bearing, and each step is a different promise:**

1. **One walk** (`fileindex`). Every extractor sees the same tree, so `01` N4's
   determinism is a property of the run and not of which extractors ran.
2. **Plan** (`plugins`). Enabled pack, matching archetype, applicable, and no
   capability above the negotiated tier -- with every exclusion *recorded*, since
   `01` F13.5 makes a skip a stated reason rather than a silent omission.
3. **Stage 1, then emit** (`01` F11.2). The families in
   `MAP_STAGE1_REQUIRED_FAMILIES` run first and `surface.md` is written **before
   the remaining families are processed** -- `03` §5.9 invariant 1, and the reason
   the north-star metric can be measured at all.
4. **Stage 2.**
5. **One transaction** (`01` F3.3). Extraction happens entirely before the write,
   so a crash during extraction leaves the store byte-identical, and the write
   itself is atomic.
6. **Coverage** (`01` F10). Recompute is the authority; the cache is rebuilt from
   it and the comparison happens before the rebuild.
7. **Emit everything.**

**Budget exhaustion is a successful run with less output** (`02` §8 exit 3). The
transaction still commits what completed and `truncated_families[]` names what is
missing. A partial map that claimed completeness would be worse than no map;
a partial map that refused to commit would throw away work a client paid for in
wall-clock time.

**The egress guard wraps extraction and nothing else.** The write path talks to a
local file, and wrapping it would put the guard between us and a store we opened
ourselves. `01` N7's claim is about what *client-facing analysis* does.
"""

import datetime as _dt
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from adopt_const import (
    MAP_STAGE1_BUDGET_S,
    MAP_STAGE1_REQUIRED_FAMILIES,
    MAP_TOTAL_BUDGET_S,
)
from adopt_coverage import CacheWriter
from adopt_coverage.records import CoverageRecords
from adopt_map.confidence import Degradation, LadderPolicy, with_counts
from adopt_map.context import Budget, ExtractorContext
from adopt_map.coverage import CoverageReport, report_coverage
from adopt_map.emit import (
    SURFACE_JSON_NAME,
    SURFACE_MD_NAME,
    render_d2,
    render_markdown,
    render_mermaid,
    render_stage1,
    render_surface_json,
)
from adopt_map.emit.d2 import D2_NAME
from adopt_map.emit.mermaid import MERMAID_NAME
from adopt_map.execseam import tool_available
from adopt_map.fileindex import FileIndex, build_index, detect_language, is_code
from adopt_map.netguard import EgressGuard, guarded
from adopt_map.plugins import ExtractorRegistry
from adopt_map.report import RunResult, peak_rss_bytes, write_run_report
from adopt_map.scheduler import ExtractorOutcome, ScheduleResult, run_all
from adopt_map.schemas.surface import EvidenceMethod, Extractor
from adopt_map.scope_resolve import ResolvedScope
from adopt_map.writer import FactBatch, SurfaceWriter, SurfaceWriteResult
from adopt_obs import (
    Clock,
    MapExitCode,
    SystemClock,
    get_logger,
    new_run_id,
    truncate_to_millisecond,
)

__all__ = ["DEFAULT_FORMATS", "DEFAULT_OUT_DIR", "RunPlan", "run"]

_log = get_logger(__name__)

#: `02` §8's `--out` default and `--format` default.
DEFAULT_OUT_DIR: Final[str] = ".adopt/out"
DEFAULT_FORMATS: Final[tuple[str, ...]] = ("md", "json", "mermaid")

#: The same default rendered as `--format`'s literal. A module-level string
#: rather than a `join` in the signature, because a function call in an
#: argument default is evaluated once at import and would leave the CLI's
#: default frozen to whatever `DEFAULT_FORMATS` held at that moment.
DEFAULT_FORMAT_FLAG: Final[str] = ",".join(DEFAULT_FORMATS)

#: The family the ladder is about. `01` F9.2 runs the ladder *"per identity
#: family per language"*, and `symbol` is the family whose recovery is bound to a
#: language grammar: an `endpoint` comes from a route table, a `db_field` from a
#: migration, and neither degrades because a language grammar is missing.
_LANGUAGE_BOUND_KIND: Final[str] = "symbol"


@dataclass(frozen=True, slots=True)
class RunPlan:
    """Which extractors will run, which will not, and why not."""

    stage1: tuple[Extractor, ...]
    stage2: tuple[Extractor, ...]
    skipped: tuple[tuple[str, str], ...]

    @property
    def all(self) -> tuple[Extractor, ...]:
        return (*self.stage1, *self.stage2)


def _is_stage1(extractor: Extractor) -> bool:
    """Whether an extractor covers a `MAP_STAGE1_REQUIRED_FAMILIES` family.

    `01` §6's north star counts stage-1 *"only when stage-1 covers every required
    family actually present"*, so the partition is by declared kinds: an
    extractor that could contribute a required family runs in the first stage
    even if this particular tree turns out to have none of it.
    """
    return bool(set(extractor.manifest().kinds) & set(MAP_STAGE1_REQUIRED_FAMILIES))


def plan_run(
    registry: ExtractorRegistry, *, archetype: str, root: str, tier: str | None
) -> RunPlan:
    """Partition the registry into the two stages, and record every exclusion."""
    selected = registry.plan(archetype=archetype, root=root, tier=tier)
    return RunPlan(
        stage1=tuple(extractor for extractor in selected if _is_stage1(extractor)),
        stage2=tuple(extractor for extractor in selected if not _is_stage1(extractor)),
        skipped=registry.skipped(archetype=archetype, root=root, tier=tier),
    )


#: Rung order within a stage -- `01` F9.2. `reflection` and `declared` sit with
#: `grammar` because they are not ladder steps: an OpenAPI document either exists
#: or it does not, and there is no degrading *into* reading one. What the ladder
#: is about is `grammar -> ctags -> regex`, and this tuple is that order with the
#: non-ladder methods placed above it.
_RUNG_ORDER: Final[tuple[tuple[str, ...], ...]] = (
    ("grammar", "reflection", "declared", "agent"),
    ("ctags",),
    ("regex",),
)


def _run_stage(
    extractors: Sequence[Extractor], ctx: ExtractorContext, *, sequential: bool
) -> ScheduleResult:
    """Run one stage **rung by rung**, widening the covered-language set between.

    This is the ladder doing something rather than reporting something. `01` F9.2
    says it *"runs strictly grammar -> ctags -> regex -> decline, per identity
    family per language"*, and a scheduler that ran every rung concurrently would
    make that sentence describe a report rather than a behaviour.

    **The cost of getting it wrong is not wasted work.** Two rungs over one
    language mint the *same URI* for one referent -- `common.regex` and a grammar
    extractor both key a Python symbol `<module>.<name>` under namespace
    `python` -- with different digests, because a grammar rung records a signature
    and a regex rung honestly records none. That is an identity fork inside a
    single run, and it was found by running the S1.2 rename drill through the real
    pack rather than through a stub.

    Outcomes are merged and re-sorted by extractor id, so the stage's result is
    still `02` §7 obligation 3's deterministic order regardless of how many rungs
    it took.
    """
    by_method: dict[str, list[Extractor]] = {
        method: [] for group in _RUNG_ORDER for method in group
    }
    ordered: list[Extractor] = []
    for extractor in extractors:
        method = extractor.manifest().method
        if method in by_method:
            by_method[method].append(extractor)
        else:  # pragma: no cover -- `EvidenceMethod` is closed and fully covered
            ordered.append(extractor)

    outcomes: list[ExtractorOutcome] = []
    enforced = True
    covered: frozenset[str] = ctx.covered_languages
    for group in _RUNG_ORDER:
        rung = [extractor for method in group for extractor in by_method[method]]
        if not rung:
            continue
        result = run_all(rung, ctx.at_rung(covered), sequential=sequential)
        outcomes.extend(result.outcomes)
        enforced = enforced and result.timeout_enforced
        covered = covered | _languages_recovered(rung, result)

    return ScheduleResult(
        outcomes=tuple(sorted(outcomes, key=lambda outcome: outcome.extractor_id)),
        timeout_enforced=enforced,
    )


def _languages_recovered(extractors: Sequence[Extractor], result: ScheduleResult) -> frozenset[str]:
    """Languages this rung actually recovered `symbol` facts from.

    Recovered, not *attempted*: a rung that ran and found nothing in Kotlin has
    not covered Kotlin, and the next rung must still try. That is the difference
    between the ladder and a list of which extractors were enabled.
    """
    manifests = {extractor.manifest().id: extractor.manifest() for extractor in extractors}
    languages: set[str] = set()
    for outcome in result.outcomes:
        if outcome.extractor_id not in manifests:  # pragma: no cover -- defensive
            continue
        for fact in outcome.facts:
            if fact.identity_kind != _LANGUAGE_BOUND_KIND:
                continue
            for reference in fact.source_refs[:1]:
                language = detect_language(reference.path)
                if language:
                    languages.add(language)
    return frozenset(languages)


def _grammar_languages(index: FileIndex, batches: Sequence[FactBatch]) -> frozenset[str]:
    """Which languages a **grammar-method** extractor actually recovered symbols from.

    Derived from the run rather than declared in a table, and that is what keeps
    the ladder from being vacuous in both directions. A hard-coded list of
    supported grammars would go stale the moment a pack was disabled; asking what
    the run produced answers the question the ladder is actually about -- *was
    grammar-level evidence available for this language, here, today?*

    A fact's language is its first source ref's file extension. A fact with no
    source ref is not language-bound and contributes to nothing here.
    """
    del index
    languages: set[str] = set()
    for batch in batches:
        if batch.manifest.method != "grammar":
            continue
        for fact in batch.facts:
            if fact.identity_kind != _LANGUAGE_BOUND_KIND:
                continue
            for reference in fact.source_refs[:1]:
                language = detect_language(reference.path)
                if language:
                    languages.add(language)
    return frozenset(languages)


def _degradations(index: FileIndex, batches: Sequence[FactBatch]) -> tuple[Degradation, ...]:
    """Every ladder transition this run took, with the file counts stamped on.

    One row per language present in the tree that **no grammar-method extractor
    recovered symbols from**. That is the case `01` F9's acceptance signal names:
    *"Removing a grammar forces the ladder down, the output says so, and nothing
    from that language claims grammar-level confidence."*

    `affected` is the number of indexed files in that language -- what we could
    not read at grammar level -- rather than a fact count, because the honest
    magnitude of a missing grammar is how much source it left unread.

    **Stated limit.** A language present in the tree degrades and says so; a
    *kind* that no extractor covers at all is not a ladder transition and is not
    reported here. That is `01` F9.3's gap path, and it becomes reachable when
    S1.4's grammar extractors exist and can decline a family they were asked for.
    """
    ctags_present = tool_available("ctags")
    covered = _grammar_languages(index, batches)

    def available(method: EvidenceMethod, language: str | None) -> bool:
        if method == "grammar":
            return language is not None and language in covered
        if method == "ctags":
            return ctags_present
        return method == "regex"

    policy = LadderPolicy(available)
    counts: dict[tuple[str, str | None], int] = {}
    transitions: list[Degradation] = []
    for language in index.languages():
        # A language with no declarations cannot degrade: there is no `symbol`
        # family to recover. Reporting one would put *"could not read markdown at
        # grammar level"* on the first screen of every run, and a degradations
        # section a reader learns to skip is worse than none.
        if language in covered or not is_code(language):
            continue
        counts[(_LANGUAGE_BOUND_KIND, language)] = len(index.by_language(language))
        transitions.extend(policy.resolve(_LANGUAGE_BOUND_KIND, language).transitions)
    return with_counts(transitions, counts)


def run(
    *,
    resolved: ResolvedScope,
    root: Path,
    registry: ExtractorRegistry,
    adopt_version: str,
    writer: SurfaceWriter | None = None,
    coverage_records: CoverageRecords | None = None,
    cache: CacheWriter | None = None,
    out_dir: Path | None = None,
    formats: Sequence[str] = DEFAULT_FORMATS,
    stage1_budget_s: float = MAP_STAGE1_BUDGET_S,
    total_budget_s: float = MAP_TOTAL_BUDGET_S,
    run_id: str | None = None,
    clock: Clock | None = None,
    sequential: bool = False,
    guard: EgressGuard | None = None,
    actor_id: str | None = None,
) -> RunResult:
    """Run one `adopt map` invocation end to end.

    Args:
        resolved: The scope, already resolved and frozen. Every abort in `02` §2
            happened before this function was reached, which is what makes exit 4
            *"zero writes"* true at the byte level.
        root: The client tree.
        registry: The extractors admitted for this run.
        adopt_version: Stamped into every artifact.
        writer: The store write path, or `None` for a dry run.
        coverage_records: The coverage read port, or `None` to skip coverage.
        cache: The store to rebuild the coverage cache through, or `None`.
        out_dir: Artifact directory; defaults to `.adopt/out`.
        formats: Which artifacts to emit -- `02` §8's `--format`.
        stage1_budget_s: `--stage1-budget`; defaults to `MAP_STAGE1_BUDGET_S`.
        total_budget_s: `--budget`; defaults to `MAP_TOTAL_BUDGET_S`.
        run_id: Correlation id; minted when absent.
        clock: Injected clock for the artifact timestamps.
        sequential: Run extractors in this process. See `scheduler.run_all`.
        guard: The egress guard. One is installed when none is supplied, because
            offline-by-default must not depend on a caller remembering.
        actor_id: Who caused the run, where a human did.

    Returns:
        The `RunResult` every artifact was rendered from.
    """
    started = time.monotonic()
    wall_start = time.time()
    the_clock: Clock = clock if clock is not None else SystemClock()
    identifier = run_id or new_run_id()
    log = _log.bind_run(identifier)
    destination = out_dir if out_dir is not None else Path(DEFAULT_OUT_DIR)

    index = build_index(root)
    log.info(
        "map_indexed",
        files=len(index.files),
        discovered=index.discovered,
        sampled=index.sampled,
    )

    plan = plan_run(registry, archetype=resolved.archetype, root=str(root), tier=resolved.tier)
    for extractor_id, reason in plan.skipped:
        log.info("map_extractor_skipped", extractor=extractor_id, reason=reason)

    budget = Budget.starting_at(wall_start, stage1_s=stage1_budget_s, total_s=total_budget_s)
    ctx = ExtractorContext(
        root=str(root),
        index=index,
        budget=budget,
        archetype=resolved.archetype,
        tier=resolved.tier,
    )

    the_guard = guard if guard is not None else EgressGuard()
    with guarded(the_guard):
        stage1 = _run_stage(plan.stage1, ctx, sequential=sequential)
        stage1_elapsed = time.monotonic() - started
        stage1_result = _assemble(
            run_id=identifier,
            adopt_version=adopt_version,
            generated_at=truncate_to_millisecond(the_clock.now()),
            resolved=resolved,
            index=index,
            schedules=(stage1,),
            plan=plan,
            guard=the_guard,
            stage1_elapsed_s=stage1_elapsed,
            total_elapsed_s=stage1_elapsed,
            dry_run=writer is None,
        )
        # `03` §5.9 invariant 1: stage-1 markdown is written **before** the
        # remaining families are processed. Emitted even when `md` is not in
        # `--format`, because the stage-1 artifact is what `01` F11.2 promises and
        # the format flag selects the *final* artifacts.
        _emit_stage1(stage1_result, destination)

        stage2 = _run_stage(plan.stage2, ctx, sequential=sequential)

    schedules = (stage1, stage2)
    batches = _batches(plan, schedules)
    write_result = None
    if writer is not None:
        write_result = writer.write_batches(
            resolved=resolved,
            batches=batches,
            vcs_revision=index.vcs_revision,
            run_id=identifier,
            actor_id=actor_id,
        )

    coverage = None
    if coverage_records is not None:
        coverage = report_coverage(
            coverage_records,
            cache,
            system_id=resolved.system_id,
            environment_id=resolved.environment_id,
            rebuild=writer is not None,
        )

    total_elapsed = time.monotonic() - started
    result = _assemble(
        run_id=identifier,
        adopt_version=adopt_version,
        generated_at=truncate_to_millisecond(the_clock.now()),
        resolved=resolved,
        index=index,
        schedules=schedules,
        plan=plan,
        guard=the_guard,
        stage1_elapsed_s=stage1_elapsed,
        total_elapsed_s=total_elapsed,
        dry_run=writer is None,
        write_result=write_result,
        coverage=coverage,
    )
    _emit(result, destination, formats)
    log.info(
        "map_completed",
        facts=result.total_facts(),
        exit_code=result.exit_code,
        truncated=len(result.truncated_families),
    )
    return result


def _batches(plan: RunPlan, schedules: Sequence[ScheduleResult]) -> tuple[FactBatch, ...]:
    """`(manifest, facts)` per extractor, in deterministic id order.

    Built by joining the plan's manifests to the scheduler's outcomes rather than
    by trusting either alone: an outcome names an id, and only the plan knows
    which manifest that id declared.
    """
    manifests = {extractor.manifest().id: extractor.manifest() for extractor in plan.all}
    batches = [
        FactBatch(manifest=manifests[outcome.extractor_id], facts=outcome.facts)
        for schedule in schedules
        for outcome in schedule.outcomes
        if outcome.extractor_id in manifests and outcome.facts
    ]
    return tuple(sorted(batches, key=lambda batch: batch.manifest.id))


def _outcomes(schedules: Sequence[ScheduleResult]) -> tuple[ExtractorOutcome, ...]:
    return tuple(
        sorted(
            (outcome for schedule in schedules for outcome in schedule.outcomes),
            key=lambda outcome: outcome.extractor_id,
        )
    )


def _exit_code(schedules: Sequence[ScheduleResult]) -> int:
    """`02` §8. Exit 3 when anything was truncated; otherwise complete.

    A *failed* extractor does not change the exit code: `01` F7.4 makes an
    extractor exception an isolated, recorded event and the run still completed
    what it could. Exit 3 is reserved for the budget, which is the one case where
    the map is knowingly short of families it planned to cover.
    """
    truncated = any(
        outcome.status in {"timeout", "truncated"}
        for schedule in schedules
        for outcome in schedule.outcomes
    )
    return MapExitCode.PARTIAL_BUDGET_EXHAUSTED if truncated else MapExitCode.COMPLETE


def _assemble(
    *,
    run_id: str,
    adopt_version: str,
    generated_at: _dt.datetime,
    resolved: ResolvedScope,
    index: FileIndex,
    schedules: Sequence[ScheduleResult],
    plan: RunPlan,
    guard: EgressGuard,
    stage1_elapsed_s: float,
    total_elapsed_s: float,
    dry_run: bool,
    write_result: SurfaceWriteResult | None = None,
    coverage: CoverageReport | None = None,
) -> RunResult:
    batches = _batches(plan, schedules)
    truncated = tuple(
        sorted({family for schedule in schedules for family in schedule.truncated_families})
    )
    return RunResult(
        run_id=run_id,
        adopt_version=adopt_version,
        generated_at=generated_at,
        resolved=resolved,
        index=index,
        batches=batches,
        outcomes=_outcomes(schedules),
        skipped=plan.skipped,
        degradations=_degradations(index, batches),
        truncated_families=truncated,
        write_result=write_result,
        coverage=coverage,
        network_attempted=guard.attempted,
        stage1_elapsed_s=stage1_elapsed_s,
        total_elapsed_s=total_elapsed_s,
        exit_code=_exit_code(schedules),
        timeout_enforced=all(schedule.timeout_enforced for schedule in schedules),
        dry_run=dry_run,
        peak_rss=peak_rss_bytes(),
    )


def _emit_stage1(result: RunResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / SURFACE_MD_NAME).write_text(render_stage1(result), encoding="utf-8")


def _emit(result: RunResult, out_dir: Path, formats: Sequence[str]) -> None:
    """Write the selected artifacts, plus the run report and telemetry.

    The run report is written whatever `--format` says: it is the evidence for
    every NFR this build claims, and making it optional would make the claims
    optional with it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    selected = {value.strip() for value in formats}
    if "md" in selected:
        (out_dir / SURFACE_MD_NAME).write_text(render_markdown(result), encoding="utf-8")
    if "json" in selected:
        (out_dir / SURFACE_JSON_NAME).write_text(render_surface_json(result), encoding="utf-8")
    if "mermaid" in selected:
        (out_dir / MERMAID_NAME).write_text(render_mermaid(result), encoding="utf-8")
    if "d2" in selected:
        (out_dir / D2_NAME).write_text(render_d2(result), encoding="utf-8")
    write_run_report(result, out_dir)
