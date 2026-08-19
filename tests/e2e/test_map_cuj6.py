"""CUJ-6 -- a staging extraction.

*Fails when* a staging run emits a production URI, disturbs the production
identity set, or reports a coverage figure that mixes the two. *Matters because*
environment separation is one of the three add-on exit criteria this rebuild exists
to demonstrate, and the failure is silent in the worst direction: a staging fact
written under a production URI becomes a claim about production that nobody made,
and append-only means it is never removed. *No other instrument catches it because*
`tests/integration/test_env_isolation.py` fuzzes extractor output against the
writer, and the command adds the part the fuzz cannot reach -- resolving
`--environment` to the right scope in the first place.

`01` section 4 CUJ-6, three steps and one failure branch. The failure branch is
"structurally impossible", which is a claim worth testing rather than repeating:
`01` F6 makes the environment a segment of every URI the mint produces, so the
assertion here is over the **store**, where a leak would have to land.
"""

from pathlib import Path

import pytest

from tests.e2e.map_journey import Journey

pytestmark = pytest.mark.e2e


def test_cuj6_a_staging_run_carries_the_staging_segment_and_leaves_production_alone(
    tmp_path: Path,
) -> None:
    """Steps 1-3: every URI is staging, production is untouched, coverage is staging's."""
    journey = Journey(tmp_path, fixture="web", environments=("prod", "staging"))

    production = journey.map(environment="prod")
    assert sum(production.payload["revisions_written"].values()) > 0
    production_uris = journey.identity_uris()
    assert production_uris

    staging = journey.map(environment="staging")

    # Step 1 -- every minted URI carries the staging segment.
    staging_uris = journey.identity_uris() - production_uris
    assert staging_uris, "a staging run minted nothing"
    assert all("/staging/" in uri for uri in staging_uris), sorted(staging_uris)[:3]

    # Step 2 -- the production identity set is untouched. Asserted as a **set**,
    # because a count would pass on a run that replaced one production identity
    # with another.
    assert production_uris <= journey.identity_uris()

    # Step 3 -- coverage is reported for staging alone; no cross-environment total.
    coverage = staging.payload["coverage"]
    assert coverage["source"] == "recompute"
    assert coverage["discovered"] <= len(staging_uris) + len(production_uris)
    assert journey.environments["staging"].id in staging.first_screen()
    assert journey.environments["prod"].id not in staging.first_screen()


def test_cuj6_branch_no_staging_run_can_emit_a_production_uri(tmp_path: Path) -> None:
    """Failure branch: a fuzzed extractor cannot reach production. Structurally.

    The mint takes the environment from the **resolved scope**, never from a fact,
    so a fact cannot carry one. That is why this is asserted over the store: the
    only way a production URI could appear from a staging run is if the resolved
    scope were wrong, and the store is where that would show.
    """
    journey = Journey(tmp_path, fixture="web", environments=("prod", "staging"))

    journey.map(environment="staging")

    uris = journey.identity_uris()
    assert uris, "a staging run minted nothing, so this asserts nothing"
    leaked = [uri for uri in uris if "/prod/" in uri]
    assert not leaked, f"a staging run emitted production URIs: {leaked[:3]}"
    assert all("/staging/" in uri for uri in uris)
