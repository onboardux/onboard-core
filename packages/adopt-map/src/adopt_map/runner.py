"""The map runner: select packs, run extractors, own every write.

**Extractors propose; the runner writes.** One transaction per pack, so a pack
that fails halfway leaves no partial pack behind, and a pack that succeeds is
durable regardless of what a later pack does.

**A failing extractor is loud and counted.** This is B-08's finding promoted into
the design: on a 930k-line real repository, one extractor failed on one run of
two, the run still exited `0`, and the only trace was one fewer identity. A
silent extractor failure and a genuinely smaller system are indistinguishable
from the outside, so every extractor's outcome is recorded, failures carry their
exception type, and `MapReport.failed` is what the CLI and the journey test both
refuse to ignore.

**The store is reached through a protocol, not a package.** `IdentityWriter` is
satisfied structurally by `adopt_store`'s `IdentityFacade`; the CLI, as the
composition root, hands one in. That is the CR-34/CR-37 pattern and it is what
keeps `sqlite3` out of this package's import graph.
"""

import datetime as _dt
from collections.abc import Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Protocol

from adopt_identity import build_uri
from adopt_map.moves import (
    MoveCandidate,
    MoveOutcome,
    ObservedIdentity,
    StoredIdentity,
    detect_moves,
)
from adopt_map.observation import Observation
from adopt_map.observe import ExtractorOutcome, Pack, observe_tree
from adopt_map.tree import SourceTree
from adopt_model._enums import Archetype, IdentityKind
from adopt_obs import AdoptError, ErrorCode, get_logger
from adopt_scope import Scope

__all__ = [
    "ExtractorOutcome",
    "IdentityWriter",
    "MapReport",
    "Pack",
    "run_map",
    "select_packs",
]

_log = get_logger("adopt_map")


class IdentityWriter(Protocol):
    """The narrow slice of `IdentityFacade` this package needs.

    Declared here rather than imported so `adopt_map` never depends on
    `adopt_store`. The runner cannot delete, cannot update and **cannot retire**
    through this protocol -- Build 1 observes and, on unambiguous evidence,
    moves. Retirement is absent by design: absence is not death (plan decision
    D6), and the build that owns what a disappearance *means* is Build 6.
    """

    def observe(
        self,
        *,
        scope: Scope,
        kind: IdentityKind,
        namespace: str | None,
        key: str | tuple[str, ...],
        extractor: str | None = ...,
        extractor_version: str | None = ...,
        source_version: str | None = ...,
        source_ref: str | None = ...,
        confidence: float | None = ...,
        actor_id: str | None = ...,
    ) -> object: ...

    def move(
        self,
        *,
        identity_id: str,
        scope: Scope,
        kind: IdentityKind,
        namespace: str | None,
        key: str | tuple[str, ...],
        actor_id: str | None = ...,
    ) -> object: ...


class Transactional(Protocol):
    """Whatever supplies the per-pack unit of work."""

    def transaction(self) -> AbstractContextManager[None]: ...


@dataclass(slots=True)
class MapReport:
    """The result of one `adopt map` run."""

    scope: str
    packs: tuple[str, ...]
    files_walked: int = 0
    files_oversized: tuple[str, ...] = field(default_factory=tuple)
    #: Repo-relative paths that yielded at least one observation. The report's
    #: unmapped count is `files_walked` minus this, which is how the map states
    #: its own coverage rather than asserting it.
    files_with_observations: set[str] = field(default_factory=set)
    identities_seen: int = 0
    outcomes: list[ExtractorOutcome] = field(default_factory=list)
    started_at: _dt.datetime | None = None
    finished_at: _dt.datetime | None = None
    #: What move detection concluded. Two of its three fields are reports that
    #: write nothing, and they are carried here so the report can say so out
    #: loud rather than leaving the reader to infer silence.
    moves: MoveOutcome = field(default_factory=MoveOutcome)
    #: Every referent this run saw, populated only when `stored` was supplied --
    #: the same condition that enables move detection, because both are
    #: comparisons against prior state. Build 6's diff consumes it: re-walking
    #: the tree to rediscover what the run just extracted would be a second
    #: extraction, and two extractions are two answers to what is there.
    observed: tuple[ObservedIdentity, ...] = ()

    @property
    def failed(self) -> list[ExtractorOutcome]:
        """Extractors that raised. Non-empty means the map is incomplete."""
        return [outcome for outcome in self.outcomes if outcome.status == "failed"]

    @property
    def files_unmapped(self) -> int:
        """Walked files no extractor drew an observation from.

        Not a defect count -- a README legitimately yields nothing. It is the
        denominator that makes the map honest about its own reach, and the
        number the glue-pass trigger is judged against.
        """
        return max(0, self.files_walked - len(self.files_with_observations))


#: Which packs run for which archetype (v6.1 §6, R10: generic + web + ai only;
#: platform, lowcode and data are pulled by a real engagement, never pushed).
#: `generic` runs for every archetype -- config, dependencies and jobs exist in
#: every system, whatever it is built from.
PACKS_FOR_ARCHETYPE: Mapping[Archetype, tuple[str, ...]] = {
    "web": ("generic", "web"),
    "ai": ("generic", "ai"),
    "platform": ("generic",),
    "lowcode": ("generic",),
    "data": ("generic",),
}


def select_packs(
    archetype: Archetype | None,
    *,
    override: Sequence[str] | None = None,
    available: Mapping[str, Pack],
) -> tuple[Pack, ...]:
    """Resolve which packs run, from the archetype or an explicit override.

    Args:
        archetype: `system.archetype`, as recorded by `adopt init`.
        override: `--packs`, for a mixed system whose archetype names only its
            dominant half. An override is taken literally: an operator who names
            packs gets those packs and no others.
        available: Registered packs by name.

    Raises:
        AdoptError: ``MAP_NO_PACK_FOR_ARCHETYPE`` when nothing resolves -- an
            unnamed archetype with no override, or an override naming packs that
            do not exist. Refused rather than defaulted: silently falling back to
            `generic` would produce a thin map that looks like a complete one.
    """
    if override is not None:
        unknown = [name for name in override if name not in available]
        if unknown:
            raise AdoptError(
                ErrorCode.MAP_NO_PACK_FOR_ARCHETYPE,
                message=f"--packs named {', '.join(sorted(unknown))}, which do not exist",
                hint=f"Available packs: {', '.join(sorted(available))}.",
            )
        if not override:
            raise AdoptError(
                ErrorCode.MAP_NO_PACK_FOR_ARCHETYPE,
                message="--packs was given no pack names",
                hint=f"Name at least one of: {', '.join(sorted(available))}.",
            )
        return tuple(available[name] for name in override)

    if archetype is None:
        raise AdoptError(
            ErrorCode.MAP_NO_PACK_FOR_ARCHETYPE,
            message="the system has no recorded archetype, so no pack could be selected",
            hint="Re-run `adopt init --archetype <a>` to record one, or pass `--packs` to "
            "choose explicitly. The archetype is a human decision (`01` §8) and is not "
            "guessed here.",
        )

    names = PACKS_FOR_ARCHETYPE.get(archetype, ())
    resolved = tuple(available[name] for name in names if name in available)
    if not resolved:
        raise AdoptError(
            ErrorCode.MAP_NO_PACK_FOR_ARCHETYPE,
            message=f"no extractor pack is registered for archetype {archetype!r}",
            hint="Generic, web and AI packs ship in this release; platform, low-code and "
            "data packs are built when a real engagement of that archetype exists "
            "(v6.1 §4 R10). Pass `--packs generic` to map what is archetype-neutral.",
        )
    return resolved


def run_map(
    *,
    tree: SourceTree,
    scope: Scope,
    packs: Iterable[Pack],
    writer: IdentityWriter,
    records: Transactional,
    actor_id: str | None = None,
    stored: Sequence[StoredIdentity] | None = None,
) -> MapReport:
    """Run every extractor in every pack and observe what they find.

    One transaction per pack. Within a pack, an extractor that raises is caught,
    recorded as `failed` with its exception type, and the remaining extractors
    still run -- one broken extractor should cost its own observations, not the
    other twenty-eight's.

    Args:
        stored: The identities already in scope, **read before this run**. When
            given, referents that disappeared are paired against ones that
            appeared and unambiguous pairs are recorded as moves. `None` skips
            detection entirely, which is what a caller with no prior state
            wants: on a first run every identity has appeared and nothing has
            gone, so there is nothing to pair.
    """
    pack_list = tuple(packs)
    report = MapReport(scope=scope.path(), packs=tuple(pack.name for pack in pack_list))
    report.files_walked = len(tree.files)
    report.files_oversized = tree.oversized
    observed: list[ObservedIdentity] = []

    # **Extraction first, writes after** -- and the whole of extraction lives in
    # `adopt_map.observe` so that `adopt ci-sense`, which has no store to write
    # to, runs the same extractors through the same code (Build 8, sprint plan
    # D8-2). Extractors are pure and hold no store handle, so running them
    # outside the transaction changes nothing they can observe; the transaction
    # only ever wrapped the writes below.
    for found in observe_tree(tree, packs=pack_list):
        report.outcomes.extend(found.outcomes)
        by_extractor = {outcome.extractor: outcome for outcome in found.outcomes}
        with records.transaction():
            for sighting in found.sightings:
                observation = sighting.observation
                _write_observation(
                    observation,
                    digest=sighting.digest,
                    extractor=sighting.extractor,
                    extractor_version=sighting.extractor_version,
                    scope=scope,
                    writer=writer,
                    actor_id=actor_id,
                )
                if stored is not None:
                    # Addressed only when detection will use it. `build_uri`
                    # repeats work the facade just did, and a run with no
                    # prior state has nothing to pair -- on a first run every
                    # identity has appeared and none has gone.
                    observed.append(
                        ObservedIdentity(
                            uri=build_uri(
                                scope,
                                observation.kind,
                                observation.namespace,
                                tuple(observation.key),
                            ),
                            kind=observation.kind,
                            namespace=observation.namespace,
                            key=tuple(observation.key),
                            digest=sighting.digest,
                            extractor=sighting.extractor,
                            extractor_version=sighting.extractor_version,
                            source_path=observation.span.path,
                        )
                    )
                by_extractor[sighting.extractor].written += 1
                report.identities_seen += 1
                report.files_with_observations.add(observation.span.path)

    if stored is not None:
        report.observed = tuple(observed)
        report.moves = _record_moves(
            outcome=detect_moves(observed=observed, stored=stored),
            scope=scope,
            writer=writer,
            records=records,
            actor_id=actor_id,
        )

    if report.failed:
        _log.error(
            "map.incomplete",
            failed=len(report.failed),
            extractors=[outcome.extractor for outcome in report.failed],
        )
    return report


def _write_observation(
    observation: Observation,
    *,
    digest: str,
    extractor: str,
    extractor_version: str,
    scope: Scope,
    writer: IdentityWriter,
    actor_id: str | None,
) -> None:
    """Record one already-observed referent.

    The digest arrives computed (`adopt_map.observe`) rather than being derived
    here, so that the value this store keeps and the value `adopt ci-sense`
    posts to the plane come from one expression. Two computations of one digest
    is how the same referent acquires two different ones, which reads
    downstream as a semantic change nobody made.
    """
    writer.observe(
        scope=scope,
        kind=observation.kind,
        namespace=observation.namespace,
        key=tuple(observation.key),
        extractor=extractor,
        extractor_version=extractor_version,
        source_version=digest,
        source_ref=observation.span.render(),
        actor_id=actor_id,
    )


def _record_moves(
    *,
    outcome: MoveOutcome,
    scope: Scope,
    writer: IdentityWriter,
    records: Transactional,
    actor_id: str | None,
) -> MoveOutcome:
    """Write the unambiguous moves; report the rest without writing anything."""
    if outcome.moves:
        with records.transaction():
            for move in outcome.moves:
                _move(move, scope=scope, writer=writer, actor_id=actor_id)
    for move in outcome.moves:
        _log.info("map.identity_moved", from_uri=move.from_uri, to_uri=move.to.uri)
    if outcome.absent:
        # `info`, not `error`: an absent referent is a fact about the tree, and
        # every plausible cause -- a new ignore rule, a narrowed root, an
        # extractor that failed this run -- is something a reader has to judge.
        # Logging it as a failure would train them to ignore it.
        _log.info("map.identities_absent", count=len(outcome.absent))
    if outcome.ambiguous:
        _log.info("map.moves_ambiguous", count=len(outcome.ambiguous))
    return outcome


def _move(
    move: MoveCandidate, *, scope: Scope, writer: IdentityWriter, actor_id: str | None
) -> None:
    writer.move(
        identity_id=move.identity_id,
        scope=scope,
        kind=move.to.kind,
        namespace=move.to.namespace,
        key=move.to.key,
        actor_id=actor_id,
    )
