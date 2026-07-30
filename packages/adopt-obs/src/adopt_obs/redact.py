"""The field deny-list: the mechanism behind "no client content in a log line".

Deny-listed field names are **dropped and counted**, at any nesting depth, on
every structured log line and every error envelope. Counting matters as much as
dropping: a silent drop looks identical to a caller that never passed the field,
so the count is what makes an attempted leak visible in the log itself.

This is defence in depth, not the primary control. The primary control is that
:func:`adopt_obs.log.get_logger` has **no free-text message argument** -- there
is no parameter through which a body, a prompt or a model output can be
smuggled as prose. The deny-list catches the remaining case: a caller who puts
content into a structured field.
"""

from typing import Any, Final

__all__ = ["DENIED_FIELDS", "REDACTED", "RedactionResult", "redact"]

#: Verbatim from implementation spec §4.2 and PRD F16.4.
#:
#: Matching is on the field *name*, case-insensitively, at any depth. It is not
#: a heuristic over values: a value-sniffing redactor gives false confidence,
#: because the moment it misses once the promise "no content in logs" is false
#: and nobody knows.
DENIED_FIELDS: Final[frozenset[str]] = frozenset(
    {"body", "content", "prompt", "output", "source", "text", "answer", "question"}
)

#: What a dropped field is replaced by when the caller asked for replacement
#: rather than removal. Never the value, never a truncation of the value, and
#: never a hash of the value -- a hash of a short secret is a lookup away from
#: the secret.
REDACTED: Final[str] = "[redacted]"

_MAX_DEPTH: Final[int] = 32


class RedactionResult:
    """A sanitized value together with how many fields were dropped."""

    __slots__ = ("dropped", "value")

    def __init__(self, value: Any, dropped: int) -> None:
        self.value = value
        self.dropped = dropped

    def __repr__(self) -> str:
        return f"RedactionResult(dropped={self.dropped})"


def _walk(value: Any, depth: int, counter: list[int]) -> Any:
    if depth > _MAX_DEPTH:
        # A structure this deep is not a log field. Refusing to descend caps
        # the work done on a hostile input and cannot leak, because the value
        # is replaced rather than rendered.
        return REDACTED
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if name.casefold() in DENIED_FIELDS:
                counter[0] += 1
                continue
            cleaned[name] = _walk(item, depth + 1, counter)
        return cleaned
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_walk(item, depth + 1, counter) for item in value]
    return value


def redact(value: Any) -> RedactionResult:
    """Drop every deny-listed field at any depth, counting the drops.

    Scalars pass through untouched: this function sanitizes *shapes*, and a
    caller who passes a bare secret string as the whole payload has already
    bypassed the field vocabulary the logger enforces.
    """
    counter = [0]
    cleaned = _walk(value, 0, counter)
    return RedactionResult(cleaned, counter[0])
