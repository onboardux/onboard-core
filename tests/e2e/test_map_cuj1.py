"""CUJ-1 -- a cold FDE maps an unfamiliar web service. The G3 journey.

*Fails when* the command an operator actually types does not produce a usable map:
the scope line missing, the first screen out of `02` section 9.1's normative order,
`surface.md` absent at stage 1, the store empty at exit 0, or a degradation buried
below the inventory. *Matters because* this is **G3** -- the one-hour cold-start
claim the whole free layer is justified by -- and every other journey assumes it.
*No other instrument catches it because* the integration suite drives
`SurfaceWriter`, the scheduler and the emitters each against its own seam, and all
three can be correct while the composition an operator invokes emits nothing: the
defects that survive per-seam testing live in the wiring, the flag parsing and the
artifact paths.

`01` section 4 CUJ-1, five steps and **three** failure branches. The timing arms of
steps 1-4 are the soak's (`bench/map_soak.py`), not this journey's: a fixture tree
finishes in under a second, so asserting a 900-second budget against it would be a
green light that measured nothing. What this asserts is everything the steps claim
*besides* the clock.
"""

from pathlib import Path

import pytest

from tests.e2e.map_journey import INVENTORY_HEADING, Journey

pytestmark = pytest.mark.e2e


def test_cuj1_a_cold_run_produces_a_usable_map(tmp_path: Path) -> None:
    """Steps 1-5: scope, counts, a readable `surface.md`, the store, the greppable URI."""
    journey = Journey(tmp_path, fixture="web")

    run = journey.map()

    # Step 1 -- resolved scope, archetype and tier, before anything else.
    first = run.first_screen()
    assert journey.system_id in first
    assert "prod" in first
    assert "web" in first and "T2" in first

    # Step 2/4 -- per-kind counts, and the full map at exit 0.
    assert run.exit_code == 0
    assert run.payload["counts_by_kind"], "a web fixture that produced no counts"
    assert set(run.payload["counts_by_kind"]) >= {"endpoint", "db_field"}

    # Step 3 -- `surface.md` exists and is readable, with the inventory after the
    # first screen rather than instead of it.
    assert (run.out_dir / "surface.md").exists()
    assert INVENTORY_HEADING in run.surface_md

    # Step 4 -- the store holds identities, revisions, items and bindings.
    assert journey.identity_uris(), "exit 0 with an empty store is not a completed map"
    assert sum(run.payload["revisions_written"].values()) > 0

    # Step 5 -- the FDE greps a domain noun and finds the endpoint URI with its
    # file:line provenance. This is the step the journey exists for: a map whose
    # inventory cannot be searched by a human is not a map.
    inventory = run.surface_md[run.surface_md.find(INVENTORY_HEADING) :]
    assert "orders" in inventory.lower()
    assert "endpoint/" in inventory


def test_cuj1_first_screen_order_is_the_documented_one(tmp_path: Path) -> None:
    """`02` section 9.1: the first screen's order is normative, not stylistic.

    Asserted as **positions**, not as presence. A reader who stops after one screen
    must get the honest headline, and a document carrying all eight blocks in the
    wrong order gives a different reader a different headline.
    """
    run = Journey(tmp_path, fixture="web").map()
    first = run.first_screen()

    order = [
        "## 1. System and environment",
        "## 2. Archetype and tier",
        "## 3. Run",
        "## 4. Counts by kind",
        "## 5. Coverage",
        "## 6. Degradations",
        "## 7. Outside version control",
    ]
    positions = [first.find(heading) for heading in order]
    assert all(p >= 0 for p in positions), f"missing first-screen block: {order}, {positions}"
    assert positions == sorted(positions), f"first screen out of order: {positions}"


def test_cuj1_branch_a_a_missing_grammar_degrades_and_says_so_on_the_first_screen(
    tmp_path: Path,
) -> None:
    """Failure branch A: step 3 still completes, that family degrades, screen one says so.

    **A Kotlin file is planted into the copied tree**, which is `02` section 11's own
    worked example (*"symbol/kotlin: grammar unavailable -> regex"*). Planting is
    what makes this assertion mean anything: the `django-orders` fixture degrades
    nothing, so a version of this test that merely iterated over whatever
    degradations occurred would iterate over an empty list and pass on a build that
    had lost the degradation path entirely. That is the vacuity this repository has
    now found five times, and it is cheaper to plant than to discover later.
    """
    journey = Journey(tmp_path, fixture="web")
    (journey.tree / "Ledger.kt").write_text(
        "package orders\n\nclass Ledger {\n    fun post(amount: Int): Int = amount\n}\n",
        encoding="utf-8",
    )

    run = journey.map()

    # The run completes. A degradation is not a failure -- `01` F7.3: it "never
    # fails the run".
    assert run.exit_code == 0
    assert (run.out_dir / "surface.md").exists()

    # The degradation happened and was recorded.
    degradations = run.payload["degradations"]
    assert any(d["language"] == "kotlin" for d in degradations), degradations

    # And it is on the **first screen**, not merely in the report. `02` section 9.1:
    # "a degradation that does not appear in the first screen is a defect."
    first = run.first_screen()
    assert "## 6. Degradations" in first
    assert "kotlin" in first

    # Nothing from that language claims grammar-level confidence -- `01` F9's
    # acceptance signal, which is the half a "did it say so" assertion misses.
    kotlin_rows = [d for d in degradations if d["language"] == "kotlin"]
    # `02` section 9.2 names these fields `from` and `to`; the dataclass cannot
    # carry Python keywords, so the rename happens at the emitter boundary.
    #
    # **One row per step taken, not one row per language.** On a machine with no
    # `ctags` the ladder walks grammar -> ctags -> regex and records both steps, so
    # the claim is about where the language *left* and where it *landed*, never
    # about a single row: the descent starts at grammar and no step lands back on
    # it. Asserting `from == "grammar"` for every row would fail on the second step
    # of a correct descent, which is the assertion this test made first.
    assert kotlin_rows[0]["from"] == "grammar", kotlin_rows
    assert all(row["to"] != "grammar" for row in kotlin_rows), kotlin_rows


def test_cuj1_branch_c_two_environments_and_no_flag_aborts_naming_both(
    tmp_path: Path,
) -> None:
    """Failure branch C: abort at step 1 with both environment ids and the flag. No default.

    The load-bearing half is the **absence of a default**. A tool that picked
    production because production was first would be wrong in the one direction
    that writes rows into a client's real environment.
    """
    journey = Journey(tmp_path, fixture="web", environments=("prod", "staging"))

    run = journey.map(environment=None, expect=None)

    assert run.exit_code == 4, run.output
    combined = run.output + str(run.payload)
    assert journey.environments["prod"].id in combined
    assert journey.environments["staging"].id in combined
    assert "--environment" in combined
    assert not journey.identity_uris(), "an abort wrote rows"
