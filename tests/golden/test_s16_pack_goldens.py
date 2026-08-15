"""Golden artifacts for the three S1.6 packs -- `03` §5.10, `05` S1.6 deliverables.

*"Each pack ships its own fixture repo, hand-labeled identity set and golden
`surface.json`."*

Same shape and same reasons as the web and AI goldens: what is recorded is the
pack's **observation set** -- sorted `(kind, namespace, normalized key, method)`
-- so a changed evidence band shows up where recall and precision are both blind
to it, and so `run_id` and `generated_at` never make a diff nobody can read.

**One module for three packs, not three modules.** The three differ only in a
fixture path and an archetype, and `test-generation-discipline`'s default answer
to "new file or extend?" is *extend*: three copies of this loop would be three
places to fix the day the golden format changes. The parameterization keeps each
pack its own test id, so a failure still names which pack moved.
"""

import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from adopt_extractors_common import pack as common_pack
from adopt_extractors_data import pack as data_pack
from adopt_extractors_lowcode import pack as lowcode_pack
from adopt_extractors_platform import pack as platform_pack
from adopt_map.minting import normalize_local_key
from adopt_map.schemas import Extractor

from tests.build1_conftest import context_for

pytestmark = [pytest.mark.golden, pytest.mark.integration]

#: `(fixture name, archetype, the pack under test)`.
CASES: Sequence[tuple[str, str, object]] = (
    ("sf-metadata-bundle", "platform", platform_pack),
    ("powerapps-export", "lowcode", lowcode_pack),
    ("dbt-warehouse", "data", data_pack),
)


def _observed(fixture: str, archetype: str, pack: object) -> list[list[str]]:
    ctx = context_for(Path("fixtures/repos") / fixture, archetype=archetype)
    rows: set[tuple[str, str, str, str]] = set()
    extractors: tuple[Extractor, ...] = (*common_pack(), *pack())  # type: ignore[operator]
    for extractor in extractors:
        manifest = extractor.manifest()
        if manifest.archetypes and archetype not in manifest.archetypes:
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


@pytest.mark.parametrize(("fixture", "archetype", "pack"), CASES, ids=[c[0] for c in CASES])
def test_the_pack_identity_set_matches_its_golden(
    fixture: str, archetype: str, pack: object
) -> None:
    """*Defect sentence.* Fails when a pack's observation set over its fixed
    fixture changes -- a component stops being recovered, a key shape shifts, or
    an extractor's evidence method is re-banded; matters because each of those is
    a silent change to what a client's map claims, and S1.6 re-banded three
    extractors from `declared` to `reflection` on an argument about the artefact,
    which is exactly the kind of change that should never happen twice without a
    reader seeing it; no other instrument catches a change in *method*, because
    recall and precision are both blind to the band a fact was recovered at.
    """
    golden = Path("fixtures/golden") / f"{fixture}.identities.json"
    observed = _observed(fixture, archetype, pack)

    if os.environ.get("ADOPT_GOLDEN_UPDATE") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(
            json.dumps(
                {
                    "golden_version": 1,
                    "fixture": fixture,
                    "note": (
                        "Sorted (identity_kind, namespace, normalized local_key, evidence "
                        "method) for the common pack and this pack over the fixture, before "
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

    assert golden.exists(), f"no golden artifact at {golden}; regenerate with ADOPT_GOLDEN_UPDATE=1"
    expected = json.loads(golden.read_text(encoding="utf-8"))["identities"]

    missing = [row for row in expected if row not in observed]
    added = [row for row in observed if row not in expected]
    assert not missing and not added, (
        f"{fixture}: the identity set changed: {len(missing)} lost, {len(added)} gained.\n"
        f"lost:  {missing[:8]}\n"
        f"gained: {added[:8]}"
    )
