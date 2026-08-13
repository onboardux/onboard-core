"""Build `ResolvedScope`, or abort -- contracts §2, implementation spec §5.2.

Five rules in order, four aborts, **and nothing is ever created**. A surface
extraction that cannot name its environment is a configuration error, not a
default (PRD F1.2): "default to production" is exactly the failure the mandatory
environment segment exists to prevent, so a system with two environments and no
`--environment` aborts and lists both rather than choosing.

**Every abort writes nothing.** That is `02` §10 C8 and it is asserted per path
rather than once: this module has no writer, no `RevisionWriter` and no
`SurfaceAuxRecords`, so a write is not something it declines to do -- it is
something it cannot do. The tests assert the store is byte-identical afterwards
anyway, because "cannot" is a claim about today's code.

**Every abort prints the exact remediation command.** A run that stops without
saying what to type is a run the operator resolves by guessing, and the first
guess is `adopt init`, which would create the scope row this build must never
create (PRD F1.3).

**Ids and slugs are both carried, and they are not interchangeable** (B1-CR-25).
Ids populate the four scope columns on every written row; slugs build the URI,
because a URI built from an internal ULID stops resolving the moment the store
leaves our hands (Build 0 CR-05). Resolving one from the other per identity is
how a lookup ends up inside a loop.
"""

from typing import Final, Protocol, cast

from pydantic import BaseModel, ConfigDict

from adopt_map.ports import ScopeLookupRecords
from adopt_model import Engagement, Environment, Firm, System
from adopt_model._enums import Archetype, Tier
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope, ScopeNode

__all__ = ["ResolvedScope", "resolve_scope"]

#: The tier at which the boundary declines observation entirely (PRD F13.2).
DECLINED_TIER: Final[Tier] = "T0"


class ResolvedScope(BaseModel):
    """The one `(firm, engagement, system, environment)` a run operates in.

    **Frozen after construction** (`03` §5.2 invariant 4) and passed read-only
    into every extractor. An extractor has no API through which to change it,
    which is what makes PRD F6 structural rather than procedural.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Ids -- what the four scope columns on every written row carry.
    firm_id: str
    engagement_id: str
    system_id: str
    environment_id: str
    # Slugs -- what the URI is built from. Immutable, never reissued (CR-05).
    scope: Scope
    environment_slug: str
    archetype: Archetype
    tier: Tier
    data_residency_region: str | None = None
    retention_policy_id: str | None = None


class _Identified(Protocol):
    """Every scope row the manifest generates carries a ULID primary key."""

    id: str


def _declined(code: ErrorCode, message: str, remediation: str) -> AdoptError:
    """An abort that exits 4 and tells the operator exactly what to run."""
    return AdoptError(code, message=message, hint=remediation)


def _one[TModel: BaseModel](
    records: ScopeLookupRecords, table: str, model_type: type[TModel], row_id: str
) -> TModel | None:
    """The row with this id, or `None`.

    A full read and a filter rather than an indexed lookup, because Build 0's
    `ScopeRecords` exposes no get-by-id for firm, engagement or environment
    (OD-2). The scope tables hold a handful of rows per store, so the absent
    index costs nothing measurable and the alternative is editing a protected
    package.

    The cast is the one assumption this helper makes and it is stated rather than
    implied: every scope row the manifest generates carries `id: str`, and the
    port's type variable is bound to `BaseModel`, which does not.
    """
    for row in records.table_rows(table, model_type):
        if cast("_Identified", row).id == row_id:
            return row
    return None


def _environments_of(records: ScopeLookupRecords, system_id: str) -> list[Environment]:
    """Every environment of a system, ordered by slug so the abort message is stable."""
    rows = [
        row for row in records.table_rows("environment", Environment) if row.system_id == system_id
    ]
    return sorted(rows, key=lambda row: row.slug)


def _resolve_environment(
    records: ScopeLookupRecords, system: System, environment_id: str | None
) -> Environment:
    """Rule 3. **There is no default environment.**"""
    candidates = _environments_of(records, system.id)

    if environment_id is not None:
        found = next((row for row in candidates if row.id == environment_id), None)
        if found is None:
            raise _declined(
                ErrorCode.MAP_SCOPE_UNRESOLVED,
                f"environment {environment_id!r} does not exist under system {system.slug!r}",
                f"Create it first: adopt init environment --system {system.id} "
                "--slug <slug> --name <name>. `adopt map` never creates a scope row: "
                "an environment is an engagement-scope decision, not an extraction "
                "artifact.",
            )
        return found

    if not candidates:
        raise _declined(
            ErrorCode.MAP_SCOPE_UNRESOLVED,
            f"system {system.slug!r} has no environment",
            f"Create one first: adopt init environment --system {system.id} "
            "--slug prod --name Production. Every identity URI carries an "
            "environment segment, so a run with none has nothing to mint into.",
        )

    if len(candidates) > 1:
        listed = ", ".join(f"{row.slug} ({row.id})" for row in candidates)
        raise _declined(
            ErrorCode.MAP_ENVIRONMENT_AMBIGUOUS,
            f"system {system.slug!r} has {len(candidates)} environments and none was named: {listed}",
            f"Name one: adopt map --environment {candidates[0].id}. There is no "
            "default environment -- defaulting to production is precisely the "
            "failure the mandatory environment segment exists to prevent.",
        )

    return candidates[0]


def resolve_scope(
    records: ScopeLookupRecords,
    *,
    firm_id: str,
    engagement_id: str,
    system_id: str,
    environment_id: str | None,
    archetype: Archetype,
    tier: Tier | None,
) -> ResolvedScope:
    """Contracts §2 rules 1-5, in order. Creates nothing.

    Args:
        records: The read port. Rule 1 (config, env, CLI overrides) happens in
            the CLI and arrives here as the four ids -- this function is where
            they are *validated*, which is the part that must be identical
            whatever supplied them.
        firm_id: Must exist.
        engagement_id: Must exist **and belong to** `firm_id`.
        system_id: Must exist and belong to `engagement_id`.
        environment_id: Must exist and belong to `system_id`, or be `None` when
            the system has exactly one environment.
        archetype: From Build 0's detector, or the `--archetype` override.
        tier: From `observability_boundary`, read-only. `None` means no boundary
            row exists, which aborts -- Build 1 never writes a provisional one
            (B1-CR-06).

    Returns:
        The frozen `ResolvedScope`.

    Raises:
        AdoptError: ``MAP_SCOPE_UNRESOLVED`` when an id does not exist or the
            parent chain is broken; ``MAP_ENVIRONMENT_AMBIGUOUS``;
            ``MAP_BOUNDARY_MISSING``; ``MAP_TIER_DECLINED``. Each exits 4 and
            each writes nothing.
    """
    # Rule 2, outermost first. Each level is checked for existence **and** for
    # parentage, because an id that exists under a different parent is the
    # failure that silently maps one client's system into another's engagement.
    firm = _one(records, "firm", Firm, firm_id)
    if firm is None:
        raise _declined(
            ErrorCode.MAP_SCOPE_UNRESOLVED,
            f"firm {firm_id!r} does not exist",
            "Create it first: adopt init firm --slug <slug> --name <name>.",
        )

    engagement = _one(records, "engagement", Engagement, engagement_id)
    if engagement is None:
        raise _declined(
            ErrorCode.MAP_SCOPE_UNRESOLVED,
            f"engagement {engagement_id!r} does not exist",
            f"Create it first: adopt init engagement --firm {firm.id} --slug <slug> --name <name>.",
        )
    if engagement.firm_id != firm.id:
        raise _declined(
            ErrorCode.MAP_SCOPE_UNRESOLVED,
            f"engagement {engagement_id!r} belongs to firm {engagement.firm_id!r}, "
            f"not {firm.id!r}: the scope chain is broken",
            "Name the engagement's own firm, or the engagement under this firm. A "
            "broken chain is refused rather than repaired -- every written row "
            "carries all four scope columns, and a mismatched pair is how one "
            "client's system lands inside another client's engagement.",
        )

    system = _one(records, "system", System, system_id)
    if system is None:
        raise _declined(
            ErrorCode.MAP_SCOPE_UNRESOLVED,
            f"system {system_id!r} does not exist",
            f"Create it first: adopt init system --engagement {engagement.id} "
            "--slug <slug> --name <name>.",
        )
    if system.engagement_id != engagement.id:
        raise _declined(
            ErrorCode.MAP_SCOPE_UNRESOLVED,
            f"system {system_id!r} belongs to engagement {system.engagement_id!r}, "
            f"not {engagement.id!r}: the scope chain is broken",
            "Name the system's own engagement. See the note on the engagement check.",
        )

    # Rule 3.
    environment = _resolve_environment(records, system, environment_id)
    if environment.system_id != system.id:  # pragma: no cover -- filtered above
        raise _declined(
            ErrorCode.MAP_SCOPE_UNRESOLVED,
            f"environment {environment.id!r} belongs to system {environment.system_id!r}, "
            f"not {system.id!r}: the scope chain is broken",
            "Name the environment's own system.",
        )

    # Rule 4. Build 1 reads the boundary; Build 2 (Doc 8) writes it.
    if tier is None:
        raise _declined(
            ErrorCode.MAP_BOUNDARY_MISSING,
            f"no observability_boundary row exists for system {system.slug!r} "
            f"environment {environment.slug!r}",
            f"Declare it first: adopt boundary --system {system.id} "
            f"--environment {environment.id}. `adopt map` does not write a "
            "provisional boundary (B1-CR-06): inventing one would let an "
            "unnegotiated tier silently license claims about a client's system.",
        )
    if tier == DECLINED_TIER:
        raise _declined(
            ErrorCode.MAP_TIER_DECLINED,
            f"the negotiated tier for {system.slug}/{environment.slug} is "
            f"{DECLINED_TIER}, which permits no observation",
            "Re-negotiate the boundary with the client and re-declare it: "
            f"adopt boundary --system {system.id} --environment {environment.id}. "
            f"{DECLINED_TIER} is a decision, not an error -- nothing was extracted "
            "and nothing was written.",
        )

    return ResolvedScope(
        firm_id=firm.id,
        engagement_id=engagement.id,
        system_id=system.id,
        environment_id=environment.id,
        scope=Scope(
            firm=ScopeNode(id=firm.id, slug=firm.slug),
            engagement=ScopeNode(id=engagement.id, slug=engagement.slug),
            system=ScopeNode(id=system.id, slug=system.slug),
            environment=ScopeNode(id=environment.id, slug=environment.slug),
        ),
        environment_slug=environment.slug,
        archetype=archetype,
        tier=tier,
        # Rule 5. Inherited from the resolved rows, stamped onto every written
        # row where the column exists. `retention_policy_id` has **no source row
        # in schema v3** -- neither `environment` nor `system` carries the column
        # -- so it resolves to `None` until Build 0 gains one. Stated rather than
        # silently omitted, so nobody re-derives the absence as a bug.
        data_residency_region=environment.data_residency_region,
        retention_policy_id=None,
    )
