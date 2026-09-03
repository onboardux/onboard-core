"""The capture ratchet: what one command writes, and what a failure leaves behind.

Two risks, and they are different in kind.

The first is **atomicity**, and it is tested by planting a failure at the last
write. The half-written store is not a loud failure: an escalation stamped
`answered` whose knowledge never committed drops out of every open-question
listing while answering nobody, and a knowledge item whose stamp never landed
leaves the question open forever beside the answer to it. Neither raises later.
So the test drives a real store through the real facades and asserts on the rows
that are *not* there.

The second is **the binding rule**, which is Build 2's H2 restated: a URI binds,
a name never does. `config`, `user` and `model` are identity keys and ordinary
English both, and a false binding stops the gap report asking for knowledge that
is genuinely missing -- the worst silent failure available here.
"""

from collections.abc import Sequence

import pytest
from adopt_ask.capture import CONFIRMED, HUMAN_AUTHORITY, capture_answer, identities_to_bind
from adopt_knowledge import IdentityView

from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

#: A URI no identity in the fixture store carries. Written out rather than
#: derived, because the point of the unmatched case is a string the registry
#: does not know.
ABSENT_URI = "onboard-v1://nope/nope/nope/nope/endpoint/-/x"


class _Adapter:
    """`CaptureStore` over one handle -- the shape `adopt answer` wires up.

    A copy of the command's adapter rather than an import of it: the command
    module builds its own with a `--json` path and option parsing around it, and
    a test that reached into that would be asserting the CLI's plumbing while
    claiming to assert capture's.
    """

    def __init__(self, handle: SqliteStoreHandle, *, fail_on_stamp: bool = False) -> None:
        self._handle = handle
        self._fail_on_stamp = fail_on_stamp

    def transaction(self) -> object:
        return self._handle.backend.transaction()

    def create_item(
        self,
        *,
        scope: Scope,
        kind: str,
        title: str,
        body_md: str,
        authority_class: str,
        verification: str,
        actor_id: str | None,
    ) -> tuple[str, str]:
        return self._handle.items().record(
            scope=scope,
            kind=kind,  # type: ignore[arg-type]
            title=title,
            body_md=body_md,
            authority_class=authority_class,  # type: ignore[arg-type]
            verification=verification,  # type: ignore[arg-type]
            actor_id=actor_id,
        )

    def record_provenance(self, *, revision_id: str, source_type: str, source_ref: str) -> str:
        return self._handle.items().record_provenance(
            revision_id=revision_id,
            source_type=source_type,  # type: ignore[arg-type]
            source_ref=source_ref,
        )

    def bind(self, *, item_id: str, identity_id: str, is_load_bearing: bool) -> str:
        binding_id, _ = self._handle.bindings().create(
            item_id=item_id, identity_id=identity_id, is_load_bearing=is_load_bearing
        )
        return binding_id

    def answer_escalation(self, *, escalation_id: str, candidate_revision_id: str) -> None:
        if self._fail_on_stamp:
            raise RuntimeError("planted failure at the last write of the transaction")
        self._handle.governance().answer_escalation(
            escalation_id=escalation_id, candidate_revision_id=candidate_revision_id
        )


def _identity(
    store: SqliteStoreHandle, scope: Scope, key: str, kind: str = "endpoint"
) -> tuple[str, str]:
    """`(identity_id, uri)` -- the URI read back from the store rather than
    hand-written, because the builder percent-encodes and a literal drifts."""
    row = store.identities().observe(
        scope=scope,
        kind=kind,  # type: ignore[arg-type]
        namespace=None,
        key=key,
        extractor="test",
        extractor_version="1",
    )
    return row.id, row.uri


def _views(identity_id: str, uri: str) -> Sequence[IdentityView]:
    return [IdentityView(identity_id=identity_id, uri=uri)]


def _open_escalation(store: SqliteStoreHandle, scope: Scope) -> str:
    assert scope.system is not None
    return store.governance().open_escalation(
        system_id=str(scope.system.id),
        branch="ungrounded",
        question="how do I rotate the API key?",
    )


@pytest.mark.unit
class TestOneUnitOfWork:
    def test_a_failure_at_the_last_write_leaves_no_knowledge_and_no_stamp(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* the four writes stop sharing one transaction.
        *Matters because* the two partial states are both silent. An escalation
        stamped `answered` with no knowledge behind it leaves every listing
        saying the question was handled while nobody was answered; knowledge
        with no stamp leaves the question open beside its own answer, so the
        next asker is told nobody knows and the queue agrees. Neither raises,
        ever. *No other instrument catches it because* each individual write is
        valid, and a store in either state passes `store doctor`, the schema
        checks and every gate this repository has.
        """
        escalation_id = _open_escalation(s4_store, s4_scope)
        before = _counts(s4_store)

        with pytest.raises(RuntimeError, match="planted failure"):
            capture_answer(
                _Adapter(s4_store, fail_on_stamp=True),
                escalation_id=escalation_id,
                scope=s4_scope,
                title="How do I rotate the API key?",
                body_md="Rotate it through the vault; the app rereads on SIGHUP.",
                identity_ids=(),
            )

        assert _counts(s4_store) == before, (
            "a planted failure at the stamp must roll back the item, the revision "
            "and the provenance written before it"
        )
        row = s4_store.governance().get_escalation(escalation_id)
        assert row is not None
        assert row.status == "open"
        assert row.candidate_revision_id is None

    def test_a_successful_capture_writes_all_four_and_the_next_ask_can_cite_it(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """The positive control the rollback test needs to mean anything: a run
        that plants nothing writes every row, and the escalation points at the
        revision an `adopt ask` would then serve."""
        escalation_id = _open_escalation(s4_store, s4_scope)

        result = capture_answer(
            _Adapter(s4_store),
            escalation_id=escalation_id,
            scope=s4_scope,
            title="How do I rotate the API key?",
            body_md="Rotate it through the vault.",
            identity_ids=(),
            actor_id="alice",
        )

        assert result.binding_ids == (), "zero bindings is legal (plan D9)"
        stamped = s4_store.governance().get_escalation(escalation_id)
        assert stamped is not None
        assert stamped.status == "answered"
        assert stamped.candidate_revision_id == result.revision_id

        revision = _revision_row(s4_store, result.revision_id)
        assert revision["verification"] == CONFIRMED
        assert revision["authority_class"] == HUMAN_AUTHORITY

    def test_the_captured_revision_is_human_authored_and_never_artifact_observed(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* a captured answer records `artifact_observed` provenance.
        *Matters because* that is the one distinction Build 2 made
        non-negotiable: a claim that says it was seen in the client's code, when
        in fact a person typed it, is a claim nobody can later audit. *No other
        instrument catches it because* both values are schema-valid and the
        answer reads identically either way."""
        escalation_id = _open_escalation(s4_store, s4_scope)

        result = capture_answer(
            _Adapter(s4_store),
            escalation_id=escalation_id,
            scope=s4_scope,
            title="Q",
            body_md="A human wrote this.",
            identity_ids=(),
        )

        provenance = s4_store.backend.query(
            "SELECT source_type, source_ref FROM provenance WHERE revision_id = ?",
            (result.revision_id,),
        )
        assert [str(row["source_type"]) for row in provenance] == ["human"]
        assert str(provenance[0]["source_ref"]) == f"escalation:{escalation_id}"

    def test_an_empty_answer_is_refused_before_anything_is_written(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* a blank `--text` is accepted. *Matters because* the
        result is a *confirmed* revision with nothing in it, so the next asker
        gets KNOWN and an empty answer -- strictly worse than the UNKNOWN they
        get today, and now with the question closed. *No other instrument
        catches it because* an empty string is a valid `body_md`."""
        escalation_id = _open_escalation(s4_store, s4_scope)
        before = _counts(s4_store)

        with pytest.raises(AdoptError) as raised:
            capture_answer(
                _Adapter(s4_store),
                escalation_id=escalation_id,
                scope=s4_scope,
                title="Q",
                body_md="   \n  ",
                identity_ids=(),
            )

        assert raised.value.code is ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE
        assert _counts(s4_store) == before


@pytest.mark.unit
class TestWhatBindsAndWhatDoesNot:
    def test_a_canonical_uri_in_the_answer_binds(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """Structural evidence: the author addressed the referent, so there is
        nothing to infer (Build 2 H2, plan D9)."""
        identity_id, uri = _identity(s4_store, s4_scope, "POST /v1/refunds")
        views = _views(identity_id, uri)

        chosen, unmatched = identities_to_bind(f"See {uri} for the flow.", views)

        assert chosen == (identity_id,)
        assert unmatched == ()

    def test_an_explicit_uri_binds_even_when_the_text_never_names_it(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """An operator passing `--uri` has addressed the identity exactly as an
        author writing it into the text has."""
        identity_id, uri = _identity(s4_store, s4_scope, "POST /v1/refunds")
        views = _views(identity_id, uri)

        chosen, unmatched = identities_to_bind(
            "Rotate through the vault.", views, explicit_uris=[uri]
        )

        assert chosen == (identity_id,)
        assert unmatched == ()

    def test_a_name_appearing_in_prose_never_binds(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* the name tier is allowed to auto-bind.
        *Matters because* a false binding makes `recompute_coverage` count the
        identity covered, so the gap report stops asking for the knowledge that
        is genuinely missing, and staleness fans out to documents that never
        described the thing that changed -- H2's named worst case. *No other
        instrument catches it because* the binding row is perfectly well-formed
        and reads as a success."""
        identity_id, uri = _identity(s4_store, s4_scope, "vault", kind="config_key")
        views = _views(identity_id, uri)

        chosen, unmatched = identities_to_bind("Rotate the key in the vault and restart.", views)

        assert chosen == (), "a bare name is a suggestion for review, never a binding"
        assert unmatched == ()

    def test_an_unmatched_explicit_uri_is_reported_rather_than_dropped(
        self, s4_store: SqliteStoreHandle, s4_scope: Scope
    ) -> None:
        """*Fails when* a `--uri` naming nothing is silently ignored.
        *Matters because* the operator either typed it wrong or the identity is
        unmapped, and both are things to be told about now rather than to
        discover later through a coverage report that quietly never improved.
        *No other instrument catches it because* the capture succeeds: the
        answer lands, just bound to nothing."""
        identity_id, uri = _identity(s4_store, s4_scope, "POST /v1/refunds")
        views = _views(identity_id, uri)

        chosen, unmatched = identities_to_bind("text", views, explicit_uris=[ABSENT_URI])

        assert chosen == ()
        assert unmatched == (ABSENT_URI,)


def _counts(store: SqliteStoreHandle) -> dict[str, int]:
    return {
        table: int(store.backend.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])  # noqa: S608
        for table in ("knowledge_item", "knowledge_revision", "provenance", "binding")
    }


def _revision_row(store: SqliteStoreHandle, revision_id: str) -> dict[str, object]:
    rows = store.backend.query(
        "SELECT verification, authority_class FROM knowledge_revision WHERE id = ?",
        (revision_id,),
    )
    return dict(rows[0])
