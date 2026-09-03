"""Bet 4: the disagreement between what the store says and what a probe saw.

*Fails when* a conflict is written for knowledge nobody confirmed, written twice
for one disagreement, or not written at all when a drifted probe contradicts a
confirmed statement. *Matters because* v6.1's fourth bet is that the
contradiction **is** the deliverable -- and a queue that fills with tool-versus-
tool disagreements, or repeats one disagreement on every run, is a queue nobody
opens, which delivers nothing. *No other instrument catches it because* the diff
tests prove drift is detected and the coverage tests prove what "confirmed" means;
only this one proves the join between them writes the right row exactly once.

**The honesty invariant is the negative test that matters.** Unverified
knowledge is not a claim anybody made, so a probe disagreeing with it is two
unconfirmed opinions. The world here is identical in both directions except for
the `verification` value, so the test cannot pass by having built a world in
which nothing could ever conflict.
"""

import datetime as _dt
from collections.abc import Callable

import pytest
from adopt_handover import PackConflict, assemble, render
from adopt_knowledge import rank_conflicts
from adopt_probe import ConflictIntent, conflicting_intents

from adopt_model import (
    Binding,
    BindingRevision,
    Conflict,
    Identity,
    KnowledgeItem,
    KnowledgeRevision,
)
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft
from adopt_store.api import SqliteStoreHandle

pytestmark = pytest.mark.unit

URI = "onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/POST%20%2Fv1%2Forders"


def _world(
    store: SqliteStoreHandle,
    scope: Scope,
    add_audience: Callable[..., None],
    *,
    verification: str | None = "verified",
    binding_status: str = "active",
) -> tuple[str, str]:
    """One identity, one item bound to it. Returns `(identity_id, revision_id)`."""
    identity = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )
    item_id, revision_id = store.items().create(
        scope=scope,
        kind="procedure",
        title="How orders are placed",
        revision=KnowledgeRevisionDraft(
            authority_class="human_confirmed",
            body_md="The endpoint returns 200 with an order_id.",
            verification=verification,  # type: ignore[arg-type]
        ),
    )
    add_audience(item_id=item_id)
    store.bindings().create(
        item_id=item_id,
        identity_id=identity.id,
        is_load_bearing=True,
        revision=BindingRevisionDraft(status=binding_status, locator_rung=1),  # type: ignore[arg-type]
    )
    return identity.id, revision_id


def _intents(store: SqliteStoreHandle, exercises: tuple[str, ...]) -> tuple[ConflictIntent, ...]:
    rows = store.export_records().table_rows
    return conflicting_intents(
        exercises=exercises,
        identities=rows("identity", Identity),
        bindings=rows("binding", Binding),
        binding_revisions=rows("binding_revision", BindingRevision),
        items=rows("knowledge_item", KnowledgeItem),
        knowledge_revisions=rows("knowledge_revision", KnowledgeRevision),
    )


def test_confirmed_knowledge_on_an_exercised_identity_conflicts(
    s4_store: SqliteStoreHandle, s4_scope: Scope, add_audience: Callable[..., None]
) -> None:
    identity_id, revision_id = _world(s4_store, s4_scope, add_audience)

    intents = _intents(s4_store, (URI,))

    assert len(intents) == 1
    assert intents[0].identity_id == identity_id
    # The **confirmed** revision, cited by id: the intent side of the
    # disagreement has to name what somebody actually agreed to.
    assert intents[0].intent_revision_id == revision_id
    assert intents[0].identity_uri == URI


def test_unverified_knowledge_never_conflicts(
    s4_store: SqliteStoreHandle, s4_scope: Scope, add_audience: Callable[..., None]
) -> None:
    """Build 2's honesty invariant, carried forward.

    Identical to the test above but for one enum value. A probe disagreeing with
    a draft is two unconfirmed opinions, and filing that would fill the queue
    with the tool arguing against itself.
    """
    _world(s4_store, s4_scope, add_audience, verification="unverified")

    assert _intents(s4_store, (URI,)) == ()


def test_a_retired_binding_never_conflicts(
    s4_store: SqliteStoreHandle, s4_scope: Scope, add_audience: Callable[..., None]
) -> None:
    """A relationship somebody withdrew is not one the store is still asserting."""
    _world(s4_store, s4_scope, add_audience, binding_status="retired")

    assert _intents(s4_store, (URI,)) == ()


def test_a_probe_declaring_no_exercises_conflicts_with_nothing(
    s4_store: SqliteStoreHandle, s4_scope: Scope, add_audience: Callable[..., None]
) -> None:
    """*Fails when* a drifted probe with no declared linkage invents one.

    *Matters because* the probe observed a change nobody said was about any
    particular thing, and guessing which knowledge it contradicts would put a
    fabricated claim in front of a reviewer.
    """
    _world(s4_store, s4_scope, add_audience)

    assert _intents(s4_store, ()) == ()


def test_a_uri_naming_no_identity_is_skipped_not_raised(
    s4_store: SqliteStoreHandle, s4_scope: Scope, add_audience: Callable[..., None]
) -> None:
    """A probe outliving the identity it named is a stale probe, not a crash."""
    _world(s4_store, s4_scope, add_audience)

    assert _intents(s4_store, ("onboard-v1://a/b/c/d/endpoint/-/gone",)) == ()


def test_the_same_disagreement_is_never_written_twice(
    s4_store: SqliteStoreHandle, s4_scope: Scope, add_audience: Callable[..., None]
) -> None:
    """The dedup, at the port that answers it.

    *Fails when* a rerun files the same conflict again. *Matters because* a
    reviewer who sees one disagreement five times stops reading the list, and
    then the sixth -- a different one -- goes unread too.
    """
    identity_id, revision_id = _world(s4_store, s4_scope, add_audience)
    records = s4_store.probe_run_records()

    assert records.open_conflicts(identity_id=identity_id, intent_revision_id=revision_id) == []

    with records.transaction():
        records.insert_conflict(
            Conflict(
                id="cf_01JQTESTCONFLICT00000000",
                identity_id=identity_id,
                intent_revision_id=revision_id,
                actual_revision_id=None,
                detected_at=_dt.datetime(2026, 8, 26, tzinfo=_dt.UTC),
                disposition="open",
            )
        )

    already = records.open_conflicts(identity_id=identity_id, intent_revision_id=revision_id)
    assert len(already) == 1
    # v1 writes no knowledge from probe output: the intent side cites a real
    # confirmed revision, and nothing is fabricated to point at on the other.
    assert already[0].actual_revision_id is None


def test_two_exercised_uris_for_one_identity_yield_one_intent(
    s4_store: SqliteStoreHandle, s4_scope: Scope, add_audience: Callable[..., None]
) -> None:
    """Dedup inside the join, before anything is written.

    A probe that listed the same identity twice must not produce two rows for
    one disagreement -- the store's dedup read would catch the second, but only
    after the first was already inserted in the same pass.
    """
    _world(s4_store, s4_scope, add_audience)

    assert len(_intents(s4_store, (URI, URI))) == 1


# -- surfacing: the queue and the client's document --------------------------


class _Reader:
    """The three read ports a pack is assembled from, all answering nothing."""

    def knowledge_for_pack(self) -> tuple[()]:
        return ()

    def identities_for_pack(self) -> tuple[()]:
        return ()

    def boundary_for_pack(self) -> None:
        return None


class _Freshness:
    def freshness_of(self, item_id: str) -> str:
        return "fresh"


def _pack_conflict(*, uri: str = URI, when: _dt.datetime | None = None) -> PackConflict:
    return PackConflict(
        uri=uri,
        kind="endpoint",
        intent_revision_id="krev_01AAAAAAAAAAAAAAAAAAAAAAAA",
        detected_at=when or _dt.datetime(2026, 8, 26, 9, 30, tzinfo=_dt.UTC),
    )


def _assembled(conflicts: tuple[PackConflict, ...]) -> object:
    return assemble(
        audience="client_ops",
        knowledge=_Reader(),
        identities=_Reader(),
        freshness=_Freshness(),
        boundary=_Reader(),
        gaps=(),
        conflicts=conflicts,
    )


def test_the_gap_appendix_names_an_open_conflict() -> None:
    """*Fails when* a client's pack stops saying which of its statements is contested.

    *Matters because* Bet 4 is that the contradiction is a **deliverable**: a
    pack that renders a confirmed runbook while a probe disagrees with it is the
    artefact this product exists to replace.
    """
    document = render(_assembled((_pack_conflict(),)))

    assert "Contradicted by observation" in document
    assert URI in document
    assert "krev_01AAAAAAAAAAAAAAAAAAAAAAAA" in document
    # Inside the gap appendix, not in a section of its own: the reader looking at
    # what the pack does not know is the reader who must be told what it says
    # that is now contested.
    appendix = document.split("\n## Coverage gaps", 1)[1]
    assert "Contradicted by observation" in appendix.split("\n## ", 1)[0]


def test_a_store_with_no_conflicts_renders_no_conflict_heading() -> None:
    """Silence is the honest rendering of a question never asked.

    *Fails when* a pack claims agreement it never measured. A store that has
    never run a probe has not verified that anything agrees, so "no conflicts"
    would be a claim, where the gap table's "no uncovered identities" is a fact
    the recompute derived.
    """
    document = render(_assembled(()))

    assert "Contradicted by observation" not in document
    # And the gap appendix itself is unchanged, so every pack Builds 1-4
    # produced renders exactly the bytes it rendered before.
    assert "No uncovered identities." in document


def test_the_pack_stays_byte_stable_with_conflicts_present() -> None:
    """*Fails when* a clock or an unordered iteration reaches the conflict table.

    *Matters because* byte-stability is Build 4's promise and the first thing an
    FDE does with a regenerated pack is diff it against the one they sent last
    week. A conflict list is the newest thing in the appendix and therefore the
    likeliest place for a render clock to appear.
    """
    earlier = _pack_conflict(when=_dt.datetime(2026, 8, 20, tzinfo=_dt.UTC))
    later = _pack_conflict(
        uri="onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/GET%20%2Fv1%2Forders",
        when=_dt.datetime(2026, 8, 25, tzinfo=_dt.UTC),
    )

    first = render(_assembled((earlier, later)))
    # Handed in the opposite order: `assemble` sorts, so the bytes must not move.
    second = render(_assembled((later, earlier)))

    assert first == second
    assert first.index(str(earlier.uri)) < first.index(str(later.uri)), (
        "oldest first -- a contradiction open longest has been wrong longest"
    )


def test_the_gaps_report_lists_open_conflicts_scoped_to_the_identities_it_evaluated(
    s4_store: SqliteStoreHandle, s4_scope: Scope, add_audience: Callable[..., None]
) -> None:
    """*Fails when* `adopt gaps` stops carrying Bet 4's deliverable, or carries
    one from another scope.

    *Matters because* the gap queue is where an FDE looks for work, and a
    contradiction is work. A conflict recorded against another system appearing
    here would be reporting on a scope nobody asked about.
    """
    identity_id, revision_id = _world(s4_store, s4_scope, add_audience)
    records = s4_store.probe_run_records()
    with records.transaction():
        records.insert_conflict(
            Conflict(
                id="cf_01JQTESTCONFLICT00000001",
                identity_id=identity_id,
                intent_revision_id=revision_id,
                actual_revision_id=None,
                detected_at=_dt.datetime(2026, 8, 26, tzinfo=_dt.UTC),
                disposition="open",
            )
        )

    rows = s4_store.export_records().table_rows("conflict", Conflict)

    in_scope = rank_conflicts(rows, {identity_id: URI})
    assert len(in_scope) == 1
    assert in_scope[0].uri == URI
    assert in_scope[0].intent_revision_id == revision_id

    # The same row, with the identity out of scope: dropped, not renamed.
    assert rank_conflicts(rows, {}) == ()


def test_a_dispositioned_conflict_leaves_the_queue_but_not_the_store(
    s4_store: SqliteStoreHandle, s4_scope: Scope, add_audience: Callable[..., None]
) -> None:
    """Representable, never resolved away.

    *Fails when* a decided conflict either keeps nagging or disappears. *Matters
    because* v6.1 Bet 4 requires the disagreement to remain **recorded** after a
    human decides about it -- the decision is the deliverable's other half, and
    a row that vanished would erase the fact that the tool was right.
    """
    identity_id, revision_id = _world(s4_store, s4_scope, add_audience)
    records = s4_store.probe_run_records()
    with records.transaction():
        records.insert_conflict(
            Conflict(
                id="cf_01JQTESTCONFLICT00000002",
                identity_id=identity_id,
                intent_revision_id=revision_id,
                actual_revision_id=None,
                detected_at=_dt.datetime(2026, 8, 26, tzinfo=_dt.UTC),
                disposition="drift_accepted",
            )
        )

    rows = s4_store.export_records().table_rows("conflict", Conflict)
    assert len(rows) == 1, "the row stays in the store"
    assert rank_conflicts(rows, {identity_id: URI}) == (), "and out of the open queue"
