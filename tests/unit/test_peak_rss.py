"""`01` N11's instrument answers on the platform it is running on.

*Fails when* `peak_rss_bytes()` stops producing a real byte count -- the Windows
branch returning `None` because a `ctypes` declaration regressed, or the POSIX
branch losing the kilobyte normalisation and understating by 1024x. *Matters
because* N11 is a **ceiling** (<= 2 GiB RSS at `MAP_MAX_WORKERS` on 500k LoC), and
a ceiling compared against `None` is never breached and a ceiling compared against
a 1024x-understated number is never breached either: the NFR passes forever while
measuring nothing. *No other instrument catches it because* every consumer treats
`None` as "unknown" and carries on -- `as_report()` emits the field as `null`, the
soak harness archives a report with an empty slot, and nothing anywhere fails.

**This is the assertion that was red before S1.8.** `resource` is POSIX-only, so
Windows returned `None` and the machine this build was authored on could not
measure its own memory ceiling.
"""

import sys

import pytest
from adopt_map.report import peak_rss_bytes

#: A CPython interpreter with this workspace imported cannot occupy less than
#: this. The bound exists to catch a **unit** error -- a kilobyte reading
#: reported as bytes lands two orders of magnitude below it -- not to assert
#: anything about the platform's allocator.
_IMPLAUSIBLY_SMALL_BYTES = 4 * 1024 * 1024


@pytest.mark.unit
def test_peak_rss_answers_on_every_platform_ci_checks() -> None:
    """`None` is for a platform that cannot answer, and both of ours can."""
    if sys.platform not in {"win32", "linux", "darwin"}:
        pytest.skip(f"{sys.platform} is outside the platforms CI checks")

    value = peak_rss_bytes()

    assert value is not None, (
        f"{sys.platform} reported no peak RSS. `01` N11 cannot be measured on a "
        "platform whose instrument returns None, and the field would be archived "
        "as null rather than failing anything."
    )
    assert value > _IMPLAUSIBLY_SMALL_BYTES, (
        f"peak RSS reported {value} bytes, which is below what an interpreter "
        "with this workspace imported can occupy -- the reading is in the wrong "
        "unit rather than merely small."
    )


@pytest.mark.unit
def test_peak_rss_never_reports_a_zero() -> None:
    """A failed measurement is `None`, never `0`.

    Both branches return `None` on failure rather than a zero, because a zero is
    a number no measurement produced and it compares clean against every ceiling.
    """
    value = peak_rss_bytes()

    assert value != 0
