"""Structured logging, typed errors, id generation, redaction, the clock.

The public API of this package is the whole of the programme's observability
surface. Three invariants hold across it and are enforced by CI rather than by
review:

1. **No log line ever contains client source, item bodies, prompt text or model
   output.** There is no free-text log parameter, and deny-listed fields are
   dropped at any depth and counted.
2. **Every error code is in the contracts §13 registry.** `error-registry-sync`
   fails the build in either direction.
3. **Ids are generated nowhere else.** An unregistered prefix is rejected.
"""

from adopt_obs.clock import (
    Clock,
    ManualClock,
    SystemClock,
    format_timestamp,
    now,
    truncate_to_millisecond,
)
from adopt_obs.errors import (
    CATEGORY_EXIT_CODES,
    ERROR_CATEGORIES,
    AdoptError,
    ErrorCategory,
    ErrorCode,
    ExitCode,
    exit_code_for,
)
from adopt_obs.ids import PREFIX_REGISTRY, UnknownPrefixError, new_id, split_id
from adopt_obs.log import Logger, LogLevel, get_logger, new_run_id, set_sink
from adopt_obs.redact import DENIED_FIELDS, REDACTED, RedactionResult, redact

__all__ = [
    "CATEGORY_EXIT_CODES",
    "DENIED_FIELDS",
    "ERROR_CATEGORIES",
    "PREFIX_REGISTRY",
    "REDACTED",
    "AdoptError",
    "Clock",
    "ErrorCategory",
    "ErrorCode",
    "ExitCode",
    "LogLevel",
    "Logger",
    "ManualClock",
    "RedactionResult",
    "SystemClock",
    "UnknownPrefixError",
    "exit_code_for",
    "format_timestamp",
    "get_logger",
    "new_id",
    "new_run_id",
    "now",
    "redact",
    "set_sink",
    "split_id",
    "truncate_to_millisecond",
]
