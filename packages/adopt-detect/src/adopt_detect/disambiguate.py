"""The `04` §4 pre-model gate, and Build 0's one production model caller.

> **The deterministic path runs to completion first. The model sees only what the
> deterministic path explicitly declined to resolve, and receives only that
> evidence.**

That is the whole of this module's job, and every line below is one of the five
steps `04` §4 lists:

1. Walk, score, rank -- `detect()`, and it cannot reach a model.
2. `confidence >= DETECT_CONFIDENCE_MIN` -> **return; the model is never called.**
3. Flag off -> return ambiguous. *(No model.)*
4. Only now: construct `detect-001` with the scores, the rules that fired, and a
   bounded directory listing -- **never file contents, never source code.**
5. The model returns a ranked **proposal**. A human must accept it before anything
   is written.

**Step 4's exclusion is a privacy invariant, not a token-saving measure** (`04`
§4). It is what lets the offline and no-content-leaves-the-environment claims
survive a security review *even when the flag is on*. So the evidence is built
here, from `DetectionResult` and `bounded_listing`, and there is no parameter
through which a caller could add file contents to it.

**This module proposes. It never decides and never writes.** `01` §8's autonomy
matrix allows the model to "propose a ranked archetype with reasoning" and requires
human approval for "writing the archetype -- always, no confidence exemption".
`propose()` therefore returns a value and takes no store, no records port and no
writer: there is no API here through which a proposal could become state, which is
the same way `01` F13.6 is kept for agent-authored code.

**The runner is injected.** `adopt_detect` declares what it needs -- an
`AgentRunner`, which is `02` §10.1's Protocol -- and the composition root hands one
in. So `detect()` and everything under it stay unable to reach a model, and the
package's "no model call on the deterministic path" invariant is structural: with
no runner passed, there is nothing to call.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from adopt_agent import AgentRequest, AgentRunner, Budget
from adopt_const import (
    AGENT_DETECT_LISTING_MAX_ENTRIES,
    AGENT_DETECT_MAX_USD,
    AGENT_DETECT_MAX_WALL_SECONDS,
)
from adopt_detect.detect import DetectionResult, bounded_listing
from adopt_model._enums import Archetype
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "DISAMBIGUATION_PROMPT_REF",
    "ArchetypeProposal",
    "build_evidence",
    "propose",
]

#: The prompt version this pass calls, named explicitly. `04` §5.2 rule 2: callers
#: name the version, and **there is no "latest"** -- a caller that resolved the
#: newest version would silently change what was asked when a `v2` landed, and
#: every trace already written would describe a prompt that no longer ran.
DISAMBIGUATION_PROMPT_REF: Final[str] = "detect-001/v1"

#: One canonical rendering for the evidence, so two runs over the same tree ask
#: the same question and produce the same `inputs_sha256`. The rule `02` §11 fixes
#: for the export bundle, applied here for the same reason.
_JSON: Final[dict[str, Any]] = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}


class ArchetypeProposal(BaseModel):
    """What the model proposed. `04` §5.1's `ArchetypeProposal`, parsed.

    **A proposal, never a decision.** There is no `accepted` field and no
    `apply()`: `01` §8 requires a recorded human decision before an archetype is
    written, and a boolean here would be a shape through which a caller could
    believe that decision had happened.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    primary: Archetype
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    secondary: tuple[Archetype, ...] = ()


def build_evidence(result: DetectionResult, *, root: Path | str) -> dict[str, Any]:
    """The four inputs `04` §5.1's user template names, and nothing else.

    The keys are exactly the template's placeholders -- `scores_json`,
    `rules_fired_json`, `listing_limit`, `listing` -- because the seam renders the
    template with these inputs and a missing key is a refusal rather than a hole.

    **What is deliberately absent is the point:** no file contents, no file sizes,
    no digests of file bodies, no snippet of anything read from the tree. Only
    scores this package computed, rule names and the paths that fired them, and a
    bounded listing of path names.
    """
    listing, truncated = bounded_listing(root, limit=AGENT_DETECT_LISTING_MAX_ENTRIES)
    rules_fired = [
        {"archetype": hit.archetype, "rule": hit.rule_id, "path": hit.path, "why": hit.why}
        for hit in result.rules_fired
    ]
    return {
        "scores_json": json.dumps(dict(result.ranked()), **_JSON),
        "rules_fired_json": json.dumps(rules_fired, **_JSON),
        "listing_limit": AGENT_DETECT_LISTING_MAX_ENTRIES,
        # Truncation is stated in the evidence rather than left for the model to
        # infer from a count: a listing that stopped is less evidence than it
        # looks like, and rule 2 of the prompt asks for low confidence when the
        # evidence does not distinguish.
        "listing": "\n".join(listing) + ("\n... (truncated)" if truncated else ""),
    }


def propose(
    result: DetectionResult,
    *,
    root: Path | str,
    runner: AgentRunner,
) -> ArchetypeProposal:
    """Step 4 and step 5. Call only when steps 1-3 have already declined.

    Args:
        result: A detection result whose `ambiguous` is true. A confident result
            raises rather than being sent: `04` §4 step 2 says the model is *never
            called* when confidence clears the threshold, and enforcing that here
            rather than trusting the caller is what makes it an invariant.
        root: The tree, re-walked for the bounded listing. Paths only.
        runner: `02` §10.1's `AgentRunner`. Injected, so this package cannot reach
            a model without one being handed to it.

    Returns:
        The parsed proposal. **Nothing is written and nothing is decided.**

    Raises:
        AdoptError: `DETECT_AMBIGUOUS` when called on a confident result --
            reusing the code the deterministic path already owns for "this tree
            did not resolve", because the failure is the same category of caller
            mistake. `AGENT_OUTPUT_SCHEMA` when the model's reply cannot be parsed
            into a proposal after the seam's single retry.
    """
    if not result.ambiguous:
        raise AdoptError(
            ErrorCode.DETECT_AMBIGUOUS,
            message=(
                f"detection resolved to {result.archetype!r} at confidence "
                f"{result.confidence}; the disambiguation pass is only for a tree "
                f"the deterministic path declined"
            ),
            hint="`04` §4 step 2: above the confidence threshold the model is never "
            "called. Check `result.ambiguous` before calling this.",
        )

    evidence = build_evidence(result, root=root)
    outcome = runner.run(
        AgentRequest(
            skill_ref=DISAMBIGUATION_PROMPT_REF,
            inputs=evidence,
            budget=Budget(
                max_usd=AGENT_DETECT_MAX_USD,
                max_wall_seconds=AGENT_DETECT_MAX_WALL_SECONDS,
            ),
            # Keyed on the evidence, so re-running the pass over an unchanged tree
            # replays the recorded run instead of paying for it twice (PRD F13.5) --
            # and a tree that changed asks a genuinely new question.
            idempotency_key=_evidence_key(evidence),
        )
    )

    if outcome.status != "ok" or not isinstance(outcome.output, dict):
        raise AdoptError(
            ErrorCode.AGENT_OUTPUT_SCHEMA,
            message=(
                f"the disambiguation pass returned status {outcome.status!r} and no proposal object"
            ),
            hint="The deterministic path already produced ranked scores; report those "
            "rather than treating an unusable proposal as an answer. `04` §3: every "
            "Build 0 capability degrades to a working deterministic behaviour.",
        )
    return ArchetypeProposal.model_validate(outcome.output)


def _evidence_key(evidence: dict[str, Any]) -> str:
    """A stable idempotency key for one question about one tree.

    Bounded well inside `IDEMPOTENCY_KEY_MAX_CHARS` by construction rather than by
    truncation: a key silently cut to a column width makes two different questions
    look like a replay of each other, which would return the wrong tree's proposal.
    """
    digest = hashlib.sha256(json.dumps(evidence, **_JSON).encode("utf-8")).hexdigest()
    return f"detect-001-{digest}"
