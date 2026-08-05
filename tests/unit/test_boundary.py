"""The observability boundary: one row, two renderings, and no way to disagree.

*Fails when* the human-readable statement stops carrying a fact the machine-
readable one carries, when a boundary starts being updated instead of appended,
or when an `ai` system below its floor stops naming what it cannot do. *Matters
because* the statement is what a client reads and later signs while the JSON is
what the system enforces: a product where those two can drift shows a customer a
promise it does not keep. *No other instrument catches it because* the tier
tests assert what the answers mean, not what is written down, and nothing else
compares the two artifacts.
"""

import datetime as _dt

import pytest

from adopt_detect import (
    DEFAULT_OUTBOUND_CATEGORIES,
    METADATA_ONLY,
    Answers,
    BoundaryView,
    declare_boundary,
    negotiate,
    render_json,
    render_markdown,
)
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

T4 = Answers(artifact_access=True, deploy_signal=True, safe_interaction=True)
T2 = Answers(artifact_access=True, deploy_signal=True, safe_interaction=False)
T0 = Answers(artifact_access=False, deploy_signal=False, safe_interaction=False)


def _declare(
    store: SqliteStoreHandle, scope: Scope, answers: Answers, archetype: str | None = None
):
    return declare_boundary(
        store.boundary(),
        scope=scope,
        decision=negotiate(answers),
        archetype=archetype,  # type: ignore[arg-type]
    )


@pytest.mark.unit
def test_the_two_renderings_agree_field_by_field(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """F10.8, asserted rather than asserted-about.

    Every value the JSON artifact carries must appear in the statement. The
    check is textual on purpose: it is what a client comparing the two documents
    would do, and it catches a renderer that quietly drops a field.
    """
    view = _declare(s4_store, s4_scope, T2, archetype="web")
    payload = render_json(view)
    statement = render_markdown(view)

    for key, value in payload.items():
        if value is None or isinstance(value, bool):
            continue
        for item in value if isinstance(value, list) else [value]:
            assert str(item) in statement, f"{key}={item!r} is in the JSON and not the statement"


@pytest.mark.unit
def test_both_renderings_come_from_one_row(s4_store: SqliteStoreHandle, s4_scope: Scope) -> None:
    """The structural claim: both functions take a `BoundaryView` and nothing else.

    A renderer that reached for a store or a config key could disagree with the
    other one; one that takes only the view cannot.
    """
    view = _declare(s4_store, s4_scope, T4, archetype="ai")
    assert render_json(view)["boundary_id"] == view.boundary_id
    assert view.boundary_id in render_markdown(view)


@pytest.mark.unit
def test_an_ai_system_below_t3_is_recorded_as_a_violation_naming_its_capabilities(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """PRD F10.7, both halves: the violation *and* the named capabilities."""
    view = _declare(s4_store, s4_scope, T2, archetype="ai")
    assert view.archetype_floor_violated is True
    assert view.unavailable_capabilities
    statement = render_markdown(view)
    assert "Boundary violation" in statement
    for capability in view.unavailable_capabilities:
        assert capability in statement


@pytest.mark.unit
def test_an_ai_system_at_t4_is_not_a_violation(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    view = _declare(s4_store, s4_scope, T4, archetype="ai")
    assert view.archetype_floor_violated is False
    assert "Boundary violation" not in render_markdown(view)


@pytest.mark.unit
def test_t0_records_a_boundary_and_recommends_declining(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """A declined engagement is exactly the one whose boundary someone reads later.

    `T0` is a finding about the engagement, not a refusal to record what was
    negotiated -- so the row exists and the recommendation is on it.
    """
    view = _declare(s4_store, s4_scope, T0)
    assert view.tier == "T0"
    assert view.decline_recommended is True
    assert "Recommendation: decline" in render_markdown(view)
    assert (
        s4_store.boundary().current(system_id=view.system_id, environment_id=view.environment_id)
        is not None
    )


@pytest.mark.unit
def test_permitted_outbound_defaults_to_metadata_only(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """PRD F10.5 and contracts §8 rule 1."""
    view = _declare(s4_store, s4_scope, T4)
    assert view.permitted_outbound_categories == DEFAULT_OUTBOUND_CATEGORIES
    assert view.permits(METADATA_ONLY) is True
    assert view.permits("full_content") is False


@pytest.mark.unit
def test_declaring_again_appends_and_current_reads_the_newest(
    s4_store: SqliteStoreHandle, s4_scope: Scope, s4_clock
) -> None:
    """*Fails when* re-negotiation starts updating a boundary in place.

    *Matters because* the boundary is what the engagement was told it may
    observe. A row that could be edited cannot answer "what did we claim in
    March", and later builds put a client signature against exactly that.
    """
    first = _declare(s4_store, s4_scope, T2)
    s4_clock.advance(_dt.timedelta(seconds=1))
    second = _declare(s4_store, s4_scope, T4)

    assert first.boundary_id != second.boundary_id
    assert s4_scope.system is not None and s4_scope.environment is not None
    current = s4_store.boundary().current(
        system_id=s4_scope.system.id, environment_id=s4_scope.environment.id
    )
    assert current is not None
    assert current.id == second.boundary_id
    assert current.tier == "T4"

    rows = s4_store.backend.query("SELECT id FROM observability_boundary ORDER BY id")
    assert len(rows) == 2, "re-declaring must append, not update"


@pytest.mark.unit
def test_a_system_wide_boundary_and_an_environment_one_are_different_rows(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """`environment_id IS NULL` is the system-wide declaration, not "any environment".

    *Fails when* the lookup starts matching a NULL with `= ?`, which returns
    nothing in SQL and would report "no boundary" for every system-wide one.
    """
    assert s4_scope.system is not None and s4_scope.environment is not None
    system_only = Scope(firm=s4_scope.firm, engagement=s4_scope.engagement, system=s4_scope.system)
    wide = _declare(s4_store, system_only, T2)
    narrow = _declare(s4_store, s4_scope, T4)

    facade = s4_store.boundary()
    assert facade.current(system_id=s4_scope.system.id) is not None
    assert facade.current(system_id=s4_scope.system.id).id == wide.boundary_id  # type: ignore[union-attr]
    assert (
        facade.current(system_id=s4_scope.system.id, environment_id=s4_scope.environment.id).id  # type: ignore[union-attr]
        == narrow.boundary_id
    )


@pytest.mark.unit
def test_a_boundary_needs_a_system(s4_store: SqliteStoreHandle, s4_scope: Scope) -> None:
    firm_only = Scope(firm=s4_scope.firm)
    with pytest.raises(AdoptError) as caught:
        _declare(s4_store, firm_only, T4)
    assert caught.value.code is ErrorCode.SCOPE_VIOLATION


@pytest.mark.unit
def test_the_view_is_frozen(s4_store: SqliteStoreHandle, s4_scope: Scope) -> None:
    """A validator that could widen the boundary it validates against is not one."""
    view = _declare(s4_store, s4_scope, T4)
    with pytest.raises((AttributeError, TypeError)):
        view.permitted_outbound_categories = ("full_content",)  # type: ignore[misc]


@pytest.mark.unit
def test_an_ambiguous_archetype_renders_as_ambiguous(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    view = _declare(s4_store, s4_scope, T4, archetype=None)
    assert render_json(view)["archetype"] is None
    assert "ambiguous" in render_markdown(view)


@pytest.mark.unit
def test_the_stored_row_projects_back_to_the_same_view(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """*Fails when* a round trip through the store loses a field the gate reads.

    `permitted_outbound_categories` is a JSON column; a realization that returned
    it as a string would make `permits()` compare a policy against characters.
    """
    view = _declare(s4_store, s4_scope, T4, archetype="ai")
    assert s4_scope.system is not None and s4_scope.environment is not None
    row = s4_store.boundary().current(
        system_id=s4_scope.system.id, environment_id=s4_scope.environment.id
    )
    assert row is not None
    reloaded = BoundaryView.of(row, archetype="ai")
    assert reloaded.permitted_outbound_categories == view.permitted_outbound_categories
    assert reloaded.permits(METADATA_ONLY) is True
    assert reloaded.tier == view.tier
