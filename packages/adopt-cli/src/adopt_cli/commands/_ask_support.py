"""The ask pipeline, in one place, so `adopt ask` and `adopt serve` cannot diverge.

v6.1 says `adopt serve` "exposes the same paths on loopback". *Same* is doing
real work in that sentence: two implementations of the ask pipeline would be two
places the freshness check could be skipped, two boundary guards to keep in step
and two answers to one question depending on which door you came through. So the
composition root moved here when serve arrived, and both verbs call
`answer_question`. The payload-parity test asserts what this arrangement makes
structurally true.

**The order below is the contract**, not an implementation detail:

    refresh the derived index -> retrieve candidates -> resolve freshness for
    every one -> compose -> guard against the boundary -> log/escalate -> render

`compose` accepts only candidates already paired with a resolution, so the third
step cannot be skipped by editing this file: the worst a future edit can do is
fail to type-check. That is critical semantic invariant #5, and
`adopt_ask.branch` records why it is a type rather than a check.

**The store is opened by the caller**, because the two callers want different
lifetimes -- one command, versus one request on a server that must not hold a
connection across threads.

**Synthesis is the last step and changes nothing but the rendering.** It runs
only when an adapter is configured, only over passages the branch already
approved, and only if `ground` accepts what came back (invariant #7). The
`--json` payload is deliberately identical either way except for one added
`synthesis` block: a caller that scripts on `branch` and `citations` must not
see a different shape because somebody set `ADOPT_ADAPTER` -- that would make
the no-model mode a different product rather than a complete one (R3).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from adopt_cli.store_option import configured_question_log, configured_search
from adopt_obs import Clock, SystemClock

__all__ = ["AskOutcome", "answer_question"]

#: The config key passive logging reads. Named once so the registry entry and
#: the read have exactly one spelling between them.
LOG_QUESTIONS_KEY = "ADOPT_ASK_LOG_QUESTIONS"


@dataclass(frozen=True, slots=True)
class AskOutcome:
    """One answered question: the machine payload, the human text, the branch."""

    payload: dict[str, Any]
    human: str
    branch: str
    escalation_id: str | None


def answer_question(
    handle: Any,
    question: str,
    *,
    scope: str | None = None,
    reindex: bool = False,
    escalate_flag: bool = False,
    interactive: bool = False,
    confirm: Callable[[str], bool] | None = None,
    resolved_config: Mapping[str, str | None] | None = None,
    synthesize: bool = True,
) -> AskOutcome:
    """Answer `question` against the open store `handle`.

    Args:
        handle: An open, writable store handle. Writable because answering may
            rebuild the retrieval index in the annex beside it -- answering
            itself writes no canon, and `escalate_flag` is the only thing here
            that does.
        question: The question, verbatim.
        scope: `firm/engagement/system/environment`, or `None` for the store's.
        reindex: Rebuild the index even if its stamp says it is current.
        escalate_flag: The `--escalate` flag, or the request's `escalate` field.
            **The action is the consent** (F2), so this alone records the text.
        interactive: Whether a human can answer a prompt. `False` for `--json`
            and for **every** serve request, because an HTTP client is not a
            person and a server that prompted would hang holding a socket.
        confirm: How to ask, injected. `None` in every non-interactive context.
        resolved_config: Config key -> value, for the passive-logging switch.
            Injected so a test can drive the key without the environment.
        synthesize: Whether to attempt the optional synthesis pass. `True` means
            *try*, not *require*: with no adapter configured it is a no-op and
            the extractive answer serves, which is the complete no-model mode
            (R3). Passed `False` by tests that assert the extractive path.

    Returns:
        The `--json` payload, the human rendering, and the branch.

    Raises:
        AdoptError: ``ASK_OUTSIDE_BOUNDARY`` when the declared boundary refuses
            the answer, or none is declared; ``ESCALATION_NOT_FOUND`` when a
            consented escalation has no system to attach to.
    """
    from adopt_ask import compose, guard, json_payload, render, retrieve
    from adopt_ask.branch import Resolved
    from adopt_ask.escalate import consented, escalate, may_escalate
    from adopt_ask.questionlog import log_question, should_log
    from adopt_ask.synthesis import SYNTHESIS_PROMPT_REF, render_with

    from adopt_cli.commands._map_support import resolve_scope
    from adopt_cli.config import resolve_all
    from adopt_detect import BoundaryView
    from adopt_freshness import resolve_freshness
    from adopt_obs import AdoptError, ErrorCode

    resolved_scope = resolve_scope(handle, scope)
    clock: Clock = handle.clock if handle.clock is not None else SystemClock()

    with configured_search(handle, clock=clock) as search:
        search.refresh(force=reindex)
        candidates = retrieve(search, question)
        verified = search.verified_in_store(
            [candidate.passage.revision_id for candidate in candidates]
        )

    freshness_records = handle.freshness_records()
    resolved = tuple(
        Resolved(
            candidate=candidate,
            freshness=resolve_freshness(freshness_records, candidate.passage.item_id, clock=clock),
        )
        for candidate in candidates
    )

    answer = compose(resolved, verified, question)

    system = resolved_scope.system
    environment = resolved_scope.environment
    row = (
        None
        if system is None
        else handle.boundary().current(
            system_id=system.id,
            environment_id=None if environment is None else environment.id,
        )
    )
    guard(
        answer,
        None if row is None else BoundaryView.of(row, archetype=None),
        scope=resolved_scope,
        occurred_at=clock.now(),
    )

    # Passive logging is asked **before** escalation and is entirely separate
    # from it: `--escalate` is consent to store one question, and the config key
    # is an operator's standing decision to store all of them. Reading either
    # from the other would collapse F2's two halves into one.
    config = (
        resolved_config
        if resolved_config is not None
        else {item.key: item.value for item in resolve_all()}
    )
    if should_log(config, key=LOG_QUESTIONS_KEY):
        with configured_question_log(handle) as question_log:
            log_question(
                question_log, answer, scope_ref=resolved_scope.path(), asked_at=clock.now()
            )

    escalation_id: str | None = None
    if consented(answer, escalate_flag=escalate_flag, interactive=interactive, confirm=confirm):
        if system is None:
            raise AdoptError(
                ErrorCode.ESCALATION_NOT_FOUND,
                message=f"the scope {resolved_scope.path()!r} names no system, so there is "
                "nothing to open a question against",
                hint="Pass --scope firm/engagement/system/environment, or open a store whose "
                "default scope resolves to a system.",
            )
        escalation_id = escalate(handle.governance(), answer, system_id=system.id)

    payload = dict(json_payload(answer))
    human = render(answer)

    synthesis = _synthesize(answer, question=question, config=config) if synthesize else None
    if synthesis is not None:
        payload["synthesis"] = {
            "answer_md": synthesis.answer_md,
            "cited_revision_ids": list(synthesis.cited_revision_ids),
            "prompt_ref": SYNTHESIS_PROMPT_REF,
        }
        human = render_with(answer, synthesis)
    if escalation_id is not None:
        payload["escalation_id"] = escalation_id
        human += f"\n\nRecorded as open question {escalation_id}."
        human += f'\nAnswer it with: adopt answer {escalation_id} --text "..."'
    elif may_escalate(answer) and not escalate_flag:
        human += "\n\nRecord it as an open question with `--escalate`."

    return AskOutcome(
        payload=payload, human=human, branch=answer.branch, escalation_id=escalation_id
    )


def _synthesize(answer: Any, *, question: str, config: Mapping[str, str | None]) -> Any:
    """One optional model call. `None` for every reason not to have made it.

    **Every failure here is silent and returns `None`**, because the answer is
    already complete without it: no adapter configured, offline refused, budget
    exhausted, provider error, ungrounded output. Turning any of those into a
    visible failure would make an optional improvement a required dependency,
    which is exactly what R3's no-model mode forbids.

    The idempotency key is the question plus the cited set, so re-asking an
    unchanged question over unchanged knowledge replays the recorded run instead
    of paying for it again (PRD F13.5) -- and a store whose knowledge has moved
    on asks a genuinely new question.
    """
    import hashlib

    from adopt_ask.synthesis import synthesize as run_synthesis

    from adopt_agent import Runner
    from adopt_cli.commands.agent import adapter_settings, prompts_root
    from adopt_cli.store_option import configured_annex
    from adopt_obs import AdoptError

    if not answer.citations:
        return None
    offline, adapter_id, model, endpoint = adapter_settings()
    if not adapter_id:
        # No adapter is the ordinary case and is not a failure: the extractive
        # answer is the product (`04` §3), not a fallback from one.
        return None

    material = "\0".join([question, *(c.revision_id for c in answer.citations)])
    key = f"ask-001:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"

    try:
        with configured_annex() as annex:
            runner = Runner(
                annex=annex,
                scope_ref=question,
                skills_root=prompts_root(),
                offline=offline,
                adapter_id=adapter_id,
                model=model,
                endpoint=endpoint,
            )
            return run_synthesis(runner, answer, idempotency_key=key)
    except AdoptError:
        return None
