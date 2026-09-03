"""Optional grounded synthesis, and the discard that makes it safe.

**Critical semantic invariant #7 lives in `ground`.** A synthesis that cites no
store revision is discarded, never served, never persisted. The rule is written
as a function returning `Synthesis | None` rather than as a validation that
raises, for the same reason invariant #5 is a type rather than a check: a `None`
has to be handled at the call site, and the only thing a caller can do with it is
serve the extractive answer. A raise could be caught and logged; a `None` cannot
be quietly converted into an answer.

**Four ways a synthesis is discarded**, and they are one rule seen from four
angles -- *the model may not introduce anything*:

* it cited nothing;
* it cited a revision that was not in the retrieved set (a fabricated or
  remembered id -- the most dangerous case, because a plausible `krev_...` reads
  as grounded);
* its output could not be parsed into the declared shape;
* its prose is empty.

**Why discarding is cheap and is therefore the right default.** The extractive
answer is already complete: the store holds prose a human wrote, and quoting it
with attribution is a correct answer to the question. Synthesis only makes that
answer *nicer to read*. So there is never a moment where discarding leaves the
FDE with nothing, which is precisely what lets the rule be absolute -- a
grounding check whose failure mode is "no answer at all" is a check somebody
eventually relaxes.

**Nothing here is persisted, ever.** v6.1 is explicit that synthesis output is a
rendering, not knowledge: it carries no revision, nothing cites it, and a second
identical question re-derives it. `adopt answer` is how prose becomes knowledge,
and that path requires a human to have written the words.

**Offline is refused by the seam, not here.** `adopt_agent.Runner` raises
`ADOPT_OFFLINE_DENIED` when no network is permitted, and `available()` reports
whether an adapter is configured at all. `synthesize` calls neither -- it takes a
runner it was handed, and the caller that had no adapter never calls it. That is
R3's no-model mode: not a flag, an absent argument.
"""

import json
from collections.abc import Set
from dataclasses import dataclass
from typing import Any, Final

from adopt_agent import AgentRequest, AgentRunner, Budget
from adopt_ask.branch import Answer
from adopt_const import AGENT_ASK_MAX_USD, AGENT_ASK_MAX_WALL_SECONDS
from adopt_obs import AdoptError, get_logger

__all__ = [
    "SYNTHESIS_PROMPT_REF",
    "Synthesis",
    "build_inputs",
    "ground",
    "render_with",
    "synthesize",
]

#: The immutable prompt version, named explicitly. `00` §5 rule 5: a prompt is
#: never edited in place, so a change is a new ref with its own golden-set
#: result table -- and `04` §5.2 rule 2 forbids a "latest", because a caller
#: resolving the newest version would silently change what was asked the day a
#: `v2` landed.
#:
#: **`name/vN`, not `name@vN`.** The loader resolves a ref as a *path* under
#: the prompts root (AI spec §6, `detect-001/v1`'s precedent). The first draft
#: of this constant used `@` and every synthesis discarded silently on a load
#: failure -- which is indistinguishable from a model declining to answer, and
#: made all four invariant-#7 discard tests pass over a function that was
#: returning `None` unconditionally. The positive control is what found it.
SYNTHESIS_PROMPT_REF: Final[str] = "ask-001/v1"

_LOG = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Synthesis:
    """A grounded synthesis. Constructing one is the claim that it is grounded.

    There is no "ungrounded synthesis" value, and that absence is the design:
    `ground` returns `None` for everything that failed, so no part of the system
    downstream of it can hold a synthesis it must remember not to serve.
    """

    answer_md: str
    cited_revision_ids: tuple[str, ...]


def build_inputs(answer: Answer) -> dict[str, Any]:
    """The prompt inputs for `answer`'s citations.

    Only the passages the branch already approved are sent -- they have passed
    the freshness check, the verification filter and the boundary guard, so
    synthesis sees exactly what the extractive answer would have shown and
    nothing the store declined to serve. Withheld unverified revisions are not
    here; sending them would let a model quote knowledge no human confirmed.
    """
    passages = "\n\n".join(
        f"[{citation.revision_id}] {citation.title}\n{citation.body_md}"
        for citation in answer.citations
    )
    return {
        "question": answer.question,
        "passage_count": len(answer.citations),
        "passages": passages,
    }


def ground(output: object, permitted: Set[str]) -> Synthesis | None:
    """Parse and validate one model output. `None` means **discard**.

    Args:
        output: `AgentResult.output` -- a `dict` when the adapter honoured the
            output schema, a `str` when it replied with JSON text, anything at
            all when it did not.
        permitted: The revision ids the retrieved, freshness-approved set
            actually contains. A citation outside this set is a fabrication,
            whatever it looks like.

    Returns:
        The synthesis, or `None` for every failure. Never raises: a model
        replying with nonsense is an expected condition on this path, not an
        error -- the extractive answer is what serves either way, so turning it
        into an exception would only give a caller something to catch and
        mishandle.
    """
    parsed = _as_mapping(output)
    if parsed is None:
        _discard("unparseable")
        return None

    prose = parsed.get("answer_md")
    raw_ids = parsed.get("cited_revision_ids")
    if not isinstance(prose, str) or not prose.strip():
        _discard("empty_prose")
        return None
    if not isinstance(raw_ids, list):
        _discard("citations_not_a_list")
        return None

    cited = tuple(str(value) for value in raw_ids)
    if not cited:
        # Invariant #7, stated plainly. The model was told an empty list is a
        # correct response when the passages do not answer the question, so this
        # is often the model behaving *well* -- and it still discards, because a
        # synthesis nobody can trace to a revision is not servable either way.
        _discard("no_citations")
        return None

    foreign = [value for value in cited if value not in permitted]
    if foreign:
        # The dangerous case. A fabricated or half-remembered `krev_...` reads as
        # grounded to every human who sees it, and a citation the store cannot
        # resolve sends an FDE looking for knowledge that does not exist.
        _discard("foreign_citations", count=len(foreign))
        return None

    return Synthesis(answer_md=prose.strip(), cited_revision_ids=cited)


def synthesize(runner: AgentRunner, answer: Answer, *, idempotency_key: str) -> Synthesis | None:
    """One model call over `answer`'s citations. `None` means serve extractively.

    Args:
        runner: The one model door (`02` §10.1). Injected, so this module cannot
            reach a provider and a caller with no adapter simply never calls it.
        answer: A KNOWN or STALE answer. An UNKNOWN carries no citations, so
            there is nothing to ground on and nothing to synthesize -- calling
            here would be asking a model to invent the answer the store just
            said it does not have.
        idempotency_key: Keyed by the caller on the question and the cited set,
            so re-asking an unchanged question replays the recorded run rather
            than paying for it twice (PRD F13.5).

    Returns:
        A grounded synthesis, or `None` -- for an UNKNOWN answer, a non-`ok`
        run, a budget exhaustion, a provider error, or any of `ground`'s four
        discards. Every one of those serves the extractive answer, which is
        already complete.
    """
    if not answer.citations:
        return None

    permitted = {citation.revision_id for citation in answer.citations}
    try:
        outcome = runner.run(
            AgentRequest(
                skill_ref=SYNTHESIS_PROMPT_REF,
                inputs=build_inputs(answer),
                budget=Budget(
                    max_usd=AGENT_ASK_MAX_USD,
                    max_wall_seconds=AGENT_ASK_MAX_WALL_SECONDS,
                ),
                idempotency_key=idempotency_key,
            )
        )
    except AdoptError as refused:
        # Offline refusal, unavailable adapter, provider failure. All of them
        # mean the same thing here -- no synthesis this time -- and none of them
        # should stop an answer the store can already give without a model.
        _LOG.info("ask_synthesis_unavailable", code=str(refused.code))
        return None

    if outcome.status != "ok":
        _LOG.info("ask_synthesis_discarded", reason=str(outcome.status))
        return None

    return ground(outcome.output, permitted)


def _as_mapping(output: object) -> dict[str, Any] | None:
    """`output` as a mapping, tolerating a JSON string and a fenced one.

    The fence is not defensive programming: CR-52 records a frontier model
    fencing its JSON and burning the seam's single retry on it, on this
    repository's own conformance run. Stripping one is cheaper than a discarded
    answer and cannot make an ungrounded output look grounded -- every citation
    is still checked against `permitted` afterwards.
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
    falling back -- makes a model that has stopped grounding indistinguishable
    from one that was never configured. The event carries a reason and a count
    and **never the model's output**: `adopt_obs`'s deny-list drops `output`
    fields, and the whole point of a discard is that the text was not fit to
    show anybody.
    """
    _LOG.info("ask_synthesis_discarded", reason=reason, **fields)
    return


def render_with(answer: Answer, synthesis: Synthesis) -> str:
    """The human rendering when a synthesis survived grounding.

    The prose replaces the quoted passages; **the citations stay**, because they
    are what the reader checks the prose against. A synthesis rendered without
    them would be exactly the confident uncited paragraph this build exists to
    make impossible -- the fact that it happens to be grounded is invisible to
    the person reading it.
    """
    header = f"{answer.branch.upper()}: {answer.question}"
    if answer.branch == "stale":
        header += f"\nSTALE because {answer.cause} -- the answer below is what was true before."

    lines = [header, "", synthesis.answer_md, "", "Grounded in:"]
    by_id = {citation.revision_id: citation for citation in answer.citations}
    for revision_id in synthesis.cited_revision_ids:
        citation = by_id[revision_id]
        line = f"  {revision_id} -- {citation.title} ({citation.freshness_state})"
        if citation.identity_uris:
            line += "\n    bound to: " + ", ".join(citation.identity_uris)
        lines.append(line)
    return "\n".join(lines)
