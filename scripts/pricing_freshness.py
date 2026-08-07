"""`pricing-freshness`: report price rows nobody has re-verified lately.

AI spec §3 says the price table "carries `verified_on` and a CI check warns past
90 days", and implementation spec §2.2 names this step beside
`PRICING_VERIFIED_MAX_AGE_DAYS`. `adopt_agent.pricing.stale_rows` did the work
from the day it was written and **nothing called it**, so the check the AI spec
requires did not run. This is the caller.

**It warns; it does not fail.** That is the AI spec's word and it is the right
one. A drifted price makes `Cost.usd` an estimate, which is bad; a build that
refuses because a vendor has not edited a page in ninety-one days is worse, and
would be a red gate nobody can fix by writing code. The warning is emitted as a
GitHub Actions annotation so it is visible on the job summary rather than buried
in a log nobody opens on a green run.

**Exit `0` on a stale row is deliberate and is the one thing to be careful
about.** A check that cannot fail is a check that can rot, so `--self-test`
proves it still *detects* -- the same reason `licence_gate.py` and
`no_destructive_sql.py` carry one. What the self-test asserts is detection, not
rejection, because rejection is not this check's job.

Usage:
    python scripts/pricing_freshness.py              # report against today
    python scripts/pricing_freshness.py --self-test  # prove it still detects
"""

import argparse
import datetime as _dt
import sys
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

from adopt_agent.pricing import PRICES, ModelPrice, stale_rows  # noqa: E402
from adopt_const import PRICING_VERIFIED_MAX_AGE_DAYS  # noqa: E402

__all__ = ["main", "report"]


def _render(row: ModelPrice, today: _dt.date) -> str:
    age = (today - row.verified_on).days
    return (
        f"{row.adapter}/{row.model}: verified {row.verified_on.isoformat()} "
        f"({age} days ago, limit {PRICING_VERIFIED_MAX_AGE_DAYS}) -- {row.source}"
    )


def report(today: _dt.date, *, annotate: bool = True) -> list[str]:
    """The lines this check prints. Returns one per stale row, newest first.

    `today` is a parameter for the reason `stale_rows` takes one: freezing the
    process clock to test a date comparison is the thing implementation spec §5's
    injectable clock exists to avoid.
    """
    stale = sorted(stale_rows(today), key=lambda row: row.verified_on, reverse=True)
    lines: list[str] = []
    for row in stale:
        line = _render(row, today)
        lines.append(line)
        if annotate:
            # A GitHub Actions warning annotation. Outside Actions this is an
            # ordinary line of output, which is why it is not conditional on an
            # environment variable -- one rendering that degrades is better than
            # two that can disagree.
            print(f"::warning title=Stale price row::{line}")
    return lines


def _self_test() -> int:
    """Prove the check still detects a row past the window.

    Asserted against a **synthetic** row rather than by moving `today` far into
    the future, because a future date would also make every real row stale and
    the assertion would pass whether or not the comparison worked. Here exactly
    one row is over the limit and the check must find exactly that one.
    """
    # const-sync: ok -- a calendar date; the 8 is a month, not CONFORMANCE_CI_MAX_MINUTES.
    anchor = _dt.date(2026, 8, 6)
    fresh = anchor
    stale = anchor - _dt.timedelta(days=PRICING_VERIFIED_MAX_AGE_DAYS + 1)
    edge = anchor - _dt.timedelta(days=PRICING_VERIFIED_MAX_AGE_DAYS)

    rows = tuple(
        ModelPrice(
            adapter="selftest",
            model=name,
            input_usd_per_million=0.0,
            output_usd_per_million=0.0,
            verified_on=when,
            source="self-test",
        )
        for name, when in (("fresh", fresh), ("edge", edge), ("stale", stale))
    )

    import adopt_agent.pricing as pricing

    original = pricing.PRICES
    try:
        pricing.PRICES = rows  # type: ignore[misc]  # a module constant, replaced for the test
        found = {row.model for row in pricing.stale_rows(anchor)}
    finally:
        pricing.PRICES = original  # type: ignore[misc]

    if found != {"stale"}:
        print(f"SELF-TEST FAILED: expected {{'stale'}}, found {found or '{}'}")
        return 1
    print(
        f"self-test OK: a row verified {PRICING_VERIFIED_MAX_AGE_DAYS + 1} days ago is "
        f"reported, and one verified exactly {PRICING_VERIFIED_MAX_AGE_DAYS} days ago is not"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test", action="store_true", help="Prove the check still detects a stale row."
    )
    parser.add_argument("--today", help="ISO date to measure against. Defaults to the system date.")
    arguments = parser.parse_args(argv)

    if arguments.self_test:
        return _self_test()

    today = _dt.date.fromisoformat(arguments.today) if arguments.today else _dt.date.today()
    stale = report(today)
    if stale:
        print(
            f"\n{len(stale)} of {len(PRICES)} price row(s) are older than "
            f"{PRICING_VERIFIED_MAX_AGE_DAYS} days. `Cost.usd` is an estimate for those "
            f"models until someone re-reads the vendor's page and updates `verified_on` "
            f"in packages/adopt-agent/src/adopt_agent/prices.json."
        )
        # Deliberately 0: AI spec §3 says this check *warns*. See the module
        # docstring -- failing here would be a red gate no code change can fix.
        return 0

    print(
        f"pricing-freshness: OK -- all {len(PRICES)} price rows verified within "
        f"{PRICING_VERIFIED_MAX_AGE_DAYS} days of {today.isoformat()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
