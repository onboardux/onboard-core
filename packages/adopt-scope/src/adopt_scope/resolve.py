"""The resolved scope chain: ids **and** slugs, at every level.

`resolve()` returns both because the two consumers need different halves — the
identity URI builder needs slugs (CR-05: a URI that depends on an internal ULID
stops resolving the moment the store leaves our hands) and the store needs ids
(a ULID is the join key). Returning one and making the caller look up the other
is how a lookup ends up in a loop that runs per identity.

A `Scope` is resolved to whatever depth was asked for. `firm` is always present;
the three levels below it are `None` when the caller resolved above them. That
is deliberately not modelled as four separate classes: every consumer walks the
same chain, and a partial chain is a normal state rather than an error.
"""

from dataclasses import dataclass
from typing import Final, Literal

from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "SCOPE_LEVELS",
    "Scope",
    "ScopeLevel",
    "ScopeNode",
    "ScopePath",
]

ScopeLevel = Literal["firm", "engagement", "system", "environment"]

#: Outermost first. The order the path grammar and every parent walk follow.
SCOPE_LEVELS: Final[tuple[ScopeLevel, ...]] = ("firm", "engagement", "system", "environment")

_PATH_SEPARATOR: Final[str] = "/"


@dataclass(frozen=True, slots=True)
class ScopeNode:
    """One level of the chain, carrying both of its names."""

    id: str
    slug: str


@dataclass(frozen=True, slots=True)
class Scope:
    """A resolved chain. Only `firm` is guaranteed present."""

    firm: ScopeNode
    engagement: ScopeNode | None = None
    system: ScopeNode | None = None
    environment: ScopeNode | None = None

    @property
    def depth(self) -> int:
        """How many levels resolved, 1-4."""
        levels = (self.firm, self.engagement, self.system, self.environment)
        return sum(1 for level in levels if level is not None)

    def slugs(self) -> tuple[str, ...]:
        """The resolved slugs, outermost first — the URI builder's input."""
        levels = (self.firm, self.engagement, self.system, self.environment)
        return tuple(level.slug for level in levels if level is not None)

    def path(self) -> str:
        """The canonical `firm/engagement/system/environment` rendering."""
        return _PATH_SEPARATOR.join(self.slugs())


@dataclass(frozen=True, slots=True)
class ScopePath:
    """A parsed scope path: between one and four slugs, outermost first.

    Parsing is separated from resolution so that a malformed path fails before
    any query runs, and so the same grammar serves the CLI and the facade.
    """

    firm: str
    engagement: str | None = None
    system: str | None = None
    environment: str | None = None

    @classmethod
    def parse(cls, path: str) -> "ScopePath":
        """Parse ``firm[/engagement[/system[/environment]]]``.

        Raises:
            AdoptError: ``SCOPE_SLUG_INVALID`` when the path is empty, has more
                than four segments, or contains an empty segment.
        """
        segments = path.strip().split(_PATH_SEPARATOR) if path.strip() else []
        if not segments or len(segments) > len(SCOPE_LEVELS) or any(not s for s in segments):
            raise AdoptError(
                ErrorCode.SCOPE_SLUG_INVALID,
                message=f"scope path {path!r} is not one to four non-empty slugs",
                hint=(
                    "A scope path is `firm`, `firm/engagement`, "
                    "`firm/engagement/system` or `firm/engagement/system/environment`."
                ),
            )
        padded = (*segments, *(None,) * (len(SCOPE_LEVELS) - len(segments)))
        # const-sync: ok -- positional indexes into the four scope levels, not SCHEMA_VERSION.
        return cls(firm=segments[0], engagement=padded[1], system=padded[2], environment=padded[3])

    def levels(self) -> tuple[tuple[ScopeLevel, str], ...]:
        """The requested levels in order, stopping at the first absent one."""
        values = (self.firm, self.engagement, self.system, self.environment)
        resolved: list[tuple[ScopeLevel, str]] = []
        for level, value in zip(SCOPE_LEVELS, values, strict=True):
            if value is None:
                break
            resolved.append((level, value))
        return tuple(resolved)
