"""N5 -- `build_uri` sustains `URI_BUILD_MIN_PER_SECOND`.

Measures the operation the requirement names, and nothing around it: the scope is
resolved once outside the loop, because resolving scope is a store read that N3
already measures and folding it in here would report a number that moves when
SQLite does.

Two shapes are measured, because they exercise different work: an ASCII key that
percent-encoding leaves almost untouched, and a multi-byte key where NFC
normalization and escaping both do real work. The **slower** of the two is what
the gate asserts on -- reporting the faster one would be choosing the flattering
measurement.

Reports always. **Asserts only on the reference runner** (`bench/RUNNER.md`
rule 1): a number from a developer's laptop is an anecdote, and an anecdote that
fails a build teaches people to disable the build.
"""

import argparse
import sys
import time
from typing import Final

from adopt_const import URI_BUILD_MIN_PER_SECOND
from adopt_identity import build_uri
from adopt_scope import Scope, ScopeNode
from bench import REFERENCE_ENV, is_reference_runner

#: Enough iterations that a scheduler hiccup does not dominate, few enough that
#: the harness stays inside the benchmark job's share of the pipeline.
# const-sync: ok -- an iteration count for this harness, not a tunable.
ITERATIONS: Final[int] = 200_000

_SCOPE: Final[Scope] = Scope(
    firm=ScopeNode(id="firm_bench", slug="northwind"),
    engagement=ScopeNode(id="eng_bench", slug="acme-erp"),
    system=ScopeNode(id="sys_bench", slug="orders-api"),
    environment=ScopeNode(id="env_bench", slug="prod"),
)

#: (label, kind, namespace, key). The second is deliberately the expensive case.
_SHAPES: Final[tuple[tuple[str, str, str | None, tuple[str, ...]], ...]] = (
    ("ascii endpoint", "endpoint", None, ("POST /v1/orders",)),
    ("multi-byte symbol path", "symbol", "billing", ("charges", "refund\u00e9", "\u6ce8\u6587")),
)


def _rate(kind: str, namespace: str | None, key: tuple[str, ...]) -> float:
    """Builds per second, warmed once so the first call's imports do not count."""
    build_uri(_SCOPE, kind, namespace, key)

    started = time.perf_counter()
    for _ in range(ITERATIONS):
        build_uri(_SCOPE, kind, namespace, key)
    elapsed = time.perf_counter() - started

    return ITERATIONS / elapsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--assert",
        dest="do_assert",
        action="store_true",
        help="Fail when the budget is breached on the reference runner.",
    )
    arguments = parser.parse_args(argv)

    rates: list[float] = []
    for label, kind, namespace, key in _SHAPES:
        rate = _rate(kind, namespace, key)
        rates.append(rate)
        print(f"build_uri ({label}): {rate:,.0f}/s over {ITERATIONS:,} builds")

    measured = min(rates)
    print(f"slowest shape: {measured:,.0f}/s (budget {URI_BUILD_MIN_PER_SECOND:,}/s)")

    if not arguments.do_assert:
        return 0
    if not is_reference_runner():
        print(
            f"not the reference runner ({REFERENCE_ENV} is unset): reported, not asserted. "
            "See bench/RUNNER.md rule 1."
        )
        return 0
    if measured < URI_BUILD_MIN_PER_SECOND:
        print(f"FAIL: N5 breached -- {measured:,.0f}/s is below {URI_BUILD_MIN_PER_SECOND:,}/s")
        return 1
    print("PASS: N5 within budget on the reference runner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
