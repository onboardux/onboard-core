"""`adopt ask` -- the Private Project Assistant. Build 3.

Honest three-way answers over the store: **KNOWN** (the answer, citing the exact
revisions, the identity URIs they are bound to, and the rule that let them
serve), **STALE** (the prior answer, served *with* the cause that made it stale),
**UNKNOWN** (a refusal). Never an unqualified guess.

**The invariant this package exists to hold** (v6.1 §4 R6, critical semantic
invariant #5): *no code path serves an answer without a freshness resolution.*
`branch.compose` takes `Resolved` values -- a candidate welded to its resolution
-- so a caller cannot express "serve this passage, I did not check". The rule is
a type, not a review comment. See `adopt_ask.branch` for why that shape was
chosen over a required mapping argument.

Three further postures, each inherited rather than invented:

* **No model call anywhere in this sprint.** The extractive answer *is* the
  complete no-model mode (R3): the store holds prose a human wrote, so answering
  is quotation with attribution rather than generation. Optional grounded
  synthesis arrives in S3.2, behind the existing agent seam, and is discarded
  when it cites nothing (invariant #7).
* **The index is derived, never canon.** FTS5 lives in the runtime annex,
  rebuilt from the store whenever the two disagree, never exported. One canon
  (R9) survives the existence of a second searchable copy because the second one
  is disposable and is treated as such.
* **Only verified knowledge serves as KNOWN** (F6). Enforced when the index is
  built *and* again when the branch is decided, against the store rather than
  the index -- the derived copy is the wrong authority on what may be said.
"""

from adopt_ask.answer import (
    ASK_EVENT_TYPE,
    envelope,
    guard,
    json_payload,
    render,
    sendable_payload,
)
from adopt_ask.branch import (
    KNOWN,
    STALE,
    UNKNOWN,
    Answer,
    Branch,
    Citation,
    Resolved,
    compose,
)
from adopt_ask.records import Passage, RefreshOutcome, SearchRecords
from adopt_ask.retrieve import Candidate, CandidateOrigin, retrieve, uris_in

__all__ = [
    "ASK_EVENT_TYPE",
    "KNOWN",
    "STALE",
    "UNKNOWN",
    "Answer",
    "Branch",
    "Candidate",
    "CandidateOrigin",
    "Citation",
    "Passage",
    "RefreshOutcome",
    "Resolved",
    "SearchRecords",
    "compose",
    "envelope",
    "guard",
    "json_payload",
    "render",
    "retrieve",
    "sendable_payload",
    "uris_in",
]
