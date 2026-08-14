"""The AI pack's golden artifact -- `03` §5.10, `05` S1.5 deliverables.

*"Each pack ships its own fixture repo, hand-labeled identity set and golden
`surface.json`."*

Same shape as the web pack's golden and for the same reasons: what is recorded is
each extractor's **observation set** -- sorted `(kind, namespace, normalized key,
method)` -- taken *before* reconciliation, so a change in either `ai.graph` or
`common.stub_tree` stays visible where the merge would hide it, and so that
`run_id` and `generated_at` never make a diff nobody can read.

Regenerate deliberately with `ADOPT_GOLDEN_UPDATE=1` and read the diff: a changed
golden file is a changed observation set, and that is a claim about a client's
system.
"""

import json
import os
from pathlib import Path

import pytest
from adopt_extractors_ai import pack as ai_pack
from adopt_extractors_common import pack as common_pack
from adopt_map.minting import normalize_local_key

from tests.build1_conftest import context_for

pytestmark = [pytest.mark.golden, pytest.mark.integration]

FIXTURE = Path("fixtures/repos/langgraph-support")
GOLDEN = Path("fixtures/golden/langgraph-support.identities.json")


def _observed() -> list[list[str]]:
    ctx = context_for(FIXTURE, archetype="ai")
    rows: set[tuple[str, str, str, str]] = set()
    for extractor in (*common_pack(), *ai_pack()):
        manifest = extractor.manifest()
        if manifest.archetypes and "ai" not in manifest.archetypes:
            continue
        for fact in extractor.extract(ctx):
            rows.add(
                (
                    fact.identity_kind,
                    fact.namespace or "-",
                    normalize_local_key(fact.identity_kind, fact.local_key),
                    manifest.method,
                )
            )
    return [list(row) for row in sorted(rows)]


def test_the_ai_pack_identity_set_matches_its_golden() -> None:
    """*Defect sentence.* Fails when the packs' observation set over this fixed
    tree changes -- a prompt location stops being recovered, a pin's key shape
    shifts, or an extractor's evidence method is re-banded; matters because each
    of those is a silent change to what a client's map claims, and the per-key
    unit table only sees the keys it names; no other instrument catches a change
    in *method*, because recall and precision are both blind to the band a fact
    was recovered at.
    """
    observed = _observed()

    if os.environ.get("ADOPT_GOLDEN_UPDATE") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(
            json.dumps(
                {
                    "golden_version": 1,
                    "fixture": "langgraph-support",
                    "note": (
                        "Sorted (identity_kind, namespace, normalized local_key, evidence "
                        "method) for the common and ai packs over the fixture, before "
                        "reconciliation. Volatile fields are excluded by construction "
                        "rather than filtered."
                    ),
                    "identities": observed,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        pytest.skip("golden file regenerated; re-run without ADOPT_GOLDEN_UPDATE to assert it")

    assert GOLDEN.exists(), f"no golden artifact at {GOLDEN}; regenerate with ADOPT_GOLDEN_UPDATE=1"
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))["identities"]

    missing = [row for row in expected if row not in observed]
    added = [row for row in observed if row not in expected]
    assert not missing and not added, (
        f"the identity set changed: {len(missing)} lost, {len(added)} gained.\n"
        f"lost:  {missing[:8]}\n"
        f"gained: {added[:8]}"
    )
