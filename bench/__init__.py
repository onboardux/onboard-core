"""Performance harnesses, one per NFR gate constant.

**A benchmark asserts only on the reference runner** pinned in `bench/RUNNER.md`
(rule 1). Anywhere else it measures and reports, because a number from a
developer's laptop -- or from a shared runner of a different class -- is an
anecdote, and an anecdote that fails a build teaches people to disable the build.

The reference runner identifies itself by setting `ADOPT_BENCH_REFERENCE=1`. That
is deliberately an explicit signal rather than something inferred from
environment variables: inferring it wrongly means either a gate that never fires
or a gate that fires on the wrong hardware, and both are worse than a switch the
workflow sets on purpose.
"""

import os
from typing import Final

__all__ = ["REFERENCE_ENV", "is_reference_runner"]

REFERENCE_ENV: Final[str] = "ADOPT_BENCH_REFERENCE"


def is_reference_runner() -> bool:
    return os.environ.get(REFERENCE_ENV) == "1"
