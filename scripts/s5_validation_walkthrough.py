"""Seed a scratch store for S5's Final Output Validation.

Not a test and not part of the suite: item 3 of `05` S5 names a **command
sequence** rather than a test file, and there is no `adopt init` until S6, so
something has to put a store where `ADOPT_STORE_PATH` points. This builds one and
stops, so the four commands are then run from a shell exactly as an operator runs
them, rather than from inside a Python process that could have arranged the
answer -- the same discipline `s4_validation_walkthrough.py` follows.

The store is the **G0 fixture**, covering every exportable table, because a
round-trip demonstration over a store holding four scope rows would prove the
round trip for four scope rows.

    uv run python scripts/s5_validation_walkthrough.py --store /tmp/g0.db
    export ADOPT_STORE_PATH=/tmp/g0.db
    uv run adopt export /tmp/b1 --json
    uv run adopt import /tmp/b1 --into /tmp/s2.db --json
    uv run adopt export /tmp/b2 --store /tmp/s2.db --json
    diff -r /tmp/b1/tables /tmp/b2/tables      # empty
    grep -c "runtime" /tmp/b1/manifest.json    # 0
"""

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    # `tests/conftest.py` does this for the suite; a script run from a shell has
    # no conftest, and the fixture builder is deliberately shared rather than
    # copied -- a second 300-line seeder is a second place for it to drift from
    # the schema it has to cover.
    sys.path.insert(0, str(_REPO_ROOT))

from adopt_obs import ManualClock  # noqa: E402
from adopt_store import open_store  # noqa: E402
from tests.golden.fixture import FIXTURE_START, build_fixture_store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--store", type=Path, required=True, help="Where to create the store.")
    arguments = parser.parse_args(argv)

    if arguments.store.exists():
        print(f"refusing to overwrite {arguments.store}; remove it first")
        return 1

    # A `ManualClock` rather than the wall clock, so the seeded store is the same
    # store on every run and a difference between two walkthroughs is a defect
    # rather than the hour it was run at.
    clock = ManualClock(FIXTURE_START)
    with open_store(arguments.store, migrate=True, clock=clock) as handle:
        scope = build_fixture_store(handle, clock)

    print(f"seeded {arguments.store} at scope {scope.path()}")
    print(f"export it with:  ADOPT_STORE_PATH={arguments.store} uv run adopt export /tmp/b1 --json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
