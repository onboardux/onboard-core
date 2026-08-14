"""`surface.json` -- a **run artifact, not an interchange contract** (`02` §9.2).

B1-CR-09 and `01` F11.1: Build 0's JSON export is the canonical machine-readable
contract, and this build does not define a second one. Downstream builds read the
store or that export. This file is for review, debugging and the labeled-corpus
tooling, and its `report_version` is Build 1's own integer line.

**Facts are byte-sorted by identity URI** (`02` §10 C15). Sorted on the *encoded
bytes* rather than on Python's string comparison, because the two differ for
non-ASCII keys and `01` N4 makes the artifact's bytes part of what determinism
means. The sort happens once, in `RunResult.minted()`, so this emitter and the
markdown inventory cannot disagree about the order.
"""

import json
from typing import Any, Final

from adopt_const import SURFACE_REPORT_VERSION
from adopt_map.report import RunResult
from adopt_obs import format_timestamp

__all__ = ["SURFACE_JSON_NAME", "render_surface_json", "surface_payload"]

SURFACE_JSON_NAME: Final[str] = "surface.json"


def surface_payload(result: RunResult) -> dict[str, Any]:
    """The `02` §9.2 shape, exactly, as a plain structure."""
    write = result.write_result
    return {
        "report_version": SURFACE_REPORT_VERSION,
        "run_id": result.run_id,
        "generated_at": format_timestamp(result.generated_at),
        "scope": {
            "firm_id": result.resolved.firm_id,
            "engagement_id": result.resolved.engagement_id,
            "system_id": result.resolved.system_id,
            "environment_id": result.resolved.environment_id,
            "environment_name": result.resolved.environment_slug,
        },
        "system": {"archetype": result.resolved.archetype, "tier": result.resolved.tier},
        "toolchain": {
            "adopt_version": result.adopt_version,
            # Empty maps rather than absent keys: `02` §9.2 declares both fields,
            # and an integrator's `jq '.toolchain.grammars'` should find an empty
            # object rather than `null` when no grammar backend is loaded. The
            # grammar and indexer packs land in S1.4.
            "grammars": {},
            "indexers": {},
        },
        "counts_by_kind": result.counts_by_kind(),
        "coverage": None if result.coverage is None else result.coverage.as_report_block(),
        "revisions_written": None if write is None else dict(write.revisions_written),
        "degradations": [entry.as_report_row() for entry in result.degradations],
        "outside_vcs": {
            "count": len(result.outside_vcs()),
            "uris": list(result.outside_vcs()),
        },
        "moves": []
        if write is None
        else [{"from": move.from_uri, "to": move.to_uri} for move in write.moves],
        "conflicts": []
        if write is None
        else [
            {
                "identity_uri": conflict.identity_uri,
                "reason": conflict.reason,
                "candidates": conflict.candidates,
            }
            for conflict in write.conflicts
        ],
        "truncated_families": list(result.truncated_families),
        "gaps": []
        if write is None
        else [
            {"identity_uri": gap.identity_uri, "reason": gap.reason, "detail": gap.detail}
            for gap in write.gaps
        ],
        "facts": [
            {
                "identity_uri": entry.uri,
                "identity_kind": entry.fact.identity_kind,
                "namespace": entry.fact.namespace,
                "title": entry.fact.title,
                "attributes": entry.fact.validated_attributes().model_dump(mode="json"),
                "relations": [
                    {
                        "predicate": relation.predicate,
                        "target_kind": relation.target_kind,
                        "target_namespace": relation.target_namespace,
                        "target_local_key": relation.target_local_key,
                    }
                    for relation in entry.fact.relations
                ],
                "extractor": entry.extractor,
                "method": entry.method,
                "confidence": entry.confidence,
                "outside_vcs": entry.fact.outside_vcs,
                "opaque": entry.fact.opaque,
            }
            for entry in result.minted()
        ],
    }


def render_surface_json(result: RunResult) -> str:
    """`surface.json`'s bytes.

    `sort_keys=True` and a fixed indent, so two runs over one tree at one tool
    version produce byte-identical files apart from the two fields `02` §9.2
    declares volatile (`run_id`, `generated_at`). That is what the determinism
    gate compares.
    """
    return json.dumps(surface_payload(result), indent=2, sort_keys=True) + "\n"
