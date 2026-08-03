"""Slug validation, uniqueness and immutability.

A slug is the only part of a scope row the identity URI is built from, which is
what makes an exported bundle resolvable after the store leaves our hands
(CR-05). Three rules follow from that and are enforced here rather than by
whoever writes the next facade:

1. **A slug matches `SLUG_PATTERN` and its length bounds.** The pattern permits
   a single character; `SLUG_MIN_CHARS` does not. Both are checked, because the
   pattern is the character class and the bounds are the length policy, and a
   value satisfying one but not the other is still not a slug.
2. **A slug is set once.** `name` is free to change at any time; a slug is not,
   because every historical URI already contains it.
3. **A slug is never reissued -- and the availability check deliberately ignores
   lifecycle state.** Implementation spec §4.5 behaviour 2 states the rule that
   way round for a reason: consulting lifecycle state is what would let an
   `ARCHIVED` system's slug be handed to a new system, silently re-pointing
   every URI ever emitted for the old one. There is therefore one check and one
   error code for both cases, and no branch on state exists to get wrong.

Every function here is pure. The caller supplies the sibling slugs already taken,
which keeps this module free of any storage dependency and lets the SQLite and
Postgres realizations share one rule set instead of two.
"""

import re
from collections.abc import Iterable
from typing import Final

from adopt_const import SLUG_MAX_CHARS, SLUG_MIN_CHARS, SLUG_PATTERN
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "ensure_slug_available",
    "ensure_slug_unchanged",
    "is_valid_slug",
    "validate_slug",
]

#: Compiled once. The pattern itself lives in `adopt_const` and is never
#: restated here -- a second copy of a format rule is a second thing to update.
_SLUG_RE: Final[re.Pattern[str]] = re.compile(SLUG_PATTERN)


def is_valid_slug(value: str) -> bool:
    """Whether ``value`` satisfies both the character class and the length bounds."""
    return bool(_SLUG_RE.match(value)) and SLUG_MIN_CHARS <= len(value) <= SLUG_MAX_CHARS


def validate_slug(value: str, *, level: str) -> None:
    """Raise ``SCOPE_SLUG_INVALID`` unless ``value`` is a well-formed slug.

    Args:
        value: The proposed slug.
        level: The scope level being named, quoted back in the message so the
            failure says which of the four levels rejected the value.

    Raises:
        AdoptError: ``SCOPE_SLUG_INVALID``.
    """
    if is_valid_slug(value):
        return
    raise AdoptError(
        ErrorCode.SCOPE_SLUG_INVALID,
        message=f"{level} slug {value!r} is not a valid slug",
        hint=(
            f"A slug is {SLUG_MIN_CHARS}-{SLUG_MAX_CHARS} characters of lowercase "
            f"letters, digits and hyphens, starting and ending with a letter or "
            f"digit ({SLUG_PATTERN}). Slugs are lowercase because they appear in "
            f"identity URIs, which compare byte-exact and never case-fold."
        ),
    )


def ensure_slug_available(value: str, taken: Iterable[str], *, level: str) -> None:
    """Raise ``SCOPE_SLUG_REUSED`` when a sibling already holds ``value``.

    ``taken`` must contain **every** sibling slug ever assigned under the parent,
    including those belonging to `ARCHIVED` and `DISCONNECTED` scopes. Filtering
    it by lifecycle state is the defect this function exists to make impossible.

    Raises:
        AdoptError: ``SCOPE_SLUG_REUSED``.
    """
    if value not in set(taken):
        return
    raise AdoptError(
        ErrorCode.SCOPE_SLUG_REUSED,
        message=f"{level} slug {value!r} is already assigned under this parent",
        hint=(
            "Slugs are never reissued, including after ARCHIVED or DISCONNECTED. "
            "Reuse would silently re-point every identity URI ever emitted for the "
            "earlier scope. Choose a different slug."
        ),
    )


def ensure_slug_unchanged(current: str, proposed: str, *, level: str) -> None:
    """Raise ``SCOPE_SLUG_IMMUTABLE`` when a write would change an assigned slug.

    Raises:
        AdoptError: ``SCOPE_SLUG_IMMUTABLE``.
    """
    if current == proposed:
        return
    raise AdoptError(
        ErrorCode.SCOPE_SLUG_IMMUTABLE,
        message=f"{level} slug {current!r} cannot be renamed to {proposed!r}",
        hint=(
            "A slug is set once at creation. `name` is freely mutable and is what "
            "should carry a change of wording; the slug is load-bearing in every "
            "identity URI already emitted."
        ),
    )
