"""The three review actions, asserted by their **store-state consequence**.

*Fails when* an action stamps the queue and leaves the store saying something
else. *Matters because* the resolution enum is the disposition, not the record:
`retire` and `rebind` both stamp `corrected`, so a test asserting the returned
value would pass for an action that did nothing at all -- and a reviewer working
a queue whose buttons stamp without acting is doing unpaid data entry. *No other
instrument catches it because* every write here is individually valid: a
terminal revision, a `moved` binding revision and a freshness column are all
things the store is happy to hold whether or not a reviewer asked for them.

Each test therefore ends at `resolve_freshness` or at the row itself, never at
the `ChangeOutcome`, except where the outcome is the *only* record of something
-- `still_stale`, which exists to be printed.
"""

import pytest
from adopt_knowledge import (
    ACTION_CONFIRM_CURRENT,
    ChangedBinding,
    PendingItem,
    confirm_current_item,
    rebind_item,
    retire_item,
    still_stale_after_confirm,
)

from adopt_freshness import RULE_BINDING_STALE, RULE_SOURCE_IDENTITY_DEAD, resolve_freshness
from adopt_model import BindingRevision, KnowledgeRevision
from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft
from adopt_store.api import SqliteStoreHandle

_BATCH_KEY = "refresh:run_01J0TESTRUNCHANGEACTION"
_SEMANTICS = "BINDING_INTACT_SEMANTICS_CHANGED"
_DEAD = "BINDING_DEAD"


def _queued(
    store: SqliteStoreHandle,
    scope: Scope,
    *,
    key: str = "POST /v1/orders",
    batch_key: str = _BATCH_KEY,
    is_load_bearing: bool = True,
) -> tuple[PendingItem, ChangedBinding, str]:
    """One queued change item, its link, and the identity that changed.

    Built through the facades rather than by inserting rows, so the subject
    under test is the shape `adopt refresh` actually produces.
    """
    assert scope.system is not None
    identity = store.identities().observe(scope=scope, kind="endpoint", namespace=None, key=key)
    item_id, revision_id = store.items().create(
        scope=scope,
        kind="answer",
        title=f"About {key}",
        revision=KnowledgeRevisionDraft(
            authority_class="artifact_observed",
            body_md="Orders are created here.",
            verification="verified",
        ),
    )
    binding_id, _ = store.bindings().create(
        item_id=item_id,
        identity_id=identity.id,
        is_load_bearing=is_load_bearing,
        revision=BindingRevisionDraft(status="active"),
    )
    batch_id, review_item_ids = store.governance().open_batch(
        system_id=str(scope.system.id), batch_key=batch_key, items=[(item_id, None)]
    )
    pending = PendingItem(
        review_item_id=review_item_ids[0],
        review_batch_id=batch_id,
        batch_key=batch_key,
        item_id=item_id,
        title=f"About {key}",
        suggestions=(),
        body_md="Orders are created here.",
        head_revision_id=revision_id,
    )
    link = ChangedBinding(
        binding_id=binding_id,
        item_id=item_id,
        identity_id=identity.id,
        identity_uri=identity.uri,
        impact_class=_SEMANTICS,
        is_load_bearing=is_load_bearing,
    )
    return pending, link, identity.id


@pytest.mark.unit
class TestRetire:
    def test_retiring_ends_the_item_and_stamps_the_queue_corrected(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope, s4_clock: ManualClock
    ) -> None:
        """*Fails when* `retire` records a disposition and leaves the note
        serving. *Matters because* the whole point of the action is that an
        obsolete answer stops being an answer -- an item stamped `corrected` in
        the queue and still resolving as live knowledge is the rot the build
        exists to delete, now with an audit trail claiming it was handled."""
        pending, _, _ = _queued(s4_store, s4_scope)

        outcome = retire_item(pending, reviews=s4_store.governance(), knowledge=s4_store.items())

        assert outcome.resolution == "corrected"
        item = s4_store.items().get(pending.item_id)
        assert item is not None
        assert item.freshness_state == "retired"
        resolution = resolve_freshness(
            s4_store.freshness_records(), pending.item_id, clock=s4_clock
        )
        assert resolution.state == "retired"
        # The revision is appended, never a delete: "what did we believe in
        # March" still answers after the note is withdrawn.
        assert outcome.revision_id is not None
        assert item.current_revision_id == outcome.revision_id


@pytest.mark.unit
class TestRebind:
    def test_rebinding_supersedes_the_old_link_and_returns_the_item_to_service(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope, s4_clock: ManualClock
    ) -> None:
        """**The action's whole reason to exist, and the one it is easiest to
        half-build.**

        *Fails when* a rebind creates the new binding and leaves the old one
        blocking. *Matters because* the item would stay STALE forever with a
        queue entry stamped resolved -- and a reviewer who watches one button
        change nothing learns the queue is decorative, which is the failure H5
        names. *No other instrument catches it because* both bindings are
        individually valid rows and the new one is exactly right."""
        pending, link, identity_id = _queued(s4_store, s4_scope)
        # The referent died: this is what makes the *old* link a blocker, so
        # returning the item to service can only come from the supersede.
        s4_store.identities().retire(identity_id=identity_id, reason="removed")
        successor = s4_store.identities().observe(
            scope=s4_scope, kind="endpoint", namespace=None, key="POST /v2/orders"
        )
        blocked = resolve_freshness(s4_store.freshness_records(), pending.item_id, clock=s4_clock)
        assert blocked.deciding_rule == RULE_SOURCE_IDENTITY_DEAD

        outcome = rebind_item(
            pending,
            reviews=s4_store.governance(),
            bindings=s4_store.bindings(),
            affected=[link],
            target_identity_id=successor.id,
            target_uri=successor.uri,
        )

        assert outcome.superseded_bindings == (link.binding_id,)
        assert outcome.new_binding_id is not None
        # The old chain kept every revision it had; only its head changed.
        old = s4_store.bindings().get(link.binding_id)
        assert old is not None
        head = next(
            row
            for row in s4_store.export_records().table_rows("binding_revision", BindingRevision)
            if row.id == old.current_revision_id
        )
        assert head.status == "moved"
        # And the item is serviceable again: the superseded link is skipped and
        # the successor's binding is what anchors it now.
        served = resolve_freshness(s4_store.freshness_records(), pending.item_id, clock=s4_clock)
        assert served.deciding_rule != RULE_SOURCE_IDENTITY_DEAD
        assert served.state != "stale"

    def test_rebinding_an_item_with_no_changed_link_is_refused(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* a rebind on an unaffected item silently adds a binding.
        *Matters because* the item would end up load-bearingly bound to two
        referents with nothing superseded -- coverage counted twice, and every
        future change to either one staling it."""
        pending, _, _ = _queued(s4_store, s4_scope)
        successor = s4_store.identities().observe(
            scope=s4_scope, kind="endpoint", namespace=None, key="POST /v2/orders"
        )

        with pytest.raises(AdoptError) as raised:
            rebind_item(
                pending,
                reviews=s4_store.governance(),
                bindings=s4_store.bindings(),
                affected=[],
                target_identity_id=successor.id,
                target_uri=successor.uri,
            )

        assert raised.value.code is ErrorCode.BIND_TARGET_NOT_FOUND
        # Refused **before** the disposition was stamped, so the entry is still
        # answerable. A refusal that consumed the item would be worse than the
        # mistake it caught.
        item = s4_store.governance().get_item(pending.review_item_id)
        assert item is not None
        assert item.resolution is None


@pytest.mark.unit
class TestConfirmCurrent:
    def test_confirming_appends_a_human_revision_and_un_stales_the_link(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope, s4_clock: ManualClock
    ) -> None:
        """*Fails when* a confirmation stamps the queue and leaves the binding
        stale. *Matters because* the item would come back in the next refresh's
        queue -- or worse, keep answering STALE to a question a human just
        confirmed the answer to -- and re-asking a question already answered is
        how a review queue becomes noise."""
        pending, link, _ = _queued(s4_store, s4_scope)
        s4_store.changes().stale_load_bearing_bindings(
            s4_store.bindings().for_identity(link.identity_id), identity_ids=[link.identity_id]
        )
        stale = resolve_freshness(s4_store.freshness_records(), pending.item_id, clock=s4_clock)
        assert stale.deciding_rule == RULE_BINDING_STALE

        outcome = confirm_current_item(
            pending,
            reviews=s4_store.governance(),
            knowledge=s4_store.items(),
            freshener=s4_store.changes(),
            affected=[link],
        )

        assert outcome.resolution == "confirmed"
        assert outcome.freshened_bindings == (link.binding_id,)
        assert outcome.still_stale == ()
        # A person's name is on the new revision, and its provenance says so --
        # `artifact_observed` cannot be acquired after the fact.
        revision = next(
            row
            for row in s4_store.export_records().table_rows("knowledge_revision", KnowledgeRevision)
            if row.id == outcome.revision_id
        )
        assert revision.authority_class == "human_confirmed"
        assert revision.verification == "verified"
        assert revision.body_md == pending.body_md
        # And the item serves again.
        served = resolve_freshness(s4_store.freshness_records(), pending.item_id, clock=s4_clock)
        assert served.state != "stale"

    def test_confirming_a_dead_cause_says_so_instead_of_pretending(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope, s4_clock: ManualClock
    ) -> None:
        """**The honesty case, and the one a well-meaning fix would break.**

        *Fails when* `confirm-current` reports success on a referent that no
        longer exists. *Matters because* the item stays STALE by the source rule
        whatever this action writes -- correctly, the endpoint is gone -- and a
        reviewer told "confirmed" who then watches `adopt ask` keep saying STALE
        concludes the freshness state is broken. *No other instrument catches it
        because* the confirmation itself is perfectly valid: the revision is
        appended, and only the sentence on screen is a lie."""
        pending, link, identity_id = _queued(s4_store, s4_scope)
        s4_store.identities().retire(identity_id=identity_id, reason="removed")
        dead_link = ChangedBinding(
            binding_id=link.binding_id,
            item_id=link.item_id,
            identity_id=link.identity_id,
            identity_uri=link.identity_uri,
            impact_class=_DEAD,
            is_load_bearing=True,
        )

        outcome = confirm_current_item(
            pending,
            reviews=s4_store.governance(),
            knowledge=s4_store.items(),
            freshener=s4_store.changes(),
            affected=[dead_link],
        )

        assert outcome.still_stale == (link.identity_uri,)
        # Nothing was freshened: the cause is source-ruled, so a binding write
        # would have been theatre.
        assert outcome.freshened_bindings == ()
        after = resolve_freshness(s4_store.freshness_records(), pending.item_id, clock=s4_clock)
        assert after.state == "stale"
        assert after.deciding_rule == RULE_SOURCE_IDENTITY_DEAD
        # The same sentence is available before the action, which is what lets
        # the CLI warn rather than apologise.
        assert still_stale_after_confirm([dead_link]) == (link.identity_uri,)


@pytest.mark.unit
class TestTheGuards:
    def test_an_item_is_resolved_once_whichever_action_asks(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* a second action lands on a resolved entry. *Matters
        because* the two writes compound rather than replace -- a retire after a
        rebind ends an item somebody just re-pointed -- and the queue would show
        one disposition for two decisions."""
        pending, _, _ = _queued(s4_store, s4_scope)
        retire_item(pending, reviews=s4_store.governance(), knowledge=s4_store.items())

        with pytest.raises(AdoptError) as raised:
            retire_item(pending, reviews=s4_store.governance(), knowledge=s4_store.items())

        assert raised.value.code is ErrorCode.REVIEW_ITEM_RESOLVED

    def test_a_change_action_on_another_population_is_refused_by_name(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* `--action retire` ends an ingested document because its
        review entry happened to be open. *Matters because* the three actions
        describe what happened to a **referent**, and an ingest suggestion is a
        question about a document -- one keystroke would mean something the
        reviewer was never asked. *No other instrument catches it because* the
        item id is real and the write would succeed."""
        pending, _, _ = _queued(s4_store, s4_scope, batch_key="ingest:doc-1")

        with pytest.raises(AdoptError) as raised:
            retire_item(pending, reviews=s4_store.governance(), knowledge=s4_store.items())

        assert raised.value.code is ErrorCode.REVIEW_ITEM_NOT_FOUND
        # Named, not reported absent: the id was right (CR-38's rule).
        assert "ingest" in raised.value.message
        assert ACTION_CONFIRM_CURRENT not in raised.value.message
