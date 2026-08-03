"""The injectable clock. The only source of "now" in the programme.

Sleeps in tests are banned (implementation spec §5). Time-window logic takes a
:class:`Clock` and a test supplies :class:`ManualClock`, which makes the test
both instant and deterministic -- a sleep is a flake with a delay attached.

All times are RFC 3339, UTC, millisecond precision, ``Z``-suffixed. Local time
never appears in a stored value or a wire payload.
"""

import datetime as _dt
from typing import Final, Protocol, runtime_checkable

__all__ = [
    "Clock",
    "ManualClock",
    "SystemClock",
    "format_timestamp",
    "now",
    "truncate_to_millisecond",
]

_TIMESTAMP_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S"
_MICROSECONDS_PER_MILLISECOND: Final[int] = 1000
# const-sync: ok -- the width of a millisecond field, not SCHEMA_VERSION.
_MILLISECOND_DIGITS: Final[int] = 3


def format_timestamp(value: _dt.datetime) -> str:
    """Render RFC 3339, UTC, millisecond precision, ``Z`` suffix.

    Raises:
        ValueError: the datetime is naive. A naive datetime has no defined
            instant, and guessing that it means UTC is how a store ends up with
            timestamps an hour apart from the same event.
    """
    if value.tzinfo is None:
        raise ValueError("naive datetime: every timestamp must carry a timezone")
    utc = value.astimezone(_dt.UTC)
    millis = utc.microsecond // _MICROSECONDS_PER_MILLISECOND
    return f"{utc.strftime(_TIMESTAMP_FORMAT)}.{millis:0{_MILLISECOND_DIGITS}d}Z"


def truncate_to_millisecond(value: _dt.datetime) -> _dt.datetime:
    """Drop precision below a millisecond, in UTC.

    Contracts §1.2 stores millisecond precision, so an instant a writer keeps in
    memory at microsecond precision is **not** the instant that comes back out.
    Anything that persists a timestamp truncates first, or the in-memory row and
    its stored form differ by a value nobody can see and every equality check
    trips over -- including the byte-identical export round-trip.

    Raises:
        ValueError: the datetime is naive.
    """
    if value.tzinfo is None:
        raise ValueError("naive datetime: every timestamp must carry a timezone")
    utc = value.astimezone(_dt.UTC)
    millis = utc.microsecond // _MICROSECONDS_PER_MILLISECOND
    return utc.replace(microsecond=millis * _MICROSECONDS_PER_MILLISECOND)


@runtime_checkable
class Clock(Protocol):
    """The seam. Production passes :class:`SystemClock`; tests pass
    :class:`ManualClock`."""

    def now(self) -> _dt.datetime:
        """The current instant, timezone-aware and in UTC."""
        ...


class SystemClock:
    """The wall clock. The only implementation permitted in production code."""

    def now(self) -> _dt.datetime:
        return _dt.datetime.now(_dt.UTC)


class ManualClock:
    """A clock a test drives by hand.

    ``advance()`` moves it forward; it never moves on its own, so a test that
    depends on elapsed time states exactly how much elapsed instead of waiting
    for it.
    """

    def __init__(self, start: _dt.datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware start instant")
        self._now = start.astimezone(_dt.UTC)

    def now(self) -> _dt.datetime:
        return self._now

    def advance(self, delta: _dt.timedelta) -> None:
        if delta < _dt.timedelta(0):
            raise ValueError("a clock does not run backwards; use a new ManualClock")
        self._now += delta


_default: Final[SystemClock] = SystemClock()


def now() -> _dt.datetime:
    """The process-default clock reading.

    Library code that can take a :class:`Clock` should take one. This exists
    for the places that genuinely cannot -- module import time, and the logger's
    own timestamp.
    """
    return _default.now()
