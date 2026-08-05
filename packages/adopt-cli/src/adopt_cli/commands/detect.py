"""`adopt detect` -- contracts §14.

Pure filesystem, no store, no socket, no model. The command is a thin rendering
of `adopt_detect.detect`; every decision it reports is made there.

**Ambiguity exits `2` and names the way forward** (PRD F10.3, `04` §4 step 3).
The payload carries the ranked scores, the rules that fired and the exact
environment variable to set for a disambiguation pass -- which is a *later*
sprint's feature, named here rather than implemented, because an operator told
"ambiguous" with no next step will pick one themselves.
"""

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from adopt_cli.json_out import emit
from adopt_detect import detect as run_detect
from adopt_detect.detect import DISAMBIGUATION_FLAG, DetectionResult
from adopt_obs import AdoptError, ErrorCode

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


def detect(path: PathArgument = Path(), json_output: JsonOption = False) -> None:
    """Classify a file tree into one archetype, or refuse and rank.

    Exits `2` with `DETECT_AMBIGUOUS` when confidence is below the threshold.
    """
    result = run_detect(path)
    payload = build_payload(result)
    if result.ambiguous:
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
