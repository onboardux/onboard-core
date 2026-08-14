"""The only code path that writes to the store -- contracts §6, impl spec §5.5.

`03` §5.5 calls this **T1, the densest suite in the plan**, and the reason is
that every guarantee this build sells is a property of this module: append-only,
one transaction, exactly the §6 write set, no `status='dead'`, no secret value.

Five things are structural here rather than remembered:

1. **There is no update, no delete and no `DROP` in this module.** Not "we do not
   call one" -- the ports this package declares (`adopt_map.ports`) expose no such
   method, so a caller cannot reach for a mutation that has no name. A source scan
   asserts the absence anyway, because "the port has no method" is a claim about
   today's code (`03` §5.5 invariant 3).
2. **Every revision goes through Build 0's `RevisionWriter`.** It is the one
   append-only mutation path for all four families, enforcing `expected_head_id`
   and raising `REVISION_CHAIN_FORK` on a mismatch.
3. **`status='dead'` is never written** (B1-CR-07, PRD F5.3). Absence and parse
   failure are indistinguishable from here, and a false death is a false
   retirement downstream. Build 3 owns death.
4. **The whole run is one transaction** (PRD F3.3). A crashed or budget-killed
   run leaves the store byte-identical to its pre-run state.
5. **Facts below `MAP_MIN_EMIT_CONFIDENCE` become gaps, not knowledge** (PRD
   F3.4, F9.3). Silence beats guessing.

**Two departures from `02` §6, both recorded rather than silent.**

*`freshness_state` is `unverified`, not `fresh` (B1-CR-39).* `02` §6 says a
surface `knowledge_item` is created `fresh`. Build 0's `RevisionWriter.create_item`
writes `unverified` and gives its reason: *"A new item claims nothing. `fresh`
would assert a verification nobody performed."* Index §3 rider 1 settles it --
Build 0 wins on a substrate shape and the pack document is repaired -- and Build 0
is also plainly right: `verification='unverified'` on the revision, which `02` §6
requires in the same row, would otherwise contradict a `fresh` parent.

*`provenance` is written only where a VCS revision resolves (B1-CR-36, OD-4).*
`02` §6 says `source_type='commit'` where a VCS ref exists, *"else the artifact
kind"*. `SourceType` is closed -- `commit · pr · ticket · adr · probe_output ·
trace · human` -- and has **no artifact member**, so "else" names a value that
does not exist and Build 1 may not add one (`00` §9 rule 4). A source ref with no
resolvable `source_type` is recorded as a gap instead of being written under a
plausible-looking wrong one.

**S1.2 makes the writer revision-aware, and the comparison is per family.**
`02` §4.3 row 1: an equal composite writes no revision and touches
`identity.last_seen` alone. `05` S1.2 asks for *"exactly one revision per changed
referent per table"*, and the honest reading of "changed" is **per table**: a
revision is appended where *that table's recorded content* differs (B1-CR-47,
OD-7). A `binding_revision` records an extractor, its version, a confidence and a
locator rung and nothing else, so a fact whose attributes changed leaves it
untouched -- appending an identical row on every content change would turn the
chain into a log of scans, which is exactly what Build 0's `IdentityFacade`
refuses to do for the same reason.
"""

import datetime as _dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from adopt_const import MAP_MIN_EMIT_CONFIDENCE, SURFACE_ATTRS_VERSION, SURFACE_REPORT_VERSION
from adopt_map.frontmatter import FrontMatter, RenderedRelation, render_body
from adopt_map.minting import mint, normalize_local_key
from adopt_map.moves import Move, Observation, detect_moves
from adopt_map.ports import RevisionAppender, ScopeLookupRecords, SurfaceAuxRecords
from adopt_map.prior import PriorIdentity, PriorState
from adopt_map.schemas.surface import (
    EvidenceMethod,
    ExtractorManifest,
    FactRelation,
    SourceRef,
    SurfaceFact,
)
from adopt_map.scope_resolve import ResolvedScope
from adopt_map.sourceversion import SourceVersion, build_source_version
from adopt_model import AuditEvent, Conflict, DeathCondition, Provenance
from adopt_model._enums import (
    AuthorityClass,
    DeathCause,
    IdentityKind,
    ItemKind,
    LocatorRung,
    Verification,
)
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, new_id, truncate_to_millisecond
from adopt_scope import Scope

__all__ = [
    "AUDIT_EVENT_TYPE",
    "SURFACE_AUTHORITY_CLASS",
    "SURFACE_DEATH_CONDITION",
    "SURFACE_ITEM_KIND",
    "SURFACE_STATUS_ACTIVE",
    "SURFACE_STATUS_MOVED",
    "SURFACE_VERIFICATION",
    "Gap",
    "SurfaceWriteResult",
    "SurfaceWriter",
    "confidence_for",
]

# -- The fixed values contracts §6 pins. Named, so a reader can find every one
#    of them in one place and a test can assert against the name.
SURFACE_ITEM_KIND: Final[ItemKind] = "surface"
SURFACE_AUTHORITY_CLASS: Final[AuthorityClass] = "artifact_observed"
SURFACE_VERIFICATION: Final[Verification] = "unverified"
SURFACE_DEATH_CONDITION: Final[DeathCause] = "referent_retired"
AUDIT_EVENT_TYPE: Final[str] = "surface.map_completed"

#: The two statuses Build 1 writes, and the complete list of them. `02` §6 gives
#: `identity_revision` and `binding_revision` `'active'` or `'moved'`; every
#: other member of either status enum belongs to a later build (B1-CR-07).
SURFACE_STATUS_ACTIVE: Final[str] = "active"
SURFACE_STATUS_MOVED: Final[str] = "moved"

#: `is_load_bearing = 1` on every binding Build 1 creates (B1-CR-17, add-on §6).
#: Build 3 owns the refinement; Build 1 does not guess. The column defaults to 1
#: in the schema so a writer that forgets errs toward staleness -- this constant
#: is the deliberate version of that, passed explicitly because `BindingFacade`
#: requires it and would otherwise let the forgetting happen silently.
SURFACE_IS_LOAD_BEARING: Final[bool] = True

#: `locator_rung` is set **only** for `ui_component` (contracts §6), and only
#: rungs 1-2 ever mint one (B1-CR-11), so an identity that reached the writer at
#: all was minted from a stable id or an accessibility pair.
_LOCATOR_RUNG_KIND: Final[str] = "ui_component"
_LOCATOR_RUNG_BY_NAMESPACE: Final[dict[str, LocatorRung]] = {
    "component": 1,
    "testid": 1,
    "aria": 2,
}

#: The `SourceType` a repo-relative reference maps to when a VCS revision was
#: resolvable. See the module docstring for what happens when none was.
_VCS_SOURCE_TYPE: Final[str] = "commit"

#: Method -> the confidence the **framework** assigns (PRD F9.1). An extractor
#: never sets its own; the plugin audit rejects one that tries (S1.3).
_CONFIDENCE_BY_METHOD: Final[dict[str, str]] = {
    "grammar": "MAP_CONF_GRAMMAR",
    "reflection": "MAP_CONF_REFLECTION",
    "declared": "MAP_CONF_DECLARED",
    "ctags": "MAP_CONF_CTAGS",
    "regex": "MAP_CONF_REGEX",
    "agent": "MAP_CONF_AGENT_REVIEWED",
}


def confidence_for(method: EvidenceMethod) -> float:
    """The confidence band for an evidence method -- PRD F9.1, `03` §3.

    Imported from `adopt_const` by name rather than inlined, so a retune in the
    constants table reaches every call site (`00` §9 rule 2).
    """
    import adopt_const

    return float(getattr(adopt_const, _CONFIDENCE_BY_METHOD[method]))


@dataclass(frozen=True, slots=True)
class Gap:
    """A referent seen but not written as knowledge, and why.

    A gap is a **first-class output**, not an error: PRD F9.3 makes "below the
    confidence floor" a recorded gap rather than a silently dropped fact, because
    a map that omits what it could not see is a map that overstates itself.
    """

    identity_uri: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class MoveRecord:
    """One emitted move, as `02` §9.2's `moves[]` carries it: URI to URI."""

    from_uri: str
    to_uri: str


@dataclass(frozen=True, slots=True)
class ConflictRecord:
    """One declined move -- `02` §9.2's `conflicts[]`."""

    identity_uri: str
    reason: str
    candidates: int


@dataclass(slots=True)
class SurfaceWriteResult:
    """What one run wrote -- the payload behind `run_report.json`'s counters."""

    report_version: int = SURFACE_REPORT_VERSION
    run_id: str = ""
    identities_seen: int = 0
    identities_new: int = 0
    revisions_written: dict[str, int] = field(
        default_factory=lambda: {"identity": 0, "knowledge": 0, "binding": 0}
    )
    provenance_written: int = 0
    gaps: list[Gap] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    moves: list[MoveRecord] = field(default_factory=list)
    #: `extractor id -> (previous version, current version)`. PRD F4.5 and CUJ-2's
    #: failure branch: an extractor version bump legitimately produces revisions,
    #: and the report has to **name it as the cause** so an operator can tell a
    #: real change from a tooling change without reading the diff.
    extractor_version_changes: dict[str, tuple[str, str]] = field(default_factory=dict)


class _IdentityRow(Protocol):
    """An `identity` row. Its ULID is all this writer needs from one."""

    id: str


class _IdentityFacade(Protocol):
    """The slice of Build 0's `IdentityFacade` this writer uses.

    Declared structurally rather than imported: `no-raw-sqlite` names `adopt_map`
    as a source module and follows indirect chains, so importing `adopt_store`
    to name the real type would drag `sqlite3` into this package's graph.
    """

    def observe(
        self,
        *,
        scope: Scope,
        # `IdentityKind`, the generated closed enum -- imported rather than
        # widened to `str`, because a protocol parameter is contravariant and
        # `str` would make the real facade fail to satisfy this.
        kind: IdentityKind,
        namespace: str | None,
        key: str | tuple[str, ...],
        extractor: str | None = ...,
        extractor_version: str | None = ...,
        confidence: float | None = ...,
        actor_id: str | None = ...,
    ) -> _IdentityRow: ...

    def find_by_uri(self, uri: str) -> _IdentityRow | None: ...


class _ItemFacade(Protocol):
    """Build 0's `KnowledgeFacade.create`.

    `revision` is `Any` and that is the honest type, not a shortcut: the draft is
    `adopt_store.KnowledgeRevisionDraft`, and naming it here would import
    `adopt_store` and drag `sqlite3` into this package's graph, breaking
    `no-raw-sqlite`. The same admission is already explicit in `__init__`'s
    `knowledge_draft: type` parameter -- the constructor is handed in because it
    cannot be imported. Narrowing to `object` does not work either: a protocol
    parameter is contravariant, so a wider annotation makes the real facade fail
    to satisfy it.
    """

    def create(
        self,
        *,
        scope: Scope,
        kind: ItemKind,
        title: str,
        revision: Any,
        actor_id: str | None = ...,
    ) -> tuple[str, str]: ...


class _BindingFacade(Protocol):
    """Build 0's `BindingFacade.create`. `revision` is `Any` -- see `_ItemFacade`."""

    def create(
        self,
        *,
        item_id: str,
        identity_id: str,
        is_load_bearing: bool,
        revision: Any = ...,
        actor_id: str | None = ...,
    ) -> tuple[str, str]: ...


class SurfaceWriter:
    """Write one run's facts, in one transaction, through Build 0's helpers.

    The facades and drafts arrive already constructed rather than being imported,
    because `no-raw-sqlite` names `adopt_map` as a source module and follows
    indirect chains: importing `adopt_store` here would drag `sqlite3` into this
    package's graph. Composition happens in `adopt_cli.store_option`, the one
    module exempted for exactly this (CR-36).
    """

    def __init__(
        self,
        *,
        identities: _IdentityFacade,
        items: _ItemFacade,
        bindings: _BindingFacade,
        aux: SurfaceAuxRecords,
        lookup: ScopeLookupRecords,
        revisions: RevisionAppender,
        knowledge_draft: type,
        binding_draft: type,
        identity_draft: type,
        schema_version: int,
        supported_schema_version: int,
        clock: Clock | None = None,
    ) -> None:
        if schema_version != supported_schema_version:
            raise AdoptError(
                ErrorCode.MAP_STORE_INCOMPATIBLE,
                message=f"the store is at schema version {schema_version}; "
                f"`adopt map` writes version {supported_schema_version}",
                hint="Open the store with a matching `adopt` build. The schema is frozen "
                "additive-only since v0.3.0 and Build 1 adds no column, so a version "
                "mismatch means the binary and the store came from different lines.",
            )
        self._identities = identities
        self._items = items
        self._bindings = bindings
        self._aux = aux
        self._lookup = lookup
        self._revisions = revisions
        self._knowledge_draft = knowledge_draft
        self._binding_draft = binding_draft
        self._identity_draft = identity_draft
        self._clock: Clock = clock if clock is not None else SystemClock()

    def _now(self) -> _dt.datetime:
        return truncate_to_millisecond(self._clock.now())

    # -- the run ----------------------------------------------------------

    def write_run(
        self,
        *,
        resolved: ResolvedScope,
        manifest: ExtractorManifest,
        facts: Sequence[SurfaceFact],
        vcs_revision: str | None,
        run_id: str | None = None,
        actor_id: str | None = None,
    ) -> SurfaceWriteResult:
        """Write every fact, then one `audit_event`. **One transaction.**

        Args:
            resolved: The run's scope. The only source of the four scope columns
                and of the URI's four leading segments.
            manifest: The emitting extractor's manifest. Its `kinds` list is
                enforced per fact -- an extractor that quietly widens its own
                vocabulary is one whose coverage arithmetic nobody can check.
            facts: What the extractor emitted.
            vcs_revision: The tree's commit sha, or `None` when the tree is not a
                checkout. `None` means provenance is recorded as a gap rather
                than written under a `source_type` that does not fit (B1-CR-36).
            run_id: Correlation id; minted when absent.
            actor_id: Who caused the run, where a human did.

        Returns:
            The counters and gaps this run produced.

        Raises:
            AdoptError: ``MAP_EXTRACTOR_FAILED`` when a fact's kind is outside the
                manifest or its attributes do not validate.
        """
        result = SurfaceWriteResult(run_id=run_id or new_id("run"))
        permitted = frozenset(manifest.kinds)
        confidence = confidence_for(manifest.method)

        with self._aux.transaction():
            # Read the prior state **once**, before anything is written, so every
            # comparison in this run is against the state the previous run left
            # rather than against whatever this run has already done to it.
            prior = PriorState.load(
                self._lookup,
                system_id=resolved.system_id,
                environment_id=resolved.environment_id,
            )
            observations: list[Observation] = []
            for fact in facts:
                self._write_fact(
                    resolved=resolved,
                    manifest=manifest,
                    permitted=permitted,
                    confidence=confidence,
                    fact=fact,
                    vcs_revision=vcs_revision,
                    actor_id=actor_id,
                    prior=prior,
                    observations=observations,
                    result=result,
                )
            self._apply_moves(
                prior=prior,
                observations=observations,
                manifest=manifest,
                actor_id=actor_id,
                result=result,
            )
            self._write_audit_event(resolved=resolved, actor_id=actor_id, result=result)

        return result

    # -- one fact ---------------------------------------------------------

    def _write_fact(
        self,
        *,
        resolved: ResolvedScope,
        manifest: ExtractorManifest,
        permitted: frozenset[str],
        confidence: float,
        fact: SurfaceFact,
        vcs_revision: str | None,
        actor_id: str | None,
        prior: PriorState,
        observations: list[Observation],
        result: SurfaceWriteResult,
    ) -> None:
        if fact.identity_kind not in permitted:
            raise AdoptError(
                ErrorCode.MAP_EXTRACTOR_FAILED,
                message=f"{manifest.id} emitted kind {fact.identity_kind!r}, which is not "
                f"in its manifest: {sorted(permitted)}",
                hint="Declare the kind in the manifest or stop emitting it. An extractor "
                "that widens its own vocabulary at runtime is one whose coverage "
                "arithmetic nobody can check afterwards.",
            )
        # Validated once, then reused: the closed schema is the egress allowlist
        # (`02` §5.1), and the validated form is also what the digest and the
        # front-matter are built from, so an undeclared attribute cannot reach a
        # digest, a body or the store.
        attributes = fact.validated_attributes().model_dump(mode="json")

        uri = mint(resolved.scope, fact)

        if confidence < MAP_MIN_EMIT_CONFIDENCE:
            result.gaps.append(
                Gap(
                    identity_uri=uri,
                    reason="below_confidence",
                    detail=f"{manifest.method} {confidence:.2f} < {MAP_MIN_EMIT_CONFIDENCE:.2f}",
                )
            )
            return

        version = build_source_version(fact, attributes, vcs_revision=vcs_revision)
        body_md = self._render_body(
            resolved=resolved,
            fact=fact,
            attributes=attributes,
            uri=uri,
            method=manifest.method,
            confidence=confidence,
        )

        entry = prior.get(uri)
        # Idempotent by construction: `observe` advances `last_seen` and writes
        # no revision when the URI is already known (Build 0's `IdentityFacade`).
        # It is called on **every** sighting, which is what `02` §4.3 row 1 means
        # by *"touch `identity.last_seen` only"*.
        identity = self._identities.observe(
            scope=resolved.scope,
            kind=fact.identity_kind,
            namespace=fact.namespace,
            key=_mintable_key(resolved, fact),
            extractor=manifest.id,
            extractor_version=manifest.version,
            confidence=confidence,
            actor_id=actor_id,
        )
        result.identities_seen += 1
        observations.append(
            Observation(
                uri=uri,
                identity_id=identity.id,
                identity_kind=fact.identity_kind,
                source_version=version,
                is_new=entry is None,
            )
        )

        if entry is None:
            self._create_referent(
                resolved=resolved,
                manifest=manifest,
                fact=fact,
                uri=uri,
                body_md=body_md,
                version=version,
                confidence=confidence,
                identity_id=identity.id,
                vcs_revision=vcs_revision,
                actor_id=actor_id,
                result=result,
            )
            return

        self._update_referent(
            entry=entry,
            manifest=manifest,
            fact=fact,
            uri=uri,
            body_md=body_md,
            version=version,
            confidence=confidence,
            vcs_revision=vcs_revision,
            actor_id=actor_id,
            result=result,
        )

    # -- the two halves of a fact: first sighting, and every later one -----

    def _create_referent(
        self,
        *,
        resolved: ResolvedScope,
        manifest: ExtractorManifest,
        fact: SurfaceFact,
        uri: str,
        body_md: str,
        version: SourceVersion,
        confidence: float,
        identity_id: str,
        vcs_revision: str | None,
        actor_id: str | None,
        result: SurfaceWriteResult,
    ) -> None:
        """A URI nobody has seen: item, binding and death condition all arrive."""
        result.identities_new += 1
        # `observe` wrote the identity's first revision. It carries no composite,
        # because Build 0's signature has no parameter for one and `adopt-store`
        # is protected -- B1-CR-48 / OD-9, and the reason `PriorIdentity`
        # compares against the knowledge revision's copy instead.
        result.revisions_written["identity"] += 1

        item_id, revision_id = self._items.create(
            scope=resolved.scope,
            kind=SURFACE_ITEM_KIND,
            title=fact.title,
            revision=self._knowledge_draft(
                authority_class=SURFACE_AUTHORITY_CLASS,
                verification=SURFACE_VERIFICATION,
                body_md=body_md,
                confidence=confidence,
                source_version=version.encode(),
            ),
            actor_id=actor_id,
        )
        result.revisions_written["knowledge"] += 1
        result.provenance_written += self._write_provenance(
            revision_id=revision_id,
            source_refs=fact.source_refs,
            vcs_revision=vcs_revision,
            uri=uri,
            result=result,
        )

        self._bindings.create(
            item_id=item_id,
            identity_id=identity_id,
            is_load_bearing=SURFACE_IS_LOAD_BEARING,
            revision=self._binding_draft(
                status=SURFACE_STATUS_ACTIVE,
                extractor=manifest.id,
                extractor_version=manifest.version,
                confidence=confidence,
                locator_rung=_locator_rung(fact),
            ),
            actor_id=actor_id,
        )
        result.revisions_written["binding"] += 1

        # Mandatory on every surface item (PRD F3.1 item 5, R22). `threshold` is
        # null: `referent_retired` is a state, not a duration, and the v2-era
        # pack's `identity_absent_days` is not an enum member (B1-CR-19). Written
        # once, at creation: a second one on a later run would be a duplicate
        # condition on one item.
        self._aux.insert_rows(
            "death_condition",
            [DeathCondition(item_id=item_id, condition=SURFACE_DEATH_CONDITION, threshold=None)],
        )

    def _update_referent(
        self,
        *,
        entry: PriorIdentity,
        manifest: ExtractorManifest,
        fact: SurfaceFact,
        uri: str,
        body_md: str,
        version: SourceVersion,
        confidence: float,
        vcs_revision: str | None,
        actor_id: str | None,
        result: SurfaceWriteResult,
    ) -> None:
        """A URI seen before: append a revision **only where content differs**.

        This is F4. Three independent comparisons, one per family, because the
        three record different things and *"changed"* is a question about a
        table, not about a referent (B1-CR-47).
        """
        stored = entry.source_version
        changed = stored is None or not stored.compares_equal(version)
        self._note_extractor_version(entry, manifest, result)

        if changed or _identity_content(entry) != (
            manifest.id,
            manifest.version,
            confidence,
            SURFACE_STATUS_ACTIVE,
        ):
            self._revisions.append_revision(
                parent_id=entry.identity.id,
                draft=self._identity_draft(
                    status=SURFACE_STATUS_ACTIVE,
                    extractor=manifest.id,
                    extractor_version=manifest.version,
                    source_version=version.encode(),
                    confidence=confidence,
                ),
                expected_head_id=self._revisions.current_head(entry.identity.id),
                actor_id=actor_id,
            )
            result.revisions_written["identity"] += 1

        if entry.item is not None and (
            changed or _knowledge_content(entry) != (body_md, confidence)
        ):
            revision_id = self._revisions.append_revision(
                parent_id=entry.item.id,
                draft=self._knowledge_draft(
                    authority_class=SURFACE_AUTHORITY_CLASS,
                    verification=SURFACE_VERIFICATION,
                    body_md=body_md,
                    confidence=confidence,
                    source_version=version.encode(),
                ),
                expected_head_id=self._revisions.current_head(entry.item.id),
                actor_id=actor_id,
            )
            result.revisions_written["knowledge"] += 1
            result.provenance_written += self._write_provenance(
                revision_id=revision_id,
                source_refs=fact.source_refs,
                vcs_revision=vcs_revision,
                uri=uri,
                result=result,
            )

        if entry.binding is not None and _binding_content(entry) != (
            manifest.id,
            manifest.version,
            confidence,
            _locator_rung(fact),
            SURFACE_STATUS_ACTIVE,
        ):
            self._revisions.append_revision(
                parent_id=entry.binding.id,
                draft=self._binding_draft(
                    status=SURFACE_STATUS_ACTIVE,
                    extractor=manifest.id,
                    extractor_version=manifest.version,
                    confidence=confidence,
                    locator_rung=_locator_rung(fact),
                ),
                expected_head_id=self._revisions.current_head(entry.binding.id),
                actor_id=actor_id,
            )
            result.revisions_written["binding"] += 1

    @staticmethod
    def _note_extractor_version(
        entry: PriorIdentity, manifest: ExtractorManifest, result: SurfaceWriteResult
    ) -> None:
        """Name a bumped extractor version as the cause -- PRD F4.5, CUJ-2's branch.

        An operator reading `revisions_written > 0` on a tree they did not touch
        needs to know whether the tool changed or the system did. Recorded per
        extractor rather than per fact: it is one fact about the run.
        """
        head = entry.identity_head
        if head is None or head.extractor != manifest.id or head.extractor_version is None:
            return
        if head.extractor_version != manifest.version:
            result.extractor_version_changes[manifest.id] = (
                head.extractor_version,
                manifest.version,
            )

    def _render_body(
        self,
        *,
        resolved: ResolvedScope,
        fact: SurfaceFact,
        attributes: dict[str, Any],
        uri: str,
        method: EvidenceMethod,
        confidence: float,
    ) -> str:
        """The `02` §5 front-matter block plus the prose.

        Relation targets are minted here, from **this run's scope** -- which is
        what keeps an edge inside the run's environment without the front-matter
        module having to know what an environment is (`02` §5.1 rule 2).
        """
        return render_body(
            FrontMatter(
                attrs_version=SURFACE_ATTRS_VERSION,
                identity_uri=uri,
                identity_kind=fact.identity_kind,
                method=method,
                confidence=confidence,
                outside_vcs=fact.outside_vcs,
                opaque=fact.opaque,
                attributes=attributes,
                relations=[
                    RenderedRelation(
                        predicate=relation.predicate,
                        target=_mint_relation_target(resolved, relation),
                    )
                    for relation in fact.relations
                ],
            ),
            fact.prose,
        )

    # -- the auxiliary tables --------------------------------------------

    def _write_provenance(
        self,
        *,
        revision_id: str,
        source_refs: Sequence[SourceRef],
        vcs_revision: str | None,
        uri: str,
        result: SurfaceWriteResult,
    ) -> int:
        """One row per source ref, attached to the **revision** (B1-CR-18).

        Not to the item: a v3 change with consequences beyond this build, and the
        reason is that an item's provenance is the provenance of whichever
        revision made the claim -- attaching it to the parent would make every
        historical claim appear to have the newest evidence.
        """
        if not source_refs:
            return 0
        if vcs_revision is None:
            # See the module docstring, B1-CR-36 / OD-4.
            result.gaps.append(
                Gap(
                    identity_uri=uri,
                    reason="provenance_unrecordable",
                    detail="the tree is not a VCS checkout and `SourceType` has no artifact "
                    "member; the source refs are known but unwritable",
                )
            )
            return 0

        observed = self._now()
        rows = [
            Provenance(
                id=new_id("prov"),
                revision_id=revision_id,
                source_type=_VCS_SOURCE_TYPE,  # type: ignore[arg-type]
                source_ref=_render_source_ref(ref),
                observed_at=observed,
            )
            for ref in source_refs
        ]
        self._aux.insert_rows("provenance", rows)
        return len(rows)

    # -- moves ------------------------------------------------------------

    def _apply_moves(
        self,
        *,
        prior: PriorState,
        observations: list[Observation],
        manifest: ExtractorManifest,
        actor_id: str | None,
        result: SurfaceWriteResult,
    ) -> None:
        """Record what `adopt_map.moves` decided -- `02` §4.3 rows 4-5, PRD F5.

        The decision is made there and applied here, because there is one path to
        the store. Note what this does **not** do: it never retires a binding and
        never marks an identity absent. A referent missing from a run may have
        been removed or may simply have defeated the parser, and the two are
        indistinguishable from here (PRD F5.3, B1-CR-07).
        """
        outcome = detect_moves(prior, observations)

        for move in outcome.moves:
            self._record_move(move=move, prior=prior, manifest=manifest, actor_id=actor_id)
            result.revisions_written["identity"] += 1
            entry = prior.get(move.from_uri)
            if entry is not None and entry.binding is not None:
                result.revisions_written["binding"] += 1
            result.moves.append(MoveRecord(from_uri=move.from_uri, to_uri=move.to_uri))

        for declination in outcome.declinations:
            self._write_conflict(
                identity_id=declination.identity_id,
                identity_uri=declination.uri,
                candidates=declination.candidates,
                result=result,
            )

    def _record_move(
        self,
        *,
        move: Move,
        prior: PriorState,
        manifest: ExtractorManifest,
        actor_id: str | None,
    ) -> None:
        """The old identity gains a `moved` revision naming the new one.

        The old identity keeps its URI and its row: a bundle exported last year
        that referenced the old address still resolves, and the alias chain says
        where the referent went. Its bindings gain `moved` revisions of their own
        and are never removed -- bindings are retired, never removed at all, and
        retirement is Build 3's (Build 0 CR-07, `02` §6).
        """
        self._revisions.append_revision(
            parent_id=move.from_identity_id,
            draft=self._identity_draft(
                status=SURFACE_STATUS_MOVED,
                extractor=manifest.id,
                extractor_version=manifest.version,
                alias_of_identity_id=move.to_identity_id,
            ),
            expected_head_id=self._revisions.current_head(move.from_identity_id),
            actor_id=actor_id,
        )
        entry = prior.get(move.from_uri)
        if entry is None or entry.binding is None:
            return
        self._revisions.append_revision(
            parent_id=entry.binding.id,
            draft=self._binding_draft(
                status=SURFACE_STATUS_MOVED,
                extractor=manifest.id,
                extractor_version=manifest.version,
            ),
            expected_head_id=self._revisions.current_head(entry.binding.id),
            actor_id=actor_id,
        )

    def _write_conflict(
        self,
        *,
        identity_id: str,
        identity_uri: str,
        candidates: int,
        result: SurfaceWriteResult,
    ) -> str:
        """One `conflict` row for a declined move -- `02` §6, B1-CR-08.

        Build 1 writes it and never resolves it: Build 3 owns the resolver and
        its precision ladder, and PRD §8's autonomy matrix assigns the resolution
        to *"nobody in Build 1"*.
        """
        conflict_id = new_id("cf")
        self._aux.insert_rows(
            "conflict",
            [
                Conflict(
                    id=conflict_id,
                    identity_id=identity_id,
                    intent_revision_id=None,
                    actual_revision_id=None,
                    detected_at=self._now(),
                    disposition="open",
                )
            ],
        )
        result.conflicts.append(
            ConflictRecord(
                identity_uri=identity_uri, reason="ambiguous_move", candidates=candidates
            )
        )
        return conflict_id

    def _write_audit_event(
        self, *, resolved: ResolvedScope, actor_id: str | None, result: SurfaceWriteResult
    ) -> None:
        """**One per run**, never per fact (`02` §6).

        `detail` is a summary: counters and the run id. No URI, no path, no
        title -- an audit row is a record that a run happened, and per-fact
        content here would put client structure into a table nothing scrubs.
        """
        detail = (
            f'{{"identities_seen":{result.identities_seen},'
            f'"identities_new":{result.identities_new},'
            f'"revisions_identity":{result.revisions_written["identity"]},'
            f'"revisions_knowledge":{result.revisions_written["knowledge"]},'
            f'"revisions_binding":{result.revisions_written["binding"]},'
            f'"gaps":{len(result.gaps)}}}'
        )
        self._aux.insert_rows(
            "audit_event",
            [
                AuditEvent(
                    id=new_id("aud"),
                    firm_id=resolved.firm_id,
                    system_id=resolved.system_id,
                    event_type=AUDIT_EVENT_TYPE,
                    actor_id=actor_id,
                    subject_ref=result.run_id,
                    detail=detail,
                    occurred_at=self._now(),
                )
            ],
        )


def _mintable_key(resolved: ResolvedScope, fact: SurfaceFact) -> str:
    """The normalized key, so the facade mints the URI this writer already did.

    `IdentityFacade.observe` calls `build_uri` itself, from the same scope and
    the same `(kind, namespace, key)`. Handing it the *normalized* key is what
    makes its URI and `mint()`'s the same string -- and `identity.uri` is
    `UNIQUE`, so a difference of one normalization would create a second row for
    one referent.
    """
    del resolved
    return normalize_local_key(fact.identity_kind, fact.local_key)


def _identity_content(entry: PriorIdentity) -> tuple[object, ...]:
    """What an `identity_revision` records, as a comparable tuple.

    The three `_*_content` helpers exist so that *"has this table's content
    changed?"* is answered by naming the columns that table actually has. A
    single shared notion of "changed" would append an identical `binding_revision`
    every time a docstring moved.
    """
    head = entry.identity_head
    if head is None:
        return ()
    return (head.extractor, head.extractor_version, head.confidence, head.status)


def _knowledge_content(entry: PriorIdentity) -> tuple[object, ...]:
    head = entry.knowledge_head
    if head is None:
        return ()
    return (head.body_md, head.confidence)


def _binding_content(entry: PriorIdentity) -> tuple[object, ...]:
    head = entry.binding_head
    if head is None:
        return ()
    return (
        head.extractor,
        head.extractor_version,
        head.confidence,
        head.locator_rung,
        head.status,
    )


def _mint_relation_target(resolved: ResolvedScope, relation: FactRelation) -> str:
    """A relation's target URI, minted from **this run's** scope.

    `02` §5.1 rule 2 makes relation targets identity URIs rather than database
    ids, which is what keeps an exported bundle resolvable off this machine. An
    extractor supplies only `(kind, namespace, local_key)` -- it has no field
    through which to name an environment -- so a staging run's edges point at
    staging identities by construction (PRD F6.1).
    """
    return mint(
        resolved.scope,
        SurfaceFact(
            identity_kind=relation.target_kind,
            namespace=relation.target_namespace,
            local_key=relation.target_local_key,
            title=relation.target_local_key,
        ),
    )


def _locator_rung(fact: SurfaceFact) -> LocatorRung | None:
    """Rung 1 or 2 for a `ui_component`, `None` for everything else (`02` §6)."""
    if fact.identity_kind != _LOCATOR_RUNG_KIND:
        return None
    return _LOCATOR_RUNG_BY_NAMESPACE.get(fact.namespace or "", 1)


def _render_source_ref(ref: SourceRef) -> str:
    """`<repo-relative-path>#L<start>-L<end>@<blob-sha>` -- contracts §6.

    **Never an absolute path.** `SourceRef.path` is repo-relative by construction,
    so there is nothing to strip here and nothing to forget to strip.
    """
    rendered = ref.path
    if ref.start_line is not None:
        rendered += f"#L{ref.start_line}"
        if ref.end_line is not None:
            rendered += f"-L{ref.end_line}"
    if ref.blob_sha is not None:
        rendered += f"@{ref.blob_sha}"
    return rendered
