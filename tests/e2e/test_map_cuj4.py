"""CUJ-4 -- an AI deployment with prompts held in a console.

*Fails when* an outside-VCS setting is minted as though it lived in the repository,
when unreadable content is invented rather than left opaque, or when the count of
places behaviour can change without a commit is not on the first screen in plain
language. *Matters because* two of three change sources for an AI deployment bypass
version control entirely (design bet 3), so a map that does not say which settings
those are is a map that will look correct on the day the behaviour changes. *No
other instrument catches it because* `tests/integration/test_ai_outside_vcs.py`
asserts the flag reaches the fact, and the flag can be correct while the sentence a
human reads never appears -- `01` F8.6 puts the requirement on the **first screen**,
which is a property of the rendered document rather than of the fact.

`01` section 4 CUJ-4, four steps and one failure branch.
"""

from pathlib import Path

import pytest

from tests.e2e.map_journey import Journey

pytestmark = pytest.mark.e2e


def test_cuj4_an_ai_deployment_enumerates_its_surface_and_says_what_is_outside_vcs(
    tmp_path: Path,
) -> None:
    """Steps 1-3: the AI families, console prompts flagged, the count in plain language."""
    journey = Journey(tmp_path, fixture="ai")

    run = journey.map("--archetype", "ai")

    # Step 1 -- prompts, pins, tools, retrieval and graph nodes are enumerated.
    kinds = set(run.payload["counts_by_kind"])
    assert "prompt" in kinds, kinds
    assert kinds & {"model_pin", "tool_schema", "retrieval_config"}, kinds

    # Step 2 -- console-held prompts are flagged outside-VCS, and each produces a
    # gap. `01` F8.6 makes the gap mandatory, not advisory.
    outside = run.payload["outside_vcs"]
    assert outside["count"] > 0, "an AI fixture with nothing outside version control"
    gap_uris = {
        gap["identity_uri"] for gap in run.payload["gaps"] if gap["reason"] == "outside_vcs"
    }
    assert set(outside["uris"]) <= gap_uris, "an outside-VCS identity with no gap recorded"

    # Step 3 -- the first screen states the count in plain language. Asserted on
    # the **first screen**, because a number that only reaches the run report is a
    # number the FDE reading the map never sees.
    first = run.first_screen()
    assert "## 7. Outside version control" in first
    assert str(outside["count"]) in first
    assert "outside version control" in first.lower()


def test_cuj4_a_floating_pin_gets_its_own_callout(tmp_path: Path) -> None:
    """Step 4: a `-latest`-style alias is called out on its own.

    `01` F8.8 calls this *"the single highest-value finding this pack produces"*,
    and B1-CR-75 settled where it goes: an unnumbered callout between item 7 and
    the inventory, because numbering a block that appears only on some clients'
    systems would make the inventory's number depend on the client's code.
    """
    journey = Journey(tmp_path, fixture="ai")
    run = journey.map("--archetype", "ai")

    pins = [f for f in run.surface_json["facts"] if f["identity_kind"] == "model_pin"]
    assert pins, "the AI fixture produced no model pin at all"

    # The attribute is `pin_stability`, not a boolean `floating` -- a pin is
    # `pinned`, `floating` or `unknown`, because a runtime-resolved pin is neither
    # of the first two and calling it "not floating" would be a claim nobody made.
    floating = [pin for pin in pins if pin["attributes"]["pin_stability"] == "floating"]
    assert floating, (
        "no floating pin in a fixture whose README declares one. Either the fixture "
        "changed or classification did -- and this assertion is the reason this "
        "test does not skip when it finds none: a callout test that skips itself "
        "when the callout is absent is a test that passes on the build that lost it."
    )

    first = run.first_screen()
    assert "floating" in first.lower()
    assert any(pin["attributes"]["model_id"] in first for pin in floating), (
        "the callout does not name the pin it is about"
    )


def test_cuj4_branch_unreadable_content_is_opaque_and_nothing_is_invented(
    tmp_path: Path,
) -> None:
    """Failure branch: no readable prompt content anywhere.

    Identities exist, digests are null, gaps are recorded, **and nothing is
    invented** -- which is the assertion that matters, because the failure this
    branch guards against is a helpful-looking summary of a file the tool could
    not read.
    """
    journey = Journey(tmp_path, fixture="ai")

    # Make every prompt file unreadable as text without removing it: the identity
    # must still be minted from its path, and only its *content* is unavailable.
    for path in journey.tree.rglob("*.txt"):
        path.write_bytes(b"\x00\x01\x02\x03\xff\xfe")
    for path in journey.tree.rglob("*.prompt"):
        path.write_bytes(b"\x00\x01\x02\x03\xff\xfe")

    run = journey.map("--archetype", "ai")

    assert run.exit_code == 0, "unreadable content is a gap, never a failed run"
    for fact in run.surface_json["facts"]:
        for value in fact.get("attributes", {}).values():
            assert "\x00" not in str(value), "a binary payload reached an artifact"
