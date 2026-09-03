"""The capture ratchet: a human's answer becomes cited knowledge, in one step.

**Why one command and one transaction.** v6.1's claim is that capture must be
*cheaper than answering out of band* -- cheaper than a Slack reply. If banking an
answer takes four verbs, the FDE types the Slack reply and the store stays
ignorant, which is the loop this product exists to break. So `adopt answer` is
one call that writes the item, its verified revision, its provenance, its
bindings and the escalation stamp, and either all of that lands or none of it
does.

**The half-written store is the failure this shape prevents**, and it is worse
than it looks. An escalation stamped `answered` whose knowledge never committed
drops the question out of every open-question listing while answering nobody: it
is not a visible error, it is a question that silently stops existing. A
knowledge item with no binding and no escalation stamp is the mirror image --
the answer is in the store and the question that prompted it stays open forever,
so the next asker gets it and the queue still says nobody has.

**Provenance is `human` and authority is `human_confirmed`, always.** v6.1 spells
this `authored`; the manifest's enums spell it these two ways and the
machine-gated artifact wins (v6.1 §0, the same reconciliation the plan's D1 made
for `verified`). What the rule protects is the one distinction Build 2 made
non-negotiable: nothing a person or a model wrote may ever claim to have been
observed in an artifact. There is no parameter here that can make it say
otherwise.

**Binding follows Build 2's two tiers exactly, and does not invent a third**
(plan D9). Explicit `--uri` binds because the operator addressed the identity;
a canonical URI written into the answer text binds because the author addressed
it; a *name* appearing in prose does not bind, ever, because `config` and `user`
are identity keys and ordinary English both, and a false binding stops the gap
report asking for knowledge that is genuinely missing. **Zero bindings is
legal** -- the demo's own key-rotation answer binds nothing and still serves.
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Final, Protocol

from adopt_knowledge import IdentityView, structural_matches

from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope

__all__ = [
    "CONFIRMED",
    "DEFAULT_KIND",
    "HUMAN_AUTHORITY",
    "HUMAN_SOURCE",
    "CaptureResult",
    "CaptureStore",
    "capture_answer",
    "identities_to_bind",
]

#: The manifest values v6.1's prose calls "authored" and "confirmed". Named
#: constants rather than literals because the rule is that they never vary with
#: the caller -- there is no argument on `capture_answer` that changes any of the
#: three, and adding one would be adding a way for a captured answer to claim it
#: was observed in an artifact.
HUMAN_AUTHORITY: Final[str] = "human_confirmed"
HUMAN_SOURCE: Final[str] = "human"
CONFIRMED: Final[str] = "verified"

#: `knowledge_item.kind` for a captured answer. The manifest has a kind named
#: for exactly this and it is the right one: `answer` is a reply to a question
#: somebody asked, which is what capture produces and what distinguishes it from
#: a `procedure` (a runbook nobody asked for), a `rationale` (why a decision was
#: made) or a `recipe`. Callers may override for a capture that is genuinely one
#: of the others.
DEFAULT_KIND: Final[str] = "answer"


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """What one capture wrote. Every id is canonical and citable."""

    item_id: str
    revision_id: str
    provenance_id: str
    binding_ids: tuple[str, ...]
    escalation_id: str
    #: URIs the answer text named that no identity in scope matched. Reported
    #: rather than silently dropped: an operator who typed a URI and got no
    #: binding has either a typo or an unmapped identity, and both are things
    #: to be told about rather than to discover through a coverage report.
    unmatched_uris: tuple[str, ...] = ()


class CaptureStore(Protocol):
    """Everything capture needs from the store, declared here (CR-34/CR-37).

    One protocol rather than four because capture is one transaction: a caller
    that could supply the item writer and the escalation writer separately could
    supply two that do not share a connection, which is precisely the
    half-written store this module exists to make impossible. `transaction` is on
    the protocol for the same reason -- the unit of work is part of the
    contract, not an implementation detail of whoever calls it.
    """

    def transaction(self) -> AbstractContextManager[None]: ...

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
        """Create the item and its first revision. Returns `(item_id, revision_id)`.

        `authority_class` and `verification` are **passed in** rather than
        chosen by the realization, so the rule that a captured answer is always
        human-authored and always verified lives in this package -- one place,
        beside the paragraph explaining it -- instead of in whichever adapter
        happens to be wired up.
        """
        ...

    def record_provenance(self, *, revision_id: str, source_type: str, source_ref: str) -> str:
        """Record where the revision's claim came from. Returns the row id."""
        ...

    def bind(self, *, item_id: str, identity_id: str, is_load_bearing: bool) -> str:
        """Bind the item to an identity. Returns the binding id."""
        ...

    def answer_escalation(self, *, escalation_id: str, candidate_revision_id: str) -> None:
        """Stamp the escalation answered, pointing at the captured revision."""
        ...


def identities_to_bind(
    body_md: str,
    identities: Sequence[IdentityView],
    *,
    explicit_uris: Sequence[str] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(identity ids to bind, URIs that matched nothing)`.

    Explicit `--uri` values and canonical URIs written into the answer are both
    structural evidence and are treated identically -- an operator naming a URI
    on the command line has addressed the referent exactly as an author writing
    it into the text has. Name matches are **excluded here rather than filtered
    downstream**: `structural_matches` is the only matcher this module calls, so
    there is no code path along which a name match could reach a binding row.

    Order is deterministic (by identity id) so a capture writes its bindings in
    the same order on every machine.
    """
    by_uri = {identity.uri: identity for identity in identities}
    chosen: dict[str, None] = {}
    unmatched: list[str] = []

    for uri in explicit_uris:
        identity = by_uri.get(uri)
        if identity is None:
            unmatched.append(uri)
        else:
            chosen[identity.identity_id] = None

    matches, _ambiguous = structural_matches(body_md, identities)
    for match in matches:
        chosen[match.identity_id] = None

    return tuple(sorted(chosen)), tuple(unmatched)


def capture_answer(
    store: CaptureStore,
    *,
    escalation_id: str,
    scope: Scope,
    title: str,
    body_md: str,
    identity_ids: Sequence[str],
    kind: str = DEFAULT_KIND,
    actor_id: str | None = None,
    unmatched_uris: Sequence[str] = (),
) -> CaptureResult:
    """Write the answer, its provenance, its bindings and the stamp. One unit of work.

    Args:
        store: The composed write surface. The CLI supplies it over one store
            handle, so every write below shares one connection and one
            transaction.
        escalation_id: The question being answered. Stamped last, because the
            stamp is the claim that everything above it succeeded.
        scope: Where the knowledge belongs.
        title: The item's title -- the question, normally, so the captured row
            is findable by the words somebody asked.
        body_md: The human's answer, verbatim.
        identity_ids: What to bind, already resolved by `identities_to_bind`.
            Resolution is separated from writing so a caller can show an
            operator what will be bound before anything is written.
        kind: `knowledge_item.kind`.
        actor_id: Who answered.
        unmatched_uris: Carried through to the result for reporting.

    Returns:
        Every id written, so the caller can print citations the next `adopt ask`
        will produce.

    Raises:
        AdoptError: ``ESCALATION_NOT_FOUND`` / ``ESCALATION_ALREADY_ANSWERED``
            from the stamp, and ``KNOWLEDGE_SOURCE_UNREADABLE`` when the answer
            text is blank. **Nothing is committed on any of them.** The blank
            check is here rather than at the CLI because `adopt serve` reaches
            the same function, and an empty confirmed revision is worse than a
            refusal: the next asker gets KNOWN and an empty answer.
    """
    if not body_md.strip():
        raise AdoptError(
            ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE,
            message="the answer text is empty",
            hint="Pass --text with the answer. An empty confirmed revision would serve "
            "the next asker a KNOWN answer with nothing in it, which is worse than "
            "the UNKNOWN they get today.",
        )

    with store.transaction():
        item_id, revision_id = store.create_item(
            scope=scope,
            kind=kind,
            title=title,
            body_md=body_md,
            authority_class=HUMAN_AUTHORITY,
            verification=CONFIRMED,
            actor_id=actor_id,
        )
        provenance_id = store.record_provenance(
            revision_id=revision_id,
            source_type=HUMAN_SOURCE,
            # The question is the source. It links the revision back to the
            # escalation a human answered, which is the only durable record of
            # *why* this text exists -- `--text` on a shell line is not one.
            source_ref=f"escalation:{escalation_id}",
        )
        binding_ids = tuple(
            store.bind(
                item_id=item_id,
                identity_id=identity_id,
                # Load-bearing, and deliberately not a flag. A captured answer
                # describes the identity it is bound to, so a move or a death of
                # that identity is exactly the event that should stale it -- and
                # pre-B6 that binding-level rule is the *only* staleness signal
                # this product has (v6.1 F3). A non-load-bearing capture would be
                # knowledge nothing can ever mark stale.
                is_load_bearing=True,
            )
            for identity_id in identity_ids
        )
        store.answer_escalation(escalation_id=escalation_id, candidate_revision_id=revision_id)

    return CaptureResult(
        item_id=item_id,
        revision_id=revision_id,
        provenance_id=provenance_id,
        binding_ids=binding_ids,
        escalation_id=escalation_id,
        unmatched_uris=tuple(unmatched_uris),
    )
