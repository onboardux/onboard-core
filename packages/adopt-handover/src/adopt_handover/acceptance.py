"""The acceptance record: what was handed over, to whom, and what is still open.

v6.1 §6 Build 9 step 5 says both parties hold the snapshot; this is the half a
human reads. It travels beside the bundle as `acceptance.json`, and the
delivering store can re-render it from its own audit rows at any time -- so it is
a *rendering* of the record rather than a second copy of it, and the two cannot
drift.

**It is a build artifact, not a published contract (R4)**, exactly as Build 4's
lineage sidecar is: no `$id`, no JSON-schema target, no compatibility promise.
`record_version` is here because a document a client keeps for years should say
what shape it is, not because a second party is shipping against it.

**The `open_items` block is the honesty rule in rendered form.** v6.1 requires
that *"unresolved items transfer with named owners rather than being closed to
look complete"* -- so the record lists what is still open and who now owns it,
and there is deliberately no field anywhere in this document for marking an item
resolved. A handover that wanted to look finished would have to lie in a place
somebody can check against the store.

**Every open question's owner is the effective one.** An escalation routed to a
named person keeps them; every other open question belongs to whoever owns the
system now, which after `close` is the receiving owner. That is the same rule
the plane's escalation router resolves by, and it is why this build needs no
write to `escalation.owner_actor_id` (sprint plan D9-6).
"""

import datetime as _dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from adopt_handover.event import (
    HANDOVER_CLOSED,
    STEP_ORDER,
    HandoverRecord,
)
from adopt_handover.views import PackConflict, PackGap
from adopt_obs import format_timestamp

__all__ = [
    "ACCEPTANCE_FILENAME",
    "RECORD_VERSION",
    "OpenQuestion",
    "effective_owner",
    "record_payload",
    "render_record",
]

#: Beside the bundle it describes, so `ls` on the handed-over directory shows
#: the two things the client keeps.
ACCEPTANCE_FILENAME: Final[str] = "acceptance.json"

#: The shape of this document. Not a wire contract (R4) -- a year-old file
#: should still be able to say what it is.
RECORD_VERSION: Final[int] = 1


@dataclass(frozen=True, slots=True)
class OpenQuestion:
    """One unanswered question at handover time."""

    id: str
    #: Who the escalation was routed to when it opened, when anybody was. `None`
    #: is the ordinary local case: Build 3 leaves it unset, and the system's
    #: owner is who it falls to.
    owner_actor_id: str | None = None


def effective_owner(question: OpenQuestion, *, system_owner: str | None) -> str | None:
    """Who owns this question now.

    The explicitly routed person if there is one, else whoever owns the system.
    After `close` the system owner **is** the receiving owner, which is how open
    questions transfer with named owners without this build rewriting a single
    escalation row.
    """
    return question.owner_actor_id or system_owner


def record_payload(
    record: HandoverRecord,
    *,
    open_gaps: Sequence[PackGap] = (),
    open_questions: Sequence[OpenQuestion] = (),
    open_conflicts: Sequence[PackConflict] = (),
    system_slug: str | None = None,
    system_owner: str | None = None,
    written_by: str,
) -> dict[str, Any]:
    """The acceptance record as a mapping. A pure function of its arguments.

    No clock: every timestamp in the result comes from a row the store already
    wrote, so re-rendering this a year later reproduces the same document.
    """
    steps = [
        {
            "step": step,
            "at": _stamp(record.step_taken_at(step)),
            "by": record.step_taken_by(step),
        }
        for step in STEP_ORDER
        if step in record.steps_done
    ]

    rounds = record.verification_rounds
    verification = {
        "rounds": len(rounds),
        "passed": sum(int(row.get("passed", 0)) for row in rounds),
        "failed": sum(int(row.get("failed", 0)) for row in rounds),
        "skipped": sum(int(row.get("skipped", 0)) for row in rounds),
    }

    closure = record.closure
    transfer: dict[str, Any] | None = None
    if record.is_closed:
        transfer = {
            "accepted_by": closure.get("accepted_by"),
            "ownership_assignment_id": closure.get("ownership_assignment_id"),
            "closed_at": _stamp(record.step_taken_at(HANDOVER_CLOSED)),
        }

    return {
        "record_version": RECORD_VERSION,
        "handover_id": record.id,
        "system": {
            "id": record.system_id,
            "slug": system_slug,
            "scope": record.scope,
            "environment_id": record.environment_id,
        },
        "receiving_owner": {"id": record.receiving_owner, "is_group": record.is_group},
        "started_at": _stamp(record.started_at),
        "started_by": record.started_by,
        "opening": record.opening,
        "steps": steps,
        "packs": [
            {
                "audience": pack.get("audience"),
                "sha256": pack.get("markdown_sha256"),
                "unverified_sections": pack.get("unverified_sections"),
            }
            for pack in record.packs
        ],
        "verification": verification,
        "snapshot": record.snapshot or None,
        "transfer": transfer,
        "open_items": {
            "gaps": [
                {"gap_key": gap.gap_key, "uri": gap.uri, "owner": gap.owner_actor_id}
                for gap in open_gaps
            ],
            "questions": [
                {
                    "id": question.id,
                    "effective_owner": effective_owner(question, system_owner=system_owner),
                }
                for question in open_questions
            ],
            "conflicts": [
                {"uri": conflict.uri, "kind": conflict.kind} for conflict in open_conflicts
            ],
        },
        "written_by": written_by,
    }


def render_record(
    record: HandoverRecord,
    *,
    open_gaps: Sequence[PackGap] = (),
    open_questions: Sequence[OpenQuestion] = (),
    open_conflicts: Sequence[PackConflict] = (),
    system_slug: str | None = None,
    system_owner: str | None = None,
    written_by: str,
) -> str:
    """`acceptance.json`'s bytes: canonical JSON, sorted keys, one trailing newline.

    Rendered with the export writer's rule for the sidecar's reason -- a document
    two parties compare must not differ because one of them ran Windows.
    """
    payload = record_payload(
        record,
        open_gaps=open_gaps,
        open_questions=open_questions,
        open_conflicts=open_conflicts,
        system_slug=system_slug,
        system_owner=system_owner,
        written_by=written_by,
    )
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _stamp(value: _dt.datetime | None) -> str | None:
    return None if value is None else format_timestamp(value)
