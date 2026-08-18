"""Seed a scratch store for `05` S1.7's Final Output Validation lines 4-7.

Same discipline as `scripts/s4_validation_walkthrough.py` and its S5 sibling: this
builds a store with one firm, engagement, system and environment plus the
`observability_boundary` row `01` F13.3 requires, then **stops**. The `adopt map`
commands are then run from a shell exactly as an operator runs them, rather than
from inside a Python process that could have arranged the answer.

    uv run python scripts/s17_validation_walkthrough.py --root /tmp/s17
    cd /tmp/s17 && ADOPT_STORE_PATH=/tmp/s17/store.db \
      uv run adopt map <repo>/fixtures/repos/rails-unhandled \
      --firm <firm> --engagement <eng> --system <sys> --environment <env> \
      --agent --json
"""

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.build1_conftest import build_scoped_store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--root", required=True, type=Path, help="Where to build the store.")
    args = parser.parse_args(argv)

    handle, scopes = build_scoped_store(args.root)
    prod = scopes["prod"]
    handle.close()
    print(
        json.dumps(
            {
                "store": str(args.root / "store.db"),
                "firm": prod.firm_id,
                "engagement": prod.engagement_id,
                "system": prod.system_id,
                "environment": prod.environment_id,
                "tier": prod.tier,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
