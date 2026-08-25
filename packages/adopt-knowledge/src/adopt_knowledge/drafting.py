"""Grounded drafting: the store writes the first draft, a human confirms it.

v6.1 §6 Build 4 (H3, restoring v4's ADR-0.41): *the system drafts; the human
confirms or corrects; the human never authors from blank.* This module is the
drafting half. It is the **only** place in either repository where a model's
words are persisted, and every rule below exists because of that one fact.

**Critical semantic invariant #7 lives in `ground`**, as it does in
`adopt_ask.synthesis` -- but the stakes are not the same and the difference is
worth stating. A discarded synthesis costs a reader a prettier paragraph, because
the extractive answer was already complete. A draft that should have been
discarded and was not becomes a `knowledge_revision`: it is bound to an identity,
it appears in a review queue where one keystroke confirms it, and it renders into
a document handed to a client. There is no already-complete answer sitting behind
it. So the same four discards are enforced here with the same absoluteness, and
the empty case is not a failure: the pack renders an honest "no knowledge yet"
gap for that identity, which is what it did before drafting existed.

**Four ways a draft is discarded**, and they are one rule seen from four angles --
*the model may not introduce anything*:

* it cited nothing;
* it cited a fact key that was not in the set supplied (a fabricated or
  half-remembered key -- the dangerous case, because a plausible `krev_...` or a
  plausible URI reads as grounded to every human who sees it);
* its output could not be parsed into the declared shape;
* its prose is empty.

**A discarded draft persists nothing.** Not an item, not a revision, not a
binding, not a provenance row, not a review item. That is what the transaction is
for, and it is why the write goes through one `DraftStore` rather than three
writers a caller could hand in from three connections.

## `human_confirmed` on something no human has confirmed

A draft lands `authority_class = human_confirmed` with `human` provenance, and
that reads wrong until you see which question the column answers.
`authority_class` records **where the text came from**: observed in an artifact,
observed in behaviour, or authored. The manifest spells "authored" as
`human_confirmed`, and it is the value `adopt_ask.capture` uses for a human's own
prose. What a draft must never claim is `artifact_observed` -- that is a
statement that these words were read out of the client's repository, and nothing
generated may ever acquire it.

Whether anybody has *agreed* with the text is a different column, and it is the
one that carries the honesty: `verification = unverified`, always, with no
argument that changes it. `recompute_coverage` counts no unverified item, so a
run of twenty drafts moves the coverage number by exactly zero;
`adopt_handover.sections.select` admits no unverified revision, so no draft is
ever selected as confirmed content; and `stamp_for` returns `unverified` for one
whatever its freshness says, so no draft renders without its banner. Confirming
it in `adopt review` is the only thing that changes any of that, and it appends
a new revision rather than editing this one.

**The actor is the drafting run and cannot be overridden.** There is no
`actor_id` parameter here on purpose -- an `--actor alice` reaching this path
would put a person's name on text a model wrote, which is the same class of lie
as `artifact_observed` and would be invisible in every later reading.

**Offline is refused by the seam, not here.** `Runner` raises
`ADOPT_OFFLINE_DENIED` when no network is permitted and reports whether an
adapter is configured at all. `run_drafting` takes a runner it was handed; a
caller with no adapter never calls it and the pack renders anyway, which is R3's
no-model mode -- not a flag, an absent argument.
"""

import hashlib
import json
from collections.abc import Container, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from adopt_agent import AgentRequest, AgentRunner, Budget
from adopt_const import AGENT_DRAFT_MAX_USD, AGENT_DRAFT_MAX_WALL_SECONDS, DRAFT_MAX_PER_RUN
from adopt_identity import parse_uri
from adopt_knowledge.ports import DraftStore
from adopt_model._enums import AuthorityClass, ItemKind, SourceType, Verification
from adopt_obs import AdoptError, get_logger
from adopt_scope import Scope

__all__ = [
    "DRAFT_ACTOR",
    "DRAFT_AUTHORITY",
    "DRAFT_EXTRACTOR_VERSION",
    "DRAFT_KIND",
    "DRAFT_PROMPT_REF",
    "DRAFT_PROVENANCE_PREFIX",
    "DRAFT_SOURCE",
    "DRAFT_VERIFICATION",
    "Draft",
    "DraftOutcome",
    "DraftReport",
    "DraftTarget",
    "Fact",
    "batch_key",
    "build_inputs",
    "draft_one",
    "ground",
    "idempotency_key_for",
    "render_body",
    "run_drafting",
    "title_for",
]

_log = get_logger("adopt_knowledge")

#: The immutable prompt version, named explicitly. `00` §5 rule 5: a prompt is
#: never edited in place, so a change is a new ref with its own golden-set result
#: table -- and `04` §5.2 rule 2 forbids a "latest".
#:
#: **`name/vN`, not `name@vN`.** The loader resolves a ref as a *path* under the
#: prompts root, and Build 3 records what the other spelling costs: every call
#: discarded silently on a load failure, which is indistinguishable from a model
#: declining to answer, and made every invariant-#7 test pass over a function
#: returning `None` unconditionally. The positive control is what finds it.
DRAFT_PROMPT_REF: Final[str] = "draft-001/v1"

#: `knowledge_item.kind` for a drafted section. A draft answers *how this is run*
#: for people taking the system over, which is what `procedure` names -- and it
#: is the kind `adopt_handover`'s runbook section selects, so a confirmed draft
#: appears where the gap was.
DRAFT_KIND: Final[ItemKind] = "procedure"

#: Where the text came from: authored, never observed. See the module docstring.
DRAFT_AUTHORITY: Final[AuthorityClass] = "human_confirmed"
DRAFT_SOURCE: Final[SourceType] = "human"

#: Whether anybody has agreed with it. **Always this, with no parameter that
#: changes it** -- the honesty invariant of the whole build in one constant.
DRAFT_VERIFICATION: Final[Verification] = "unverified"

#: Who a drafting run records itself as. Not a person, and not overridable: a
#: draft carrying an operator's name is a lie every later reader inherits.
DRAFT_ACTOR: Final[str] = "adopt-draft"

#: `binding_revision.extractor` for the link a draft creates. The prompt ref
#: rather than a tier name, because that is honestly what justified the binding:
#: a drafting run addressed this identity.
DRAFT_EXTRACTOR_VERSION: Final[str] = "1"

#: Prefixes every `provenance.source_ref` a drafting run writes, so a draft is
#: recognisable by its provenance rather than by a column nothing else uses.
#: This is what makes the run idempotent (an identity already drafted is skipped)
#: and what lets the pack tell a draft from a harvest candidate -- both are
#: `unverified`, and only one belongs in a handover document.
DRAFT_PROVENANCE_PREFIX: Final[str] = "draft:"

#: How many characters of the fact-set digest the batch key carries. Long enough
#: that two runs in one store do not collide, short enough that a human can read
#: the key back off a queue listing.
_BATCH_DIGEST_CHARS: Final[int] = 12


@dataclass(frozen=True, slots=True)
class Fact:
    """One thing the store already knows, and the key the model must cite it by.

    The key is what grounding is checked against, so it has to be something a
    human can resolve afterwards: a canonical URI, a `krev_` revision id, a
    provenance ref. A key invented for the prompt's convenience would make a
    citation unresolvable and the grounding check a formality.
    """

    key: str
    text: str


@dataclass(frozen=True, slots=True)
class DraftTarget:
    """One identity to draft a section about, with everything known about it.

    Facts are supplied by the caller -- the composition root that may read the
    store -- rather than gathered here, for the reason every other module in this
    package is handed its rows: `adopt_knowledge` holds no dialect. It also means
    the fact set sent to the model and the fact set grounding is checked against
    are provably the same tuple.
    """

    identity_id: str
    uri: str
    kind: str
    facts: tuple[Fact, ...] = ()


@dataclass(frozen=True, slots=True)
class Draft:
    """A grounded draft. Constructing one is the claim that it is grounded.

    There is no "ungrounded draft" value, and that absence is the design: `ground`
    returns `None` for everything that failed, so nothing downstream can hold a
    draft it must remember not to write.
    """

    body_md: str
    cited_facts: tuple[str, ...]
    unknowns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DraftOutcome:
    """What happened to one target. Either it landed, or `reason` says why not."""

    uri: str
    identity_id: str
    item_id: str | None = None
    revision_id: str | None = None
    binding_ids: tuple[str, ...] = ()
    provenance_ids: tuple[str, ...] = ()
    #: Why nothing was written: `discarded`, `skipped` or `capped`. `None` means
    #: the draft landed.
    reason: str | None = None

    @property
    def landed(self) -> bool:
        return self.revision_id is not None


@dataclass(slots=True)
class DraftReport:
    """One drafting run, whatever it managed to do."""

    outcomes: list[DraftOutcome] = field(default_factory=list)
    review_batch_id: str | None = None
    review_item_ids: tuple[str, ...] = ()

    @property
    def landed(self) -> tuple[DraftOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.landed)

    @property
    def discarded(self) -> tuple[DraftOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if not outcome.landed)


def title_for(uri: str) -> str:
    """The item title a drafted section carries.

    Derived from the URI rather than asked of the model: the title is what the
    pack renders as a heading and what `select` sorts by, so a model-chosen title
    would make the pack's byte order depend on the model's mood. An unparseable
    URI keeps the URI as its title -- ugly, and better than a heading that names
    nothing.

    **The key is a sequence, and it is joined on `/` here.** `POST /v1/orders` is
    one segment whose slash is data; `billing/charges/refund` is three whose
    slashes are structure -- and a heading has to render both, so the separator
    that reconstructs the second is the one to use. Rendering the tuple itself
    would put a Python repr in a client's document.
    """
    try:
        parsed = parse_uri(uri)
    except Exception:
        return uri
    return f"{parsed.kind} {'/'.join(parsed.key)}"


def build_inputs(target: DraftTarget, audience: str) -> dict[str, Any]:
    """The prompt inputs for one target. **Store facts only.**

    v6.1 is explicit that the model never free-associates about the client's
    system, and this function is where that holds: everything rendered here came
    out of the store, which means it was observed by the deterministic mapper or
    written by a person. No file is opened at draft time -- the store already
    holds what was observed, and re-reading the repository would put source code
    on a wire the boundary never agreed to.
    """
    facts = "\n".join(f"[{fact.key}] {fact.text}" for fact in target.facts)
    return {
        "uri": target.uri,
        "kind": target.kind,
        "audience": audience,
        "fact_count": len(target.facts),
        "facts": facts,
    }


def idempotency_key_for(target: DraftTarget) -> str:
    """The seam's replay key: (identity URI, prompt ref, supplied fact set).

    Re-running `--draft-missing` over an unchanged store replays the recorded run
    rather than paying for it twice (PRD F13.5). The digest covers the facts that
    were **supplied**, not the ones cited back: it is computed before the call,
    and a store that has learned something new about this identity is asking a
    genuinely different question that should not replay the old answer.
    """
    material = "\0".join([DRAFT_PROMPT_REF, target.uri, *(fact.key for fact in target.facts)])
    return f"draft-001:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def batch_key(uris: Sequence[str], audience: str) -> str:
    """The review queue's coalescing key for one drafting run.

    Named after what was drafted rather than after the clock, so the key is a
    pure function of the run -- the same discipline the pack's bytes follow. The
    `draft:` prefix is what `review.source_of` dispatches on.
    """
    digest = hashlib.sha256("\0".join(sorted(uris)).encode("utf-8")).hexdigest()
    return f"draft:{audience}:{digest[:_BATCH_DIGEST_CHARS]}"


def render_body(draft: Draft) -> str:
    """The Markdown actually stored: the prose, then the model's own unknowns.

    **The unknowns are never silently dropped.** They are the half of the output
    that turns a draft into an elicitation prompt: a question a reviewer can
    answer is worth more than a sentence they have to disprove, and a model that
    admitted it did not know something must not have that admission edited out on
    the way to the person who does. Rendered into the body rather than into a
    column because they belong to this revision's text -- a confirm that rewrites
    the body answers them, and the answer supersedes the question in one chain.
    """
    body = draft.body_md.strip()
    if not draft.unknowns:
        return body
    lines = [body, "", "**Open questions**", ""]
    lines += [f"- {question.strip()}" for question in draft.unknowns]
    return "\n".join(lines)


def ground(output: object, permitted: Container[str]) -> Draft | None:
    """Parse and validate one model output. `None` means **discard**.

    Args:
        output: `AgentResult.output` -- a `dict` when the adapter honoured the
            output schema, a `str` when it replied with JSON text, anything at
            all when it did not.
        permitted: The fact keys that were actually supplied. A citation outside
            this set is a fabrication, whatever it looks like.

    Returns:
        The draft, or `None` for every failure. Never raises: a model replying
        with nonsense is an expected condition here, not an error -- the pack
        renders the identity as an honest gap either way, which is exactly what
        it did before this module existed.
    """
    parsed = _as_mapping(output)
    if parsed is None:
        _discard("unparseable")
        return None

    prose = parsed.get("body_md")
    raw_facts = parsed.get("cited_facts")
    if not isinstance(prose, str) or not prose.strip():
        _discard("empty_prose")
        return None
    if not isinstance(raw_facts, list):
        _discard("citations_not_a_list")
        return None

    cited = tuple(str(value) for value in raw_facts)
    if not cited:
        # Invariant #7, stated plainly. The prompt tells the model an empty list
        # is a correct response when the facts do not support a section, so this
        # is often the model behaving *well* -- and it still discards, because a
        # section nobody can trace to a store fact is not something to hand a
        # client under any banner.
        _discard("no_citations")
        return None

    foreign = [value for value in cited if value not in permitted]
    if foreign:
        # The dangerous case. A fabricated key reads as grounded to every human
        # who sees it, and unlike a synthesis this one would be *written down* --
        # bound to an identity, queued for a confirm, and rendered into a
        # document handed to somebody who was not in the room.
        _discard("foreign_citations", count=len(foreign))
        return None

    unknowns = parsed.get("unknowns")
    return Draft(
        body_md=prose.strip(),
        cited_facts=cited,
        # A missing or malformed `unknowns` is emptiness, not a discard: it is
        # the one field that carries no claim about the client's system, so
        # throwing away a grounded draft over it would trade real content for a
        # formality. Every field that *does* carry a claim is checked above.
        #
        # `output_schema.json` agrees, and it did not at first: the schema listed
        # `unknowns` as required, so a model that simply omitted it burned the
        # seam's single retry and the draft was discarded by the *seam* before
        # this tolerance was ever consulted. The two halves have to say the same
        # thing about which fields carry a claim, or the stricter one silently
        # decides.
        unknowns=tuple(str(value) for value in unknowns) if isinstance(unknowns, list) else (),
    )


def draft_one(runner: AgentRunner, target: DraftTarget, *, audience: str) -> Draft | None:
    """One bounded model call for one identity. `None` means draft nothing.

    Returns:
        A grounded draft, or `None` -- for a target with no facts, a non-`ok`
        run, a budget exhaustion, a provider error, or any of `ground`'s
        discards. Every one of those leaves the identity in the gap appendix,
        which is where it already was.
    """
    if not target.facts:
        # Nothing to ground on. Calling would be asking a model to invent the
        # section the store just said it cannot support, and `ground` would
        # discard whatever came back -- so the call is pure cost and pure risk.
        _discard("no_facts")
        return None

    permitted = {fact.key for fact in target.facts}
    try:
        outcome = runner.run(
            AgentRequest(
                skill_ref=DRAFT_PROMPT_REF,
                inputs=build_inputs(target, audience),
                budget=Budget(
                    max_usd=AGENT_DRAFT_MAX_USD,
                    max_wall_seconds=AGENT_DRAFT_MAX_WALL_SECONDS,
                ),
                idempotency_key=idempotency_key_for(target),
            )
        )
    except AdoptError as refused:
        # Offline refusal, unavailable adapter, provider failure. All of them
        # mean the same thing here -- no draft for this identity -- and none of
        # them should stop the pack the caller is assembling.
        _log.info("draft_unavailable", code=str(refused.code))
        return None

    if outcome.status != "ok":
        _discard(str(outcome.status))
        return None

    return ground(outcome.output, permitted)


def run_drafting(
    targets: Sequence[DraftTarget],
    *,
    runner: AgentRunner,
    store: DraftStore,
    scope: Scope,
    audience: str,
    already_drafted: Container[str] = frozenset(),
    limit: int = DRAFT_MAX_PER_RUN,
) -> DraftReport:
    """Draft up to `limit` targets, write what survived, queue it for review.

    Args:
        targets: The uncovered identities to draft, **already ranked** by the
            caller. Order decides who gets the budget when there are more gaps
            than the cap allows, and the gap ranking is deterministic -- so two
            runs over one store draft the same twenty.
        already_drafted: Identity ids that already carry an unverified draft.
            Skipped rather than re-drafted: a second `--draft-missing` should
            advance through the queue, not re-bill for a section whose only
            problem is that nobody has confirmed it yet.
        limit: How many model calls this invocation may make. Targets past it are
            reported `capped` so an operator can see the queue is longer than the
            run, rather than believing the store is finished.

    Returns:
        A `DraftReport` naming every target and what happened to it -- including
        the discards, because a run that drafted nothing and a run that was never
        configured must not look the same to whoever reads the output.
    """
    report = DraftReport()
    calls = 0

    for target in targets:
        if target.identity_id in already_drafted:
            report.outcomes.append(_nothing(target, "skipped"))
            continue
        if calls >= limit:
            report.outcomes.append(_nothing(target, "capped"))
            continue

        calls += 1
        draft = draft_one(runner, target, audience=audience)
        if draft is None:
            report.outcomes.append(_nothing(target, "discarded"))
            continue
        report.outcomes.append(_write(draft, target, store=store, scope=scope, audience=audience))

    pending = [
        (outcome.item_id or "", outcome.revision_id)
        for outcome in report.outcomes
        if outcome.landed
    ]
    if pending:
        batch_id, item_ids = store.open_batch(
            system_id=str(scope.system.id) if scope.system is not None else "",
            batch_key=batch_key([outcome.uri for outcome in report.landed], audience),
            items=pending,
            owner_actor_id=DRAFT_ACTOR,
        )
        report.review_batch_id = batch_id
        report.review_item_ids = item_ids

    _log.info(
        "drafting.completed",
        targets=len(targets),
        calls=calls,
        landed=len(report.landed),
        discarded=len(report.discarded),
    )
    return report


def _write(
    draft: Draft,
    target: DraftTarget,
    *,
    store: DraftStore,
    scope: Scope,
    audience: str,
) -> DraftOutcome:
    """Item, revision, provenance, binding and audience tag. One unit of work.

    **The binding is load-bearing and is written here rather than left to
    review.** A draft is a section *about* this identity, so a move or a death of
    the identity is exactly the event that should stale it -- and pre-Build 6 the
    binding-level rule is the only staleness signal this product has. It also
    means confirming the draft adds no link, which is what lets drafts take the
    candidate confirm mechanics unchanged rather than needing a third meaning of
    "confirm".
    """
    with store.transaction():
        item_id, revision_id = store.record(
            scope=scope,
            kind=DRAFT_KIND,
            title=title_for(target.uri),
            body_md=render_body(draft),
            authority_class=DRAFT_AUTHORITY,
            verification=DRAFT_VERIFICATION,
            actor_id=DRAFT_ACTOR,
        )
        provenance_ids = [
            store.record_provenance(
                revision_id=revision_id,
                source_type=DRAFT_SOURCE,
                # The prompt version is the first thing recorded, because it is
                # what makes the text reproducible: `skill_sha256` binds the run
                # to exact prompt bytes, and this row is how a reader gets from a
                # revision back to which prompt produced it.
                source_ref=f"{DRAFT_PROVENANCE_PREFIX}prompt:{DRAFT_PROMPT_REF}",
            )
        ]
        provenance_ids += [
            store.record_provenance(
                revision_id=revision_id,
                source_type=DRAFT_SOURCE,
                source_ref=f"{DRAFT_PROVENANCE_PREFIX}fact:{key}",
            )
            for key in draft.cited_facts
        ]
        binding_id, _binding_revision = store.bind(
            item_id=item_id,
            identity_id=target.identity_id,
            is_load_bearing=True,
            extractor=DRAFT_PROMPT_REF,
            extractor_version=DRAFT_EXTRACTOR_VERSION,
            actor_id=DRAFT_ACTOR,
        )
        # **A draft must be tagged or it serves nothing.** `recompute_coverage`
        # refuses to count an item with no `audience_tag` row, so an untagged
        # draft could be written, bound and confirmed and still leave the gap
        # open -- and `select` filters on the audience, so an untagged draft
        # would never render into the pack that asked for it either.
        store.tag_audience(item_id=item_id, audience=audience)

    return DraftOutcome(
        uri=target.uri,
        identity_id=target.identity_id,
        item_id=item_id,
        revision_id=revision_id,
        binding_ids=(binding_id,),
        provenance_ids=tuple(provenance_ids),
    )


def _nothing(target: DraftTarget, reason: str) -> DraftOutcome:
    return DraftOutcome(uri=target.uri, identity_id=target.identity_id, reason=reason)


def _as_mapping(output: object) -> dict[str, Any] | None:
    """`output` as a mapping, tolerating a JSON string and a fenced one.

    The fence is not defensive programming: CR-52 records a frontier model
    fencing its JSON on this repository's own conformance run and burning the
    seam's single retry on it. Stripping one is cheaper than a discarded draft
    and cannot make an ungrounded output look grounded -- every citation is still
    checked against `permitted` afterwards.
    """
    if isinstance(output, dict):
        return output
    if not isinstance(output, str):
        return None
    text = output.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _discard(reason: str, **fields: int) -> None:
    """Record one discard. The caller returns `None`; this only writes the event.

    Every discard emits a structured event, because the alternative -- silently
    writing nothing -- makes a model that has stopped grounding indistinguishable
    from one that was never configured. The event carries a reason and a count
    and **never the model's output**: `adopt_obs`'s deny-list drops `output`
    fields, and the whole point of a discard is that the text was not fit to
    write down.
    """
    _log.info("draft_discarded", reason=reason, **fields)
    return
