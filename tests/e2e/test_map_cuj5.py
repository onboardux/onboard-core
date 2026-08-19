"""CUJ-5 -- the air-gapped security-review run.

*Fails when* a deterministic run attempts egress, imports or executes client code,
or writes outside `.adopt/` and the store. *Matters because* this is the journey a
client's security reviewer runs before the tool is allowed near a real system, and
it is the one where being wrong costs the engagement rather than a bug report:
`01` section 1.6 makes *"no AI on the critical path"* and *"no phone-home"*
non-negotiable invariants, and `01` N8's poisoned fixture must not detonate. *No
other instrument catches it because* `tests/property/test_offline.py` and
`test_no_client_import.py` each assert one guard against one seam, and a reviewer's
question is about the **whole command**: what did this process do to my machine.

`01` section 4 CUJ-5, three steps and one failure branch.

**On "the network interface administratively down".** `05` S1.8 asks for that in
CI, and it is a real requirement rather than a synonym for the guard being on: a
socket-level deny proves our code refuses, and an interface that is down proves
nothing in the process could have reached a network even by a path we did not
think of. It needs a network namespace (`unshare -rn`), which is a Linux CI job --
`.github/workflows/ci.yml`'s `cuj5-network-down`. What this journey asserts is the
half that holds on every platform: zero egress attempts, zero client imports, and
nothing written outside the permitted paths. The CI job runs this same file with
the interface actually down.
"""

from pathlib import Path

import pytest

from tests.e2e.map_journey import Journey

pytestmark = pytest.mark.e2e


def test_cuj5_an_offline_run_completes_with_zero_egress_and_zero_client_imports(
    tmp_path: Path,
) -> None:
    """Steps 1-2: exit 0, and the run report says nothing left the machine."""
    journey = Journey(tmp_path, fixture="web")

    run = journey.map("--no-agent")

    assert run.exit_code == 0
    report = run.run_report
    assert report["network_attempted"] == 0, "a deterministic run attempted egress"
    assert report["client_imports_attempted"] == 0, "a deterministic run imported client code"
    assert report["agent"]["calls"] == 0, "a `--no-agent` run called a model"


def test_cuj5_nothing_is_written_outside_the_permitted_paths(tmp_path: Path) -> None:
    """Step 3: nothing outside `.adopt/` and the store.

    Asserted by photographing the client tree before and after and requiring the
    photographs to match. A check that looked only for new files would miss an
    **edited** one, and the tree a security reviewer hands over is one they expect
    back unchanged in both senses.
    """
    journey = Journey(tmp_path, fixture="web")
    before = {
        path.relative_to(journey.tree): path.stat().st_mtime_ns
        for path in journey.tree.rglob("*")
        if path.is_file()
    }
    sizes_before = {
        path.relative_to(journey.tree): path.stat().st_size
        for path in journey.tree.rglob("*")
        if path.is_file()
    }

    journey.map("--no-agent")

    after = {
        path.relative_to(journey.tree): path.stat().st_mtime_ns
        for path in journey.tree.rglob("*")
        if path.is_file()
    }
    sizes_after = {
        path.relative_to(journey.tree): path.stat().st_size
        for path in journey.tree.rglob("*")
        if path.is_file()
    }

    assert set(after) == set(before), "the run added or removed a file in the client tree"
    assert sizes_after == sizes_before, "the run changed a file in the client tree"
    assert after == before, "the run touched a file in the client tree"


def test_cuj5_branch_a_poisoned_fixture_does_not_detonate(tmp_path: Path) -> None:
    """Failure branch, and `01` N8: the poisoned tree is read, never run.

    The fixture's whole design is that importing it has an observable effect, so
    *"it did not detonate"* is an assertion rather than an absence: if the tool
    imported it, the marker the fixture writes would exist.
    """
    journey = Journey(tmp_path, fixture="poisoned")

    run = journey.map("--no-agent")

    assert run.exit_code == 0, "a poisoned tree is a tree, not a failed run"
    assert run.run_report["client_imports_attempted"] == 0

    detonations = list(tmp_path.rglob("DETONATED*")) + list(Path.cwd().glob("DETONATED*"))
    assert not detonations, f"the poisoned fixture executed: {detonations}"
