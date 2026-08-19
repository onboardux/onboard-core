"""CUJ-3 -- a module is renamed.

*Fails when* a rename with no behaviour change produces an orphan plus an
unrelated addition instead of a move, when a binding is deleted rather than
superseded, or when two equally good candidates are silently resolved instead of
declined. *Matters because* an orphan is how a client's knowledge quietly detaches
from the thing it described: the old item still exists, still answers questions,
and now describes code nobody can reach. *No other instrument catches it because*
`tests/integration/test_moves.py` proves the move **rule** over the writer, and the
rule can be right while the command that has to notice a rename across two runs --
prior state, reconciliation, the CLI's own store wiring -- never reaches it.

`01` section 4 CUJ-3, four steps and one failure branch. The declination branch is
the one that matters: `01` section 8's autonomy matrix gives *"resolve an ambiguous
move"* to **nobody in Build 1**, and a build that guessed would be writing an alias
a human never approved.
"""

import shutil
from pathlib import Path

import pytest

from tests.e2e.map_journey import Journey

pytestmark = pytest.mark.e2e


def test_cuj3_a_rename_becomes_one_move_and_no_orphan(tmp_path: Path) -> None:
    """Steps 1-4: a `moved` revision with an alias, bindings superseded, one entry."""
    journey = Journey(tmp_path, fixture="stub")
    journey.map()

    (journey.tree / "orders" / "api.py").rename(journey.tree / "orders" / "views.py")
    second = journey.map()

    # Step 1 -- the old identity moved to the new one, rather than the new one
    # simply appearing.
    moves = second.payload["moves"]
    assert moves, "a rename with no behaviour change produced no move"
    for move in moves:
        assert "orders.api." in move["from"]
        assert "orders.views." in move["to"]

    # Step 3 -- the new identity exists; step 2 -- nothing was deleted, which is
    # what makes the old URI still resolvable for anything that stored it.
    uris = journey.identity_uris()
    assert any("orders.views." in uri for uri in uris)
    assert any("orders.api." in uri for uri in uris), "the moved-from identity was removed"

    # Step 4 -- one entry, not an add plus an orphan. Asserted on the third run:
    # a move recorded once per run would make every later delta report a rename
    # that happened days ago.
    third = journey.map()
    assert third.payload["moves"] == []
    assert third.payload["revisions_written"] == {"identity": 0, "knowledge": 0, "binding": 0}


def test_cuj3_branch_two_candidates_decline_and_record_a_conflict(tmp_path: Path) -> None:
    """Failure branch: two candidates match, so no move is emitted and a conflict is.

    Built by **copying** the renamed file rather than moving it, so two identical
    declaration sets carry one prior identity's digest. That is the ambiguity the
    rule must decline: choosing either would be a coin toss recorded as a fact.
    """
    journey = Journey(tmp_path, fixture="stub")
    journey.map()

    source = journey.tree / "orders" / "api.py"
    shutil.copyfile(source, journey.tree / "orders" / "views.py")
    shutil.copyfile(source, journey.tree / "orders" / "handlers.py")
    source.unlink()
    second = journey.map()

    assert second.payload["moves"] == [], "an ambiguous rename was resolved rather than declined"
    conflicts = second.payload["conflicts"]
    assert conflicts, "an ambiguous rename recorded no conflict for Build 3 to resolve"
    for conflict in conflicts:
        assert conflict["reason"] == "ambiguous_move"
        assert conflict["candidates"] >= 2

    # `01` F5.3 and `00` section 5: Build 1 never marks an identity dead. The
    # declined move leaves the old identity exactly where it was.
    assert any("orders.api." in uri for uri in journey.identity_uris())
