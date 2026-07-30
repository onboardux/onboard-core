"""Structured logging. One JSON object per line, no free text, ever.

The signature is the control. :meth:`Logger.emit` takes an ``event`` -- a stable
snake_case name -- and keyword fields. There is **no message parameter**, so
there is no channel through which a body, a prompt, a model output or a chunk of
client source can be logged as prose. That is what makes the log queryable and
what makes "no client content in any log line" a structural property rather than
a habit.

Every line carries ``ts``, ``level``, ``event`` and ``run_id``. Deny-listed
fields are dropped at any depth and the drop count is emitted as
``redacted_fields``, so an attempted leak is visible in the log rather than
silent.

OSS mode is local only, with zero telemetry, permanently. Lines go to stderr or
to a local file the operator chose. There is no opt-in switch to add later.
"""

import json
import sys
from enum import StrEnum
from typing import Any, Final, TextIO

from adopt_obs.clock import Clock, SystemClock, format_timestamp
from adopt_obs.ids import new_id
from adopt_obs.redact import redact

__all__ = ["LogLevel", "Logger", "get_logger", "new_run_id", "set_sink"]

_RESERVED_FIELDS: Final[frozenset[str]] = frozenset(
    {"ts", "level", "event", "logger", "run_id", "redacted_fields"}
)


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    #: Alarm-grade: a defect signal that must page, not merely be recorded.
    #: `COVERAGE_CACHE_DISAGREEMENT` is the canonical example.
    ALARM = "alarm"


# Ordering is derived from declaration order rather than written out, so the
# ranks cannot drift from the enum and no integer literal appears here at all.
_LEVEL_ORDER: Final[dict[LogLevel, int]] = {
    level: rank
    for rank, level in enumerate(
        (LogLevel.DEBUG, LogLevel.INFO, LogLevel.WARN, LogLevel.ERROR, LogLevel.ALARM)
    )
}

_sink: TextIO = sys.stderr
_min_level: LogLevel = LogLevel.INFO


def set_sink(stream: TextIO, *, min_level: LogLevel = LogLevel.INFO) -> None:
    """Point the log at a stream. Used by the CLI and by tests.

    There is no network sink and no remote handler to configure, by design.
    """
    global _sink, _min_level
    _sink = stream
    _min_level = min_level


def new_run_id() -> str:
    """Mint a correlation id for one CLI invocation or one unit of work."""
    return new_id("run")


class Logger:
    """A named emitter. Obtain one with :func:`get_logger`.

    A logger may carry its own ``sink``. Without one it writes to the process
    sink set by :func:`set_sink`. Per-logger sinks exist so a caller -- a test,
    or an embedded use where two components log to different destinations --
    never has to mutate process-wide state to redirect output.
    """

    __slots__ = ("_clock", "_name", "_run_id", "_sink")

    def __init__(self, name: str, run_id: str, clock: Clock, sink: TextIO | None = None) -> None:
        self._name = name
        self._run_id = run_id
        self._clock = clock
        self._sink = sink

    @property
    def run_id(self) -> str:
        return self._run_id

    def bind_run(self, run_id: str) -> "Logger":
        """A copy of this logger correlated to a different run."""
        return Logger(self._name, run_id, self._clock, self._sink)

    def emit(self, level: LogLevel, event: str, /, **fields: Any) -> None:
        """Emit one line.

        Args:
            level: severity.
            event: a stable snake_case event name. Positional-only, so it can
                never be mistaken for a formatted message.
            **fields: structured fields. Deny-listed names are dropped at any
                depth and counted.

        Raises:
            ValueError: the event name is not a stable lowercase identifier, or
                a field collides with a reserved key.
        """
        if _LEVEL_ORDER[level] < _LEVEL_ORDER[_min_level]:
            return
        if not event or not event.replace("_", "").replace(".", "").isalnum():
            raise ValueError(
                f"event name {event!r} must be a stable snake_case identifier "
                "(dots permitted for namespacing); log lines carry no free text"
            )
        if event != event.lower():
            raise ValueError(f"event name {event!r} must be lowercase")
        collisions = _RESERVED_FIELDS & fields.keys()
        if collisions:
            raise ValueError(f"fields {sorted(collisions)} collide with reserved log keys")

        result = redact(fields)
        line: dict[str, Any] = {
            "ts": format_timestamp(self._clock.now()),
            "level": str(level),
            "event": event,
            "logger": self._name,
            "run_id": self._run_id,
        }
        line.update(result.value)
        if result.dropped:
            line["redacted_fields"] = result.dropped
        # `default=str` renders an unexpected object as its repr rather than
        # raising mid-emit. A logger that can throw while reporting a failure
        # turns one incident into two.
        sink = self._sink if self._sink is not None else _sink
        sink.write(json.dumps(line, sort_keys=False, default=str) + "\n")
        sink.flush()

    def debug(self, event: str, /, **fields: Any) -> None:
        self.emit(LogLevel.DEBUG, event, **fields)

    def info(self, event: str, /, **fields: Any) -> None:
        self.emit(LogLevel.INFO, event, **fields)

    def warn(self, event: str, /, **fields: Any) -> None:
        self.emit(LogLevel.WARN, event, **fields)

    def error(self, event: str, /, **fields: Any) -> None:
        self.emit(LogLevel.ERROR, event, **fields)

    def alarm(self, event: str, /, **fields: Any) -> None:
        """Alarm-grade: a defect signal that pages. Use sparingly and never for
        an expected condition."""
        self.emit(LogLevel.ALARM, event, **fields)


def get_logger(
    name: str,
    *,
    run_id: str | None = None,
    clock: Clock | None = None,
    sink: TextIO | None = None,
) -> Logger:
    """Obtain a named logger, minting a run id when one is not supplied."""
    return Logger(name, run_id or new_run_id(), clock or SystemClock(), sink)
