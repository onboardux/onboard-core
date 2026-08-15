"""`labeling_queue.json` -- the unlabelled bucket as a file. `01` F12.6, PRD Q7.

**PRD Q7 was open and its stated default is a file artifact**: *"Opaque-field
labeling queue -- file artifact or a table? **Default: file artifact.** A table
is a Build 0 amendment."* `00` §9 rule 4 forbids Build 1 a new column, so a table
is not this build's to add, and the default is taken and flagged
(`docs/pack/OPEN-DECISIONS.md` OD-14) rather than assumed silently.

**It is a work queue for a person, so it is written for a person.** Each entry
carries the URI, the platform's own API name, whatever type the bundle stated and
where it was read from -- and **no label field, no candidate field, and no
score**. `01` §8: labelling opaque platform fields is *Human, required,
auto-promotion **never***. A queue with a writable answer slot is a queue
something will eventually write into.

**Emitted only on a run that found components.** A `metadata_component`-free run
-- every web and AI run -- has no unlabelled bucket to report, and writing an
empty queue beside every `surface.md` would teach a reader to ignore the file
that matters on the one archetype where it does. A platform run with everything
labelled *does* get the artifact, with `"entries": []`: there the question was
asked and the answer was none, which is a different fact from not asking.
"""

import json
from typing import Any, Final

from adopt_const import SURFACE_REPORT_VERSION
from adopt_map.report import RunResult
from adopt_map.unlabeled import BUCKETED_KIND, unlabeled_components
from adopt_obs import format_timestamp

__all__ = ["LABELING_QUEUE_NAME", "labeling_queue_payload", "render_labeling_queue", "wanted"]

LABELING_QUEUE_NAME: Final[str] = "labeling_queue.json"


def wanted(result: RunResult) -> bool:
    """Whether this run has an unlabelled bucket to report at all."""
    return BUCKETED_KIND in result.counts_by_kind()


def labeling_queue_payload(result: RunResult) -> dict[str, Any]:
    """The queue as a plain structure."""
    entries = unlabeled_components(result)
    total = result.counts_by_kind().get(BUCKETED_KIND, 0)
    return {
        "report_version": SURFACE_REPORT_VERSION,
        "run_id": result.run_id,
        "generated_at": format_timestamp(result.generated_at),
        "scope": {
            "system_id": result.resolved.system_id,
            "environment_id": result.resolved.environment_id,
            "environment_name": result.resolved.environment_slug,
        },
        # Both numbers, because the ratio is the finding. "48 unlabelled" means
        # nothing; "48 of 51" is design Appendix B's honest limit, measured.
        "components": total,
        "unlabeled": len(entries),
        "instructions": (
            "Each entry names a component this export did not label. A label is a "
            "human's to write: record it in the platform, re-export, and re-run "
            "`adopt map`. Nothing in this tool will fill one in."
        ),
        "entries": [
            {
                "identity_uri": entry.uri,
                "namespace": entry.namespace,
                "api_name": entry.api_name,
                "component_type": entry.component_type,
                "opaque": entry.opaque,
                "evidence": entry.evidence(),
                "source_path": entry.source_path,
            }
            for entry in entries
        ],
    }


def render_labeling_queue(result: RunResult) -> str:
    """The queue's bytes. Sorted keys and a trailing newline, like every artifact."""
    return json.dumps(labeling_queue_payload(result), indent=2, sort_keys=True) + "\n"
