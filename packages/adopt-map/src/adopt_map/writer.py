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
"""

import datetime as _dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from adopt_const import MAP_MIN_EMIT_CONFIDENCE, SURFACE_REPORT_VERSION
from adopt_map.minting import mint
from adopt_map.ports import SurfaceAuxRecords
from adopt_map.schemas.surface import EvidenceMethod, ExtractorManifest, SourceRef, SurfaceFact
from adopt_map.scope_resolve import ResolvedScope
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
    conflicts: list[str] = field(default_factory=list)


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
        knowledge_draft: type,
        binding_draft: type,
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
        self._knowledge_draft = knowledge_draft
        self._binding_draft = binding_draft
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
            for fact in facts:
                self._write_fact(
                    resolved=resolved,
                    manifest=manifest,
                    permitted=permitted,
                    confidence=confidence,
                    fact=fact,
                    vcs_revision=vcs_revision,
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
        # Validated, not serialized: the closed schema is the egress allowlist
        # (`02` §5.1), and **S1.2 owns the front-matter serializer** (`05` S1.2).
        # Validating here means an extractor emitting an undeclared attribute
        # fails now rather than in the sprint that starts writing them out.
        fact.validated_attributes()

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

        existing = self._identities.find_by_uri(uri)
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
        identity_id = identity.id
        result.identities_seen += 1
        if existing is None:
            result.identities_new += 1
            result.revisions_written["identity"] += 1

        item_id, revision_id = self._items.create(
            scope=resolved.scope,
            kind=SURFACE_ITEM_KIND,
            title=fact.title,
            revision=self._knowledge_draft(
                authority_class=SURFACE_AUTHORITY_CLASS,
                verification=SURFACE_VERIFICATION,
                # Prose only. The `attrs_version: 1` front-matter block is
                # `02` §5 and lands in S1.2 with its round-trip suite.
                body_md=fact.prose,
                confidence=confidence,
                # `source_version` is the §4 composite and lands in S1.2, which
                # owns the projection table. `None` here is honest: there is no
                # digest yet, and a placeholder would make the idempotence
                # comparison compare two placeholders and find them equal.
                source_version=None,
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
                status="active",
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
        # pack's `identity_absent_days` is not an enum member (B1-CR-19).
        self._aux.insert_rows(
            "death_condition",
            [DeathCondition(item_id=item_id, condition=SURFACE_DEATH_CONDITION, threshold=None)],
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

    def write_conflict(self, *, identity_id: str, result: SurfaceWriteResult) -> str:
        """One `conflict` row for an ambiguous move -- `02` §6, B1-CR-08.

        Build 1 writes it and never resolves it: Build 3 owns the resolver and
        its precision ladder. Present in S1.1 because the writer owns every write
        to the store; the move rule that calls it lands in S1.2.
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
        result.conflicts.append(conflict_id)
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
    from adopt_map.minting import normalize_local_key

    del resolved
    return normalize_local_key(fact.identity_kind, fact.local_key)


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
