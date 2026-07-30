"""Prefixed ULID generation. The only place an id is created in the programme.

Ids are ``<prefix>_<26-char Crockford base32>``, generated store-side and never
by callers. The prefix registry below is the executable copy of
``02-contracts-build0.md`` §1.1: an unregistered prefix is rejected rather than
minted, because a typo'd prefix produces an id that looks valid, joins to
nothing, and is indistinguishable from data loss six months later.

Ids are **monotonic**. Two ids minted in the same millisecond still sort in
creation order, which is what lets export row ordering be deterministic by
primary key without a separate sequence column.
"""

import os
import threading
import time
from typing import Final

__all__ = ["PREFIX_REGISTRY", "UnknownPrefixError", "new_id", "split_id"]

# Crockford base32: no I, L, O or U, so a transcribed id cannot become a
# different valid id through a reading mistake.
_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# const-sync: ok -- base32 packs five bits per character, not WORKFLOW_STEP_MAX_ATTEMPTS.
_BITS_PER_CHAR: Final[int] = 5
_CHAR_MASK: Final[int] = 0x1F
# const-sync: ok -- a byte is eight bits, not CONFORMANCE_CI_MAX_MINUTES.
_BITS_PER_BYTE: Final[int] = 8

# ULID layout: 48 bits of millisecond timestamp, 80 bits of randomness.
# const-sync: ok -- 48 here is the ULID timestamp width, not SLUG_MAX_CHARS.
_TIMESTAMP_BITS: Final[int] = 48
_RANDOM_BITS: Final[int] = 80
# const-sync: ok -- ten base32 characters cover the timestamp, not a minute budget.
_TIMESTAMP_CHARS: Final[int] = 10
_RANDOM_CHARS: Final[int] = 16
_ULID_CHARS: Final[int] = _TIMESTAMP_CHARS + _RANDOM_CHARS
_MAX_RANDOM: Final[int] = (1 << _RANDOM_BITS) - 1
_MAX_TIMESTAMP: Final[int] = (1 << _TIMESTAMP_BITS) - 1
_RANDOM_BYTES: Final[int] = _RANDOM_BITS // _BITS_PER_BYTE
_MS_PER_SECOND: Final[int] = 1000

#: The prefix registry, verbatim from contracts §1.1.
#:
#: `run_` is the log and trace correlation id. It appears in the error envelope
#: (`error.run_id`) and on every structured log line, so it must be mintable
#: here; it was added to §1.1 in the same change that introduced this module.
PREFIX_REGISTRY: Final[dict[str, str]] = {
    "firm": "firm",
    "eng": "engagement",
    "sys": "system",
    "env": "environment",
    "sle": "system_lifecycle_event",
    "idn": "identity",
    "irev": "identity_revision",
    "ki": "knowledge_item",
    "krev": "knowledge_revision",
    "prov": "provenance",
    "bnd": "binding",
    "brev": "binding_revision",
    "cf": "conflict",
    "conn": "connector",
    "sen": "sensor",
    "pd": "probe_definition",
    "pdrev": "probe_definition_revision",
    "prun": "probe_run",
    "pobs": "probe_observation",
    "bv": "baseline_version",
    "ce": "change_event",
    "cls": "classification",
    "clsv": "classifier_version",
    "sre": "silent_repair_eligibility",
    "rb": "review_batch",
    "ri": "review_item",
    "apr": "approval",
    "esc": "escalation",
    "own": "ownership_assignment",
    "aud": "audit_event",
    "ob": "observability_boundary",
    "vb": "value_baseline",
    "ve": "value_event",
    "act": "actor (external reference)",
    "ag": "agent_run (runtime annex)",
    "run": "run (log and trace correlation)",
}


class UnknownPrefixError(ValueError):
    """Raised when a caller asks for an id with an unregistered prefix."""


_lock = threading.Lock()
_last_timestamp_ms: int = -1
_last_random: int = 0


def _wall_clock_ms() -> int:
    """Wall-clock milliseconds.

    Deliberately not the injectable clock: a ULID's timestamp component is an
    ordering device, not an observation. Freezing it in a test would make ids
    non-monotonic across the freeze and would be asserting the wrong thing --
    monotonicity is guaranteed by the counter below, not by time moving.
    """
    return int(time.time() * _MS_PER_SECOND)


def _encode(value: int, length: int) -> str:
    out = ["0"] * length
    for i in range(length - 1, -1, -1):
        out[i] = _ALPHABET[value & _CHAR_MASK]
        value >>= _BITS_PER_CHAR
    return "".join(out)


def _next_ulid() -> str:
    """Mint a monotonic ULID.

    Within one millisecond the random component is incremented rather than
    redrawn, so ordering is total even under a tight write loop. On the
    astronomically unlikely overflow the timestamp advances by one millisecond,
    which keeps ordering correct at the cost of a timestamp one tick ahead.
    """
    global _last_timestamp_ms, _last_random

    with _lock:
        now = _wall_clock_ms()
        if now > _MAX_TIMESTAMP:  # pragma: no cover -- year 10889
            raise OverflowError("ULID timestamp space exhausted")
        if now == _last_timestamp_ms:
            if _last_random >= _MAX_RANDOM:
                _last_timestamp_ms += 1
                _last_random = int.from_bytes(os.urandom(_RANDOM_BYTES)) >> 1
            else:
                _last_random += 1
        elif now < _last_timestamp_ms:
            # The wall clock moved backwards (NTP step, VM restore). Hold the
            # previous millisecond and keep incrementing: an id that sorts
            # correctly matters more than an id whose timestamp is accurate.
            _last_random += 1
        else:
            _last_timestamp_ms = now
            _last_random = int.from_bytes(os.urandom(_RANDOM_BYTES)) >> 1
        timestamp, randomness = _last_timestamp_ms, _last_random

    return _encode(timestamp, _TIMESTAMP_CHARS) + _encode(randomness, _RANDOM_CHARS)


def new_id(prefix: str) -> str:
    """Mint a prefixed ULID.

    ``prefix`` is accepted with or without its trailing underscore -- both
    ``new_id("firm")`` and ``new_id("firm_")`` yield ``firm_01J...``.

    Raises:
        UnknownPrefixError: the prefix is not in the contracts §1.1 registry.
    """
    key = prefix[:-1] if prefix.endswith("_") else prefix
    if key not in PREFIX_REGISTRY:
        known = ", ".join(sorted(PREFIX_REGISTRY))
        raise UnknownPrefixError(
            f"unregistered id prefix {prefix!r}; "
            f"register it in contracts §1.1 and PREFIX_REGISTRY first. Known: {known}"
        )
    return f"{key}_{_next_ulid()}"


def split_id(value: str) -> tuple[str, str]:
    """Split a prefixed id into ``(prefix, ulid)``, validating both halves.

    Raises:
        UnknownPrefixError: the prefix is unregistered.
        ValueError: the ULID component is malformed.
    """
    prefix, separator, ulid = value.partition("_")
    if not separator:
        raise ValueError(f"malformed id {value!r}: no prefix separator")
    if prefix not in PREFIX_REGISTRY:
        raise UnknownPrefixError(f"unregistered id prefix {prefix!r} in {value!r}")
    if len(ulid) != _ULID_CHARS or any(c not in _ALPHABET for c in ulid):
        raise ValueError(f"malformed ULID component in {value!r}")
    return prefix, ulid
