"""One walk of the client tree, shared by everything -- `03` §5.8.

**The tree is walked exactly once per run.** Not as an optimization: every
extractor seeing the *same* file set is what makes `01` N4 determinism a property
of the run rather than of how many extractors happened to be enabled. Two walks
are two chances to disagree about what the tree contains, and a fact emitted
against a file another extractor never saw is a fact nobody can reproduce.

**The tree is opened read-only** (`03` §6). Nothing here writes, and nothing here
imports, executes, evaluates or dynamically loads anything it finds -- `02` §7
obligation 1, proven by the `poisoned-import` fixture. A file is bytes to this
module and to every extractor downstream of it.

**Sampling is disclosed or it does not happen.** Above `MAP_MAX_TREE_FILES` the
index keeps a `MAP_SAMPLING_MODE_RATIO` share, and `sampled` plus both
denominators travel with the index into the run report and onto the first screen
of `surface.md`. A map that silently examined a quarter of a tree and reported
coverage over the whole one is the single most expensive lie this build could
tell, so the disclosure is a field on the index rather than a line somebody
remembers to print.

**The sample is a pure function of the path.** A digest threshold over the
repo-relative path, so the same tree yields the same sample on every machine and
at every tree size -- a stride or a random sample would make `source_version`
stability depend on how many files happened to be indexed.
"""

import hashlib
import os
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from adopt_const import (
    MAP_BINARY_SNIFF_BYTES,
    MAP_MAX_FILE_BYTES,
    MAP_MAX_TREE_FILES,
    MAP_SAMPLING_MODE_RATIO,
)

__all__ = [
    "CODE_LANGUAGES",
    "IGNORE_FILES",
    "FileEntry",
    "FileIndex",
    "build_index",
    "detect_language",
    "git_blob_sha",
    "is_code",
    "paths",
    "read_text",
]

#: The two ignore files this build honours, in the order they are applied. A
#: `.adoptignore` is read after `.gitignore` so a client can re-include something
#: their VCS ignores, or exclude something it does not.
IGNORE_FILES: Final[tuple[str, ...]] = (".gitignore", ".adoptignore")

#: Always skipped, whatever the ignore files say. `.git` holds object storage
#: rather than client source, and walking it is both large and meaningless.
_ALWAYS_SKIPPED_DIRS: Final[frozenset[str]] = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".adopt"}
)

#: Extension -> language, for the ladder's per-language arm (`01` F9.2) and for
#: the `symbol` namespace convention (`02` §3.1). A suffix this map does not
#: carry yields `None`, which is an honest "we do not know" rather than a guess:
#: a family with no language declines rather than claiming a grammar it has no
#: evidence for.
_LANGUAGE_BY_SUFFIX: Final[dict[str, str]] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
    ".php": "php",
    ".cs": "csharp",
    ".scala": "scala",
    ".swift": "swift",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".env": "dotenv",
    ".md": "markdown",
    ".xml": "xml",
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".tf": "terraform",
    ".sh": "shell",
    ".bash": "shell",
}

#: The digest-threshold denominator, and the digest width that produces it.
#: `int.from_bytes` over eight bytes yields a value in ``[0, 2**64)``; keeping
#: those below ``ratio * 2**64`` keeps very nearly `ratio` of any large path set,
#: without the count depending on the order the walk happened to produce.
#:
#: The two move together and neither is retunable on its own: changing the width
#: changes which files the sample contains, which is the sampling function's
#: identity rather than a threshold anybody may revise against evidence.
_SAMPLE_DIGEST_BYTES: Final[int] = 8  # const-sync: ok -- a digest width, not a tunable
_SAMPLE_SPACE: Final[int] = 1 << (
    _SAMPLE_DIGEST_BYTES * 8  # const-sync: ok -- bits per byte
)


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One indexed file. **Repo-relative, POSIX-separated, always.**

    `03` §5.9 strips absolute paths at emission and `02` §6 forbids one in
    `provenance`. Holding a relative path from the first read means there is
    nothing to strip and nothing to forget to strip -- the same argument
    `SourceRef` makes one layer up.
    """

    path: str
    size: int
    language: str | None
    blob_sha: str
    is_binary: bool


@dataclass(frozen=True, slots=True)
class FileIndex:
    """What one walk found, and what it deliberately did not look at.

    Every count here reaches `run_report.json` and, where it is a degradation,
    the first screen of `surface.md`. `discovered` is what the walk saw before
    any sampling; `len(files)` is what extractors will actually be given, and the
    two differ exactly when `sampled` is true.
    """

    root: str
    files: tuple[FileEntry, ...]
    discovered: int
    sampled: bool
    skipped_large: int
    skipped_binary: int
    vcs_revision: str | None

    def by_language(self, language: str) -> tuple[FileEntry, ...]:
        """Every indexed file in one language, in index order."""
        return tuple(entry for entry in self.files if entry.language == language)

    def languages(self) -> tuple[str, ...]:
        """Every language present, sorted. Drives the per-language ladder."""
        return tuple(sorted({entry.language for entry in self.files if entry.language}))

    def disclosure(self) -> str | None:
        """The mandatory sampling sentence, or `None` when nothing was sampled.

        Returned rather than printed so the caller decides where it lands; every
        caller in this build puts it on the first screen, because a sampled run
        that reads as a complete one is the failure `03` §5.8 names.
        """
        if not self.sampled:
            return None
        return (
            f"{len(self.files)} of {self.discovered} files were examined: the tree is "
            f"above the {MAP_MAX_TREE_FILES} file sampling threshold, so counts and "
            "coverage below describe the sampled share and not the whole tree."
        )


#: Which of the detected languages carry **declarations** -- the `symbol` family
#: the degrade ladder is about (`01` F9.2). Markdown, JSON and a dotenv are
#: languages this index recognises and are not code: nothing declares a function
#: in a README.
#:
#: The distinction is load-bearing for the first screen. Without it the ladder
#: reports *"could not read markdown at grammar level"* on every run, and a
#: reader who learns to skip the degradations section is a reader who misses the
#: one that mattered -- which is exactly the failure `02` §9.1's ordering rule
#: exists to prevent.
CODE_LANGUAGES: Final[frozenset[str]] = frozenset(
    {
        "python",
        "typescript",
        "javascript",
        "java",
        "kotlin",
        "go",
        "ruby",
        "rust",
        "php",
        "csharp",
        "scala",
        "swift",
        "shell",
    }
)


def detect_language(path: str) -> str | None:
    """The language for a repo-relative path, or `None` when it is not known."""
    return _LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix.lower())


def is_code(language: str | None) -> bool:
    """Whether a language can carry declarations, and so a ladder degradation."""
    return language is not None and language in CODE_LANGUAGES


def git_blob_sha(data: bytes) -> str:
    """The git blob object id for `data` -- `02` §6's `<blob-sha>`.

    Git's own framing (``blob <len>\\0``) rather than a bare content digest, so
    a `provenance.source_ref` this build writes names the object a client can
    look up with `git cat-file` in their own repository. A digest only we can
    reproduce would be provenance nobody else can check.

    `usedforsecurity=False` because this is a content address, not a signature.
    The algorithm is git's and is not ours to choose.
    """
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


class _IgnoreRules:
    """The bounded `.gitignore` subset this build honours.

    Supported, and stated so nobody has to infer it from behaviour: comments,
    blank lines, ``!`` negation, a trailing ``/`` for directory-only, a leading
    ``/`` for root-anchored, and the ``*`` / ``?`` / ``**`` wildcards. Not
    supported: character classes and per-directory nested ignore files.

    **An unsupported construct is ignored, never approximated.** A pattern this
    class half-understands would exclude files a client expected indexed, and the
    resulting map would be short by an amount nobody could account for.
    """

    __slots__ = ("_rules",)

    def __init__(self, patterns: Iterable[str]) -> None:
        self._rules: list[tuple[re.Pattern[str], bool, bool]] = []
        for raw in patterns:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            directory_only = line.endswith("/")
            line = line.rstrip("/")
            if not line:
                continue
            self._rules.append((self._compile(line), negated, directory_only))

    @staticmethod
    def _compile(pattern: str) -> re.Pattern[str]:
        anchored = pattern.startswith("/")
        body = pattern.lstrip("/")
        parts: list[str] = []
        index = 0
        while index < len(body):
            char = body[index]
            if body.startswith("**/", index):
                parts.append("(?:.*/)?")
                index += 3  # const-sync: ok -- the length of the token "**/", a parser offset
            elif body.startswith("**", index):
                parts.append(".*")
                index += 2
            elif char == "*":
                parts.append("[^/]*")
                index += 1
            elif char == "?":
                parts.append("[^/]")
                index += 1
            else:
                parts.append(re.escape(char))
                index += 1
        head = "^" if anchored else "^(?:.*/)?"
        return re.compile(head + "".join(parts) + "(?:/.*)?$")

    def excluded(self, path: str, *, is_dir: bool) -> bool:
        """Whether `path` is excluded. Later rules win, as git's do."""
        verdict = False
        for rule, negated, directory_only in self._rules:
            if directory_only and not is_dir:
                continue
            if rule.match(path):
                verdict = not negated
        return verdict


def _read_ignore_rules(root: Path) -> _IgnoreRules:
    patterns: list[str] = []
    for name in IGNORE_FILES:
        candidate = root / name
        if candidate.is_file():
            patterns.extend(candidate.read_text(encoding="utf-8", errors="replace").splitlines())
    return _IgnoreRules(patterns)


def _keeps(path: str, ratio: float) -> bool:
    """The deterministic sampling decision for one repo-relative path."""
    digest = hashlib.blake2b(path.encode("utf-8"), digest_size=_SAMPLE_DIGEST_BYTES).digest()
    return int.from_bytes(digest, "big") < int(ratio * _SAMPLE_SPACE)


def _walk(root: Path, rules: _IgnoreRules) -> Iterator[tuple[str, Path]]:
    """Yield `(repo-relative posix path, absolute path)`. **One `os.walk`.**"""
    for directory, subdirectories, filenames in os.walk(root):
        here = Path(directory)
        relative_dir = here.relative_to(root).as_posix()
        # Pruned in place, which is what stops the walk descending into an
        # ignored tree at all rather than walking it and discarding the result.
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in _ALWAYS_SKIPPED_DIRS
            and not rules.excluded(
                name if relative_dir == "." else f"{relative_dir}/{name}", is_dir=True
            )
        )
        for name in sorted(filenames):
            relative = name if relative_dir == "." else f"{relative_dir}/{name}"
            if rules.excluded(relative, is_dir=False):
                continue
            yield relative, here / name


def _vcs_revision(root: Path) -> str | None:
    """The tree's commit sha, or `None` when the tree is not a checkout.

    `None` is a real answer rather than a failure: `02` §6 and B1-CR-36 make a
    non-checkout produce **no `provenance` row and a recorded gap**, because
    `SourceType` is closed and has no member for an artifact nobody committed.
    Read from `.git` as text; **no subprocess and no `git` binary**, so an
    unreadable or unusual checkout degrades to `None` rather than to an error.
    """
    head = root / ".git" / "HEAD"
    if not head.is_file():
        return None
    content = head.read_text(encoding="utf-8", errors="replace").strip()
    if not content.startswith("ref: "):
        return content or None
    reference = root / ".git" / content.removeprefix("ref: ")
    if reference.is_file():
        return reference.read_text(encoding="utf-8", errors="replace").strip() or None
    packed = root / ".git" / "packed-refs"
    if packed.is_file():
        target = content.removeprefix("ref: ")
        for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
            sha, _, name = line.partition(" ")
            if name.strip() == target:
                return sha
    return None


def build_index(
    root: Path | str,
    *,
    max_file_bytes: int = MAP_MAX_FILE_BYTES,
    max_tree_files: int = MAP_MAX_TREE_FILES,
    sampling_ratio: float = MAP_SAMPLING_MODE_RATIO,
) -> FileIndex:
    """Walk `root` once and return the index every extractor will read.

    Args:
        root: The client tree. Opened read-only; never imported or executed.
        max_file_bytes: Files above this are skipped and counted. Defaults to
            `MAP_MAX_FILE_BYTES`.
        max_tree_files: Above this many files the index samples. Defaults to
            `MAP_MAX_TREE_FILES`.
        sampling_ratio: The share kept in sampling mode. Defaults to
            `MAP_SAMPLING_MODE_RATIO`.

    Returns:
        A `FileIndex` whose `files` are sorted by path, so the order is a
        property of the tree and not of the filesystem's directory order.
    """
    base = Path(root)
    rules = _read_ignore_rules(base)

    discovered = 0
    skipped_large = 0
    skipped_binary = 0
    candidates: list[tuple[str, Path, int]] = []
    for relative, absolute in _walk(base, rules):
        try:
            size = absolute.stat().st_size
        except OSError:
            # A dangling symlink or a file that vanished mid-walk is not an
            # error: the tree is a client's and it is allowed to change under
            # us. It is simply not in the index.
            continue
        discovered += 1
        if size > max_file_bytes:
            skipped_large += 1
            continue
        candidates.append((relative, absolute, size))

    sampled = discovered > max_tree_files
    if sampled:
        candidates = [entry for entry in candidates if _keeps(entry[0], sampling_ratio)]

    entries: list[FileEntry] = []
    for relative, absolute, size in candidates:
        try:
            data = absolute.read_bytes()
        except OSError:
            continue
        is_binary = b"\0" in data[:MAP_BINARY_SNIFF_BYTES]
        if is_binary:
            skipped_binary += 1
            continue
        entries.append(
            FileEntry(
                path=relative,
                size=size,
                language=detect_language(relative),
                blob_sha=git_blob_sha(data),
                is_binary=False,
            )
        )

    return FileIndex(
        root=str(base),
        files=tuple(sorted(entries, key=lambda entry: entry.path)),
        discovered=discovered,
        sampled=sampled,
        skipped_large=skipped_large,
        skipped_binary=skipped_binary,
        vcs_revision=_vcs_revision(base),
    )


def read_text(index: FileIndex, entry: FileEntry) -> str:
    """Read one indexed file as text. **The only read path extractors use.**

    Decoding is lenient because a client tree is not ours to be strict about: a
    file with one bad byte is still a file whose routes we can read, and refusing
    it would turn an encoding accident into a coverage gap. Bytes are never
    executed, imported or evaluated.
    """
    return (Path(index.root) / entry.path).read_text(encoding="utf-8", errors="replace")


def paths(entries: Sequence[FileEntry]) -> tuple[str, ...]:
    """The repo-relative paths of `entries`, in order. A convenience for tests
    and for the sample disclosure, so neither reaches into the dataclass."""
    return tuple(entry.path for entry in entries)
