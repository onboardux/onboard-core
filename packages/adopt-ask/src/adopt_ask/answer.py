"""Rendering an `Answer`, and the boundary that decides whether it may leave.

**The extractive answer is the passages themselves.** No summarization, no
paraphrase, no stitching -- the cited revision bodies verbatim, in retrieval
order, each with its revision id, the identity URIs it is bound to, and the
freshness rule that permitted it. That is the complete no-model mode R3
requires, and it is complete because the store already holds prose a human
wrote: an assistant that had to generate in order to answer would have nothing
to say without a model, which is exactly the dependency v6.1 refuses.

**The boundary check, and the sentence it implements.** v6.1 §6 Build 3: *the
assistant never answers outside the declared observability boundary*. Two ways
that can be false, and both are refusals here:

* **No boundary is declared.** Fail closed, on Build 0's egress posture -- an
  undeclared boundary is not an unlimited one. `adopt init` always declares one,
  so a store without one is a store assembled by something else, and that is
  precisely when guessing is worst.
* **The boundary does not permit what this answer would carry.** The envelope is
  validated by the Build 0 gate rather than by a second rule invented here, so
  the deny-list widens by itself when a `md`/`text` column is added to the
  manifest (`content_fields`).

**Why a local answer validates a metadata-only envelope.** `adopt ask` prints
inside the boundary: the content does not leave, so the envelope that represents
what *would* leave carries ids, URIs, states and counts -- never the question
text, the titles or the bodies. Those three are content fields by derivation,
and building them into a metadata-only payload would make the gate reject every
answer on every default store, which is how a control ends up switched off. A
caller with a genuinely outbound destination passes the policy that destination
uses, and then the boundary is asked the real question. B7's channels are the
first such caller.
"""

import datetime as _dt
from collections.abc import Mapping
from typing import Any, Final

from adopt_ask.branch import Answer
from adopt_const import SCHEMA_VERSION
from adopt_detect import METADATA_ONLY, BoundaryView
from adopt_obs import AdoptError, ErrorCode
from adopt_policy import validate_envelope
from adopt_schema.manifest import Manifest
from adopt_scope import Scope

__all__ = ["ASK_EVENT_TYPE", "envelope", "guard", "render", "sendable_payload"]

#: The envelope's `event_type` for an answer. One name, because a boundary
#: audit that had to recognise three spellings of "we answered a question" is a
#: boundary audit nobody completes.
ASK_EVENT_TYPE: Final[str] = "ask_answered"


def sendable_payload(answer: Answer, *, include_content: bool) -> dict[str, Any]:
    """The envelope payload for `answer`.

    Under `include_content=False` this is deliberately free of every content
    field: no `question`, no `title`, no `body_md`. Those are the names
    `find_content_fields` derives from the manifest and the logger deny-list,
    and omitting them is what makes a metadata-only answer honestly
    metadata-only rather than merely asserted to be.
    """
    citations: list[dict[str, Any]] = []
    for citation in answer.citations:
        entry: dict[str, Any] = {
            "revision_id": citation.revision_id,
            "item_id": citation.item_id,
            "identity_uris": list(citation.identity_uris),
            "origin": citation.origin,
            "freshness_state": citation.freshness_state,
            "deciding_rule": citation.deciding_rule,
        }
        if include_content:
            entry["title"] = citation.title
            entry["body_md"] = citation.body_md
        citations.append(entry)

    payload: dict[str, Any] = {
        "branch": answer.branch,
        "citation_count": len(answer.citations),
        "withheld_count": len(answer.withheld),
        "citations": citations,
    }
    if answer.cause is not None:
        payload["cause"] = answer.cause
    if include_content:
        payload["question"] = answer.question
    return payload


def envelope(
    answer: Answer,
    *,
    scope: Scope,
    occurred_at: _dt.datetime,
    content_policy: str = METADATA_ONLY,
) -> dict[str, Any]:
    """A contracts §8 envelope for `answer`, in `scope`.

    Raises:
        AdoptError: ``ASK_OUTSIDE_BOUNDARY`` when the scope is not resolved to an
            environment. Every scope id is required to check an envelope against
            any boundary at all, and a half-resolved scope is a question we
            cannot say whose system it concerns.
    """
    if scope.engagement is None or scope.system is None or scope.environment is None:
        raise _refuse(
            f"the scope {scope.path()!r} does not resolve to an environment, so no "
            "boundary governs it",
            "Open the store at a full firm/engagement/system/environment scope. An "
            "answer whose system is unknown cannot be checked against any boundary.",
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "firm_id": scope.firm.id,
        "engagement_id": scope.engagement.id,
        "system_id": scope.system.id,
        "environment_id": scope.environment.id,
        "event_type": ASK_EVENT_TYPE,
        "occurred_at": occurred_at.isoformat(),
        "content_policy": content_policy,
        "payload": sendable_payload(answer, include_content=content_policy != METADATA_ONLY),
    }


def guard(
    answer: Answer,
    boundary: BoundaryView | None,
    *,
    scope: Scope,
    occurred_at: _dt.datetime,
    content_policy: str = METADATA_ONLY,
    manifest: Manifest | None = None,
) -> None:
    """Refuse `answer` unless the declared boundary permits it. Raises, or returns None.

    Args:
        answer: The composed answer. Not modified, and never partially served --
            a refusal here withholds the whole answer, because an answer with
            its citations stripped to fit a boundary is an unattributed claim,
            which is worse than a refusal.
        boundary: The declared boundary, or `None` when the store holds none.
        scope: The full scope the store was opened at.
        occurred_at: Injected clock reading; never `datetime.now()` here.
        content_policy: What the destination carries. The default is the local
            terminal's: metadata only, because the content never leaves.
        manifest: Injected manifest, for tests asserting the derived deny-list.

    Raises:
        AdoptError: ``ASK_OUTSIDE_BOUNDARY`` for every refusal, with the
            underlying Build 0 envelope violation named in the message.
    """
    if boundary is None:
        raise _refuse(
            "no observability boundary is declared for this scope",
            "Run `adopt init` (or `adopt boundary`) to declare one. An undeclared "
            "boundary is not an unlimited boundary -- with nothing to check against, "
            "the assistant refuses rather than guesses what the client agreed to.",
        )

    try:
        validate_envelope(
            envelope(answer, scope=scope, occurred_at=occurred_at, content_policy=content_policy),
            boundary,
            manifest=manifest,
        )
    except AdoptError as violation:
        if violation.code is ErrorCode.ASK_OUTSIDE_BOUNDARY:
            raise
        raise _refuse(
            f"the declared boundary does not permit this answer: {violation.message}",
            "The boundary is the authority, not the question. Widening what may leave "
            "is a contract amendment recorded on the boundary row with "
            "`contractual_approval_ref`.",
        ) from violation


def render(answer: Answer) -> str:
    """The human rendering: the branch, then each passage verbatim with its grounds."""
    if answer.branch == "unknown":
        lines = [f"UNKNOWN: the store holds no confirmed answer to {answer.question!r}."]
        if answer.withheld:
            lines.append(
                f"{len(answer.withheld)} matching revision(s) were withheld as unverified. "
                "Confirm them in `adopt review` and ask again."
            )
        return "\n".join(lines)

    header = f"{answer.branch.upper()}: {answer.question}"
    if answer.branch == "stale":
        header += f"\nSTALE because {answer.cause} -- the answer below is what was true before."

    blocks = [header]
    for citation in answer.citations:
        grounds = f"  revision {citation.revision_id} ({citation.freshness_state}, "
        grounds += f"matched by {citation.origin}, rule {citation.deciding_rule})"
        if citation.identity_uris:
            grounds += "\n  bound to: " + ", ".join(citation.identity_uris)
        blocks.append(f"\n## {citation.title}\n{citation.body_md}\n{grounds}")

    if answer.withheld:
        blocks.append(f"\n{len(answer.withheld)} further revision(s) withheld as unverified.")
    return "\n".join(blocks)


def _refuse(message: str, hint: str) -> AdoptError:
    return AdoptError(ErrorCode.ASK_OUTSIDE_BOUNDARY, message=message, hint=hint)


def json_payload(answer: Answer) -> Mapping[str, Any]:
    """The `--json` payload: the full answer, content included.

    Distinct from `sendable_payload` on purpose. This one is for the operator's
    own terminal inside the boundary and carries everything; that one is for an
    envelope that may leave and carries what the boundary permits. Collapsing
    the two would mean either an unreadable local answer or an egress payload
    shaped by what is convenient to print.
    """
    return {
        "question": answer.question,
        "branch": answer.branch,
        "cause": answer.cause,
        "withheld": list(answer.withheld),
        "citations": [
            {
                "revision_id": citation.revision_id,
                "item_id": citation.item_id,
                "title": citation.title,
                "body_md": citation.body_md,
                "identity_uris": list(citation.identity_uris),
                "origin": citation.origin,
                "freshness_state": citation.freshness_state,
                "deciding_rule": citation.deciding_rule,
            }
            for citation in answer.citations
        ],
    }
