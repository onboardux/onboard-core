"""`adopt detect` -- contracts §14.

Pure filesystem, no store, no socket. The command is a thin rendering of
`adopt_detect.detect`; every decision it reports is made there.

**Ambiguity exits `2` and names the way forward** (PRD F10.3, `04` §4 step 3).
The payload carries the ranked scores, the rules that fired and the exact
environment variable to set for a disambiguation pass.

**With `ADOPT_FEATURE_AGENT_DISAMBIGUATION` on, one model call becomes possible
here -- and the exit code does not change.** That is the human-accept step in its
cheapest honest form: the pass returns a **proposal**, the proposal is printed,
and `adopt detect` still exits `2` with `DETECT_AMBIGUOUS`, because nothing has
been decided and nothing has been written. `01` §8 requires human approval for
"writing the archetype -- always, no confidence exemption", so the only way a
proposal becomes state is an operator reading it and passing
`adopt init --archetype`. There is deliberately no `--accept` flag that would let
one invocation both propose and persist.

**A failed pass is not a failed command.** `04` §3's last row is load-bearing:
every Build 0 capability degrades to a working deterministic behaviour. If the
pass cannot run -- no adapter, no credential, offline, a budget crossing, an
unusable reply -- the ranked scores are still reported and the reason is carried
in the payload. The deterministic path *is* the product.
"""

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from adopt_agent import Runner
from adopt_cli.commands.agent import adapter_settings, disambiguation_enabled, prompts_root
from adopt_cli.json_out import emit
from adopt_cli.store_option import configured_annex
from adopt_detect import detect as run_detect
from adopt_detect.detect import DISAMBIGUATION_FLAG, DetectionResult
from adopt_detect.disambiguate import propose
from adopt_obs import AdoptError, ErrorCode, get_logger

__all__ = ["build_payload", "detect"]

PathArgument = Annotated[Path, typer.Argument(help="The tree to classify. Read, never executed.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def build_payload(result: DetectionResult) -> dict[str, Any]:
    """Contracts §14's `adopt detect` shape: archetype, confidence, scores, rules_fired."""
    return {
        "archetype": result.archetype,
        "confidence": result.confidence,
        "scores": dict(result.ranked()),
        "rules_fired": [
            {"archetype": hit.archetype, "rule": hit.rule_id, "path": hit.path, "why": hit.why}
            for hit in result.rules_fired
        ],
        "files_considered": result.files_considered,
        "truncated": result.truncated,
    }


def _proposal(path: Path, result: DetectionResult) -> dict[str, Any]:
    """`04` §4 steps 4-5, or the reason they did not run.

    Reached only when detection has already declined (step 2) **and** the flag is
    on (step 3). Every failure below degrades to reporting the deterministic
    answer, because `04` §3 makes that the product rather than a fallback.
    """
    log = get_logger("adopt_cli")
    offline, adapter_id, model, endpoint = adapter_settings()
    try:
        with configured_annex() as annex:
            runner = Runner(
                annex=annex,
                # The annex row's scope, and the pass runs before any store
                # exists -- `adopt detect` takes no `--scope` and creates nothing.
                scope_ref=str(path),
                skills_root=prompts_root(),
                offline=offline,
                adapter_id=adapter_id,
                model=model,
                endpoint=endpoint,
            )
            proposal = propose(result, root=path, runner=runner)
    except AdoptError as error:
        # Named, structured, and no payload: the code and the category, exactly
        # what `03` §4.2 permits a log line to carry.
        log.warn("detect.disambiguation_unavailable", code=str(error.code))
        return {"available": False, "reason": error.code.value, "accepted": False}

    return {
        "available": True,
        "primary": proposal.primary,
        "confidence": proposal.confidence,
        "reasoning": proposal.reasoning,
        "secondary": list(proposal.secondary),
        # **Always false, and there is no code path that sets it true.** `01` §8
        # allows no confidence exemption for writing an archetype, so acceptance
        # happens in a separate invocation an operator types.
        "accepted": False,
    }


def detect(path: PathArgument = Path(), json_output: JsonOption = False) -> None:
    """Classify a file tree into one archetype, or refuse and rank.

    Exits `2` with `DETECT_AMBIGUOUS` when confidence is below the threshold --
    including when a disambiguation proposal was obtained, because a proposal is
    not a decision.
    """
    result = run_detect(path)
    payload = build_payload(result)
    if result.ambiguous:
        # Step 3 of `04` §4: with the flag off this returns here, having made no
        # model call and having formed no request. The flag is checked before the
        # pass is imported into the call path at all.
        if disambiguation_enabled():
            payload["proposal"] = _proposal(path, result)
        # Emitted before raising so the operator gets the evidence, not only the
        # refusal. The error envelope alone would say "ambiguous" and nothing
        # about which archetypes were close or which rules fired.
        emit(payload, as_json=json_output, title="adopt detect")
        raise AdoptError(
            ErrorCode.DETECT_AMBIGUOUS,
            message=(
                f"confidence {result.confidence} is below the threshold; "
                f"ranked scores {json.dumps(dict(result.ranked()))}"
            ),
            hint=f"Detection does not guess -- a wrong archetype is a different set of "
            f"extractors, not a slightly wrong answer. Narrow the path to one system, "
            f"or set {DISAMBIGUATION_FLAG}=1 to enable the reasoning pass, whose "
            f"proposal a human must accept before anything is written.",
        )
    emit(payload, as_json=json_output, title="adopt detect")
