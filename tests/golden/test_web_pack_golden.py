"""The web pack's golden artifact -- `03` §5.10, `05` S1.4 deliverables.

*"Each pack ships its own fixture repo, hand-labeled identity set and golden
`surface.json`."*

**What is golden here is each extractor's observation set, not the whole file --
and not the reconciled run.** `adopt_map.orchestrator.reconcile_batches` merges two
observations of one referent into the single fact that reaches the store; this
snapshot is taken *before* that, so a change in either extractor stays visible even
where the merge would hide it.

`02` §9.2 makes `run_id` and `generated_at` volatile by declaration, and a snapshot
of the entire artifact would have to be regenerated on every run for reasons that
carry no information -- which is how a snapshot becomes something people update
with `-u` without reading. The golden file holds the **sorted `(kind, namespace,
key, method)` tuples**, which is exactly what changes when extraction changes and
nothing else.

Regenerate deliberately with `ADOPT_GOLDEN_UPDATE=1`, and read the diff: a
changed golden file is a changed observation set, and that is a claim about a
client's system. An environment variable rather than a pytest option, because a
shared `conftest` flag added for one test is surface every other suite inherits.
"""

import json
import os
import time
from pathlib import Path

import pytest
from adopt_extractors_common import pack as common_pack
from adopt_extractors_web import pack as web_pack
from adopt_map.context import Budget, ExtractorContext
from adopt_map.fileindex import build_index
from adopt_map.minting import normalize_local_key

pytestmark = [pytest.mark.golden, pytest.mark.integration]

FIXTURE = Path("fixtures/repos/django-orders")
GOLDEN = Path("fixtures/golden/django-orders.identities.json")


def _observed() -> list[list[str]]:
    ctx = ExtractorContext(
        root=str(FIXTURE),
        index=build_index(FIXTURE),
        budget=Budget.starting_at(time.time(), stage1_s=900.0, total_s=3600.0),
        archetype="web",
        tier="T2",
    )
    rows: set[tuple[str, str, str, str]] = set()
    for extractor in (*common_pack(), *web_pack()):
        manifest = extractor.manifest()
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


def test_the_web_pack_identity_set_matches_its_golden() -> None:
    """*Defect sentence.* Fails when the packs' **observation set** over a fixed
    tree changes -- a route stops being recovered, a key's shape shifts, or an
    extractor's evidence method is re-banded; matters because each of those is a
    silent change to what a client's map claims, and a per-extractor test only
    sees its own slice; no other instrument catches a *shape* change, because
    recall stays 1.0 when a key changes on both sides of the comparison.

    It is also the second half of determinism: `-m determinism` asserts two runs
    agree with each other, and this asserts they agree with a recorded past.
    """
    observed = _observed()

    if os.environ.get("ADOPT_GOLDEN_UPDATE") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(
            json.dumps(
                {
                    "golden_version": 1,
                    "fixture": "django-orders",
                    "note": (
                        "Sorted (identity_kind, namespace, normalized local_key, evidence "
                        "method) for the common and web packs over the fixture. Volatile "
                        "fields are excluded by construction rather than filtered."
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
