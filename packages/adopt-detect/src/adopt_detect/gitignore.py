"""A deliberately small `.gitignore` reader for the detection walk.

Implementation spec §4.11 requires the walk to respect `.gitignore`. The reason
is not tidiness: a client checkout's ignored paths are build output, vendored
dependencies and virtual environments, and every one of those carries framework
markers that are **not** evidence about the system. A `site-packages` tree makes
every archetype look like every other.

**What is implemented, and what is not.** Root-level `.gitignore` only, with
literal and glob patterns, directory-only patterns (`build/`), anchored patterns
(`/dist`), and negation (`!keep.py`). Nested `.gitignore` files, `.gitmodules`,
the global excludes file and `.git/info/exclude` are **not** read.

That boundary is chosen rather than accidental. Reimplementing git's full
ignore semantics is a project, and getting it subtly wrong means detection
silently reads files git would have hidden -- the failure this module exists to
prevent, reintroduced with more code. The unread sources all *narrow* what git
would ignore, so the worst case here is that detection considers a file git
would have skipped; combined with `_ALWAYS_SKIPPED` in the walk, which covers
the directories that actually distort a score, that is a bounded and visible
gap rather than a silent one.
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

__all__ = ["GitignoreFilter"]

_GITIGNORE: Final[str] = ".gitignore"
_COMMENT: Final[str] = "#"
_NEGATION: Final[str] = "!"


@dataclass(frozen=True, slots=True)
class _Pattern:
    """One compiled ignore line.

    **Two regexes, and the split is what makes `build/` correct.** `self_regex`
    matches the path the pattern names; `under_regex` matches anything beneath
    it. A directory-only pattern applies `is_dir` to the *first* only -- because
    `build/` names a directory, and `build/out.js` is a file that is ignored for
    being **inside** it, not for being it. Testing `is_dir` against both would
    make `build/` hide the directory and leave every file under it visible, which
    is the opposite of what a trailing slash means.
    """

    self_regex: re.Pattern[str]
    under_regex: re.Pattern[str]
    negated: bool
    directory_only: bool

    def matches(self, relative_path: str, *, is_dir: bool) -> bool:
        if self.under_regex.match(relative_path) is not None:
            return True
        if self.directory_only and not is_dir:
            return False
        return self.self_regex.match(relative_path) is not None


@cache
def _compile(pattern: str, anchored: bool) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Translate one gitignore glob into `(matches-itself, matches-under-it)`.

    An unanchored pattern may match at any depth, which is git's rule: `build`
    ignores `build` and `a/b/build` alike. An anchored one (`/dist`) matches only
    from the root.
    """
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:[^/]+/)*")
            # const-sync: ok -- the length of the token `**/`, not SCHEMA_VERSION.
            index += 3
        elif pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    body = "".join(parts)
    prefix = "" if anchored else "(?:.*/)?"
    return (
        re.compile(f"{prefix}{body}\\Z"),
        re.compile(f"{prefix}{body}/.*\\Z"),
    )


@dataclass(frozen=True, slots=True)
class GitignoreFilter:
    """The compiled patterns of one tree's root `.gitignore`."""

    patterns: tuple[_Pattern, ...]

    @classmethod
    def empty(cls) -> "GitignoreFilter":
        return cls(patterns=())

    @classmethod
    def for_tree(cls, root: Path) -> "GitignoreFilter":
        """Read `<root>/.gitignore` if it exists; an absent or unreadable file
        yields an empty filter rather than an error, because a tree without one
        is the common case and an unreadable one is the operator's problem to
        see in `doctor`, not a reason detection cannot run."""
        path = root / _GITIGNORE
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return cls.empty()
        return cls.from_text(text)

    @classmethod
    def from_text(cls, text: str) -> "GitignoreFilter":
        patterns: list[_Pattern] = []
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line.strip() or line.lstrip().startswith(_COMMENT):
                continue
            negated = line.startswith(_NEGATION)
            if negated:
                line = line[1:]
            directory_only = line.endswith("/")
            line = line.rstrip("/")
            if not line:
                continue
            anchored = line.startswith("/")
            if anchored:
                line = line[1:]
            # A pattern containing a slash anywhere is anchored to the root in
            # git's rules, even without a leading one: `doc/frotz` is not
            # `a/doc/frotz`.
            anchored = anchored or "/" in line
            self_regex, under_regex = _compile(line, anchored)
            patterns.append(
                _Pattern(
                    self_regex=self_regex,
                    under_regex=under_regex,
                    negated=negated,
                    directory_only=directory_only,
                )
            )
        return cls(patterns=tuple(patterns))

    def is_ignored(self, relative_path: str, *, is_dir: bool) -> bool:
        """Whether a path relative to the tree root is ignored.

        **Last match wins**, which is git's rule and the reason negation works at
        all: `*.log` followed by `!keep.log` has to leave `keep.log` visible, and
        a first-match-wins reading would hide it.
        """
        ignored = False
        for pattern in self.patterns:
            if pattern.matches(relative_path, is_dir=is_dir):
                ignored = not pattern.negated
        return ignored
