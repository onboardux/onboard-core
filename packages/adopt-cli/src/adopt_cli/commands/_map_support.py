"""Store lookups `adopt map` needs: which scope, and which archetype.

Separate from `map_command` so the command body stays the lazy-import boundary
and these can be tested against a store without going through typer.

Both read through `export_records`, the same read port `adopt export` uses.
`ScopeRecords` finds a level by *slug within its parent*, which is the lookup
`resolve` needs and the opposite of the one here: this module starts from an
environment row and walks outwards by id. Reading the four scope tables and
joining them in memory is a handful of rows and needs no new port -- and a new
port would mean a new realization and a new escape case in the plane.
"""

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from adopt_model import Engagement, Environment, Firm, System
from adopt_model._enums import Archetype
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope, ScopeFacade

__all__ = ["StoreView", "resolve_scope", "system_archetype"]


class _ExportReader(Protocol):
    """Mirrors `table_rows` exactly -- same parameter name, bound and return."""

    def table_rows[TModel: BaseModel](
        self, table: str, model_type: type[TModel]
    ) -> Sequence[TModel]: ...


class StoreView(Protocol):
    """The slice of the store handle these helpers read.

    Structural rather than imported, for the same reason `adopt_map` declares
    `IdentityWriter`: `adopt_cli.store_option` is the *only* module the
    `no-raw-sqlite` contract exempts, so every other CLI module reaches the store
    through a shape rather than through `adopt_store`.
    """

    def scope(self) -> ScopeFacade: ...

    def export_records(self) -> _ExportReader: ...


def resolve_scope(handle: StoreView, scope: str | None) -> Scope:
    """The scope to map into: the one given, or the store's only one.

    `adopt map` with no `--scope` is the demo's second line, so a store holding
    exactly one environment must not require the operator to retype what `init`
    already recorded. More than one is refused rather than guessed: writing a
    client's identities into the wrong environment is not a mistake anyone
    notices quickly, and every one of those rows carries that scope forever.

    Raises:
        AdoptError: ``SCOPE_VIOLATION`` when the store holds no environment, or
            holds several and none was named.
    """
    if scope is not None:
        return handle.scope().resolve(scope)

    environments = _rows(handle, "environment", Environment)
    if not environments:
        raise AdoptError(
            ErrorCode.SCOPE_VIOLATION,
            message="the store holds no environment to map into",
            hint="Run `adopt init . --scope <firm/engagement/system/environment>` first. "
            "Every identity carries all four scope levels.",
        )
    if len(environments) > 1:
        raise AdoptError(
            ErrorCode.SCOPE_VIOLATION,
            message=f"the store holds {len(environments)} environments; --scope names which",
            hint="Pass --scope firm/engagement/system/environment. A default here would "
            "write identities into whichever environment happened to sort first.",
        )
    return handle.scope().resolve(_path_of(handle, environments[0]))


def system_archetype(handle: StoreView, scope: Scope) -> Archetype | None:
    """`system.archetype` for the scope's system, as `adopt init` recorded it."""
    if scope.system is None:
        return None
    for system in _rows(handle, "system", System):
        if system.id == scope.system.id:
            return system.archetype
    return None


def _rows[TModel: BaseModel](handle: StoreView, table: str, model: type[TModel]) -> list[TModel]:
    return list(handle.export_records().table_rows(table, model))


def _path_of(handle: StoreView, environment: Environment) -> str:
    """Rebuild `firm/engagement/system/environment` from an environment row."""
    systems = {row.id: row for row in _rows(handle, "system", System)}
    engagements = {row.id: row for row in _rows(handle, "engagement", Engagement)}
    firms = {row.id: row for row in _rows(handle, "firm", Firm)}

    system = systems.get(environment.system_id)
    engagement = engagements.get(system.engagement_id) if system is not None else None
    firm = firms.get(engagement.firm_id) if engagement is not None else None
    if system is None or engagement is None or firm is None:
        raise AdoptError(
            ErrorCode.SCOPE_VIOLATION,
            message=f"the scope chain above environment {environment.slug!r} is incomplete",
            hint="The store is inconsistent; `adopt store doctor` reports on it.",
        )
    return f"{firm.slug}/{engagement.slug}/{system.slug}/{environment.slug}"
