"""`SourceTree` -- the in-memory, read-only view every extractor sees.

Three properties, each of them a refusal:

**The tree is never written to.** There is no method here that creates,
modifies or deletes anything under the root. v6.1 §6 puts "any write to the
repository" out of scope for Build 1, and the way that is held is by the only
object extractors are handed not having the capability.

**Nothing in the tree is executed.** Files are read as bytes and decoded. No
import, no `eval`, no subprocess. This is what lets an FDE point `adopt map` at
a client repository they do not own and have not audited.

**Reads are bounded and cached.** `MAP_MAX_FILE_BYTES` caps any single file;
a file over it is skipped and *counted*, never silently dropped. Decoding uses
`errors="replace"` so one mis-encoded vendored file cannot fail a run.

The walk itself is `adopt_detect.walk_files` -- the programme's single walk, with
its bounds, `.gitignore` scope, symlink refusal and deterministic order. A second
walk here would be a second answer to "what is in this repository".
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath

from adopt_const import DETECT_MAX_FILES, MAP_MAX_FILE_BYTES
from adopt_detect import walk_files
from adopt_obs import AdoptError, ErrorCode

__all__ = ["SourceTree", "TreeFile"]


@dataclass(frozen=True, slots=True)
class TreeFile:
    """One readable file in the tree."""

    #: Repo-relative POSIX path. The identity key of a path-derived referent is
    #: built from this, so it must not carry a platform separator.
    path: str
    absolute: Path
    size: int

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.path).suffix

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name


class SourceTree:
    """A bounded, read-only, cached view of a repository.

    Construct with `SourceTree.scan(root)`; extractors receive one and read
    through it. Text is cached because several extractors legitimately read the
    same file -- the generic pack's config-key scan and the web pack's endpoint
    scan both open `settings.py` -- and reading it twice would double the I/O of
    the whole run for no benefit.
    """

    def __init__(self, root: Path, files: Sequence[TreeFile], *, oversized: Sequence[str]) -> None:
        self._root = root
        self._files = tuple(files)
        self._oversized = tuple(oversized)
        self._text_cache: dict[str, str | None] = {}

    @classmethod
    def scan(cls, root: Path | str, *, max_files: int = DETECT_MAX_FILES) -> "SourceTree":
        """Enumerate the tree once, refusing one that exceeds the walk bound.

        Raises:
            AdoptError: ``MAP_TREE_TOO_LARGE`` when the tree holds more files
                than `max_files`. A refusal rather than a truncation: a map that
                silently stopped somewhere would report a coverage number for a
                repository it had only partly seen, and every count downstream --
                including the recall floor -- would be quietly wrong.
        """
        resolved = Path(root).resolve()
        files: list[TreeFile] = []
        oversized: list[str] = []
        for relative, absolute in walk_files(resolved):
            if len(files) >= max_files:
                raise AdoptError(
                    ErrorCode.MAP_TREE_TOO_LARGE,
                    message=f"{resolved} holds more than {max_files} walkable files",
                    hint="Map a subdirectory, or exclude generated and vendored trees with "
                    ".gitignore. The bound is refused rather than truncated because a "
                    "partial walk reports a coverage number for a repository it only "
                    "partly saw.",
                )
            try:
                size = absolute.stat().st_size
            except OSError:
                # Vanished or unreadable between the walk and the stat. Skipped,
                # never fatal -- but it is not silent either: it is absent from
                # `files`, so it lands in the report's unmapped count.
                continue
            if size > MAP_MAX_FILE_BYTES:
                oversized.append(relative)
                continue
            files.append(TreeFile(path=relative, absolute=absolute, size=size))
        return cls(resolved, files, oversized=oversized)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def files(self) -> tuple[TreeFile, ...]:
        """Every readable file, in the walk's deterministic order."""
        return self._files

    @property
    def oversized(self) -> tuple[str, ...]:
        """Files skipped for exceeding `MAP_MAX_FILE_BYTES`, by path."""
        return self._oversized

    @cached_property
    def _by_path(self) -> dict[str, TreeFile]:
        return {entry.path: entry for entry in self._files}

    def get(self, path: str) -> TreeFile | None:
        return self._by_path.get(path)

    def exists(self, path: str) -> bool:
        return path in self._by_path

    def text(self, entry: TreeFile | str) -> str | None:
        """The file's decoded text, or `None` if it could not be read.

        `None` rather than an exception: one unreadable file in a client
        checkout is an ordinary condition, and an extractor that had to guard
        every read with a try/except would eventually forget one and take the
        whole run down with it.
        """
        path = entry if isinstance(entry, str) else entry.path
        if path in self._text_cache:
            return self._text_cache[path]
        found = self._by_path.get(path)
        text: str | None = None
        if found is not None:
            try:
                with found.absolute.open("rb") as handle:
                    raw = handle.read(MAP_MAX_FILE_BYTES)
                text = raw.decode("utf-8", errors="replace")
            except OSError:
                text = None
        self._text_cache[path] = text
        return text

    def iter_suffix(self, *suffixes: str) -> Iterator[TreeFile]:
        """Files with any of these suffixes, in walk order."""
        wanted = frozenset(suffixes)
        for entry in self._files:
            if entry.suffix in wanted:
                yield entry

    def iter_named(self, *names: str) -> Iterator[TreeFile]:
        """Files with any of these exact basenames, in walk order."""
        wanted = frozenset(names)
        for entry in self._files:
            if entry.name in wanted:
                yield entry

    def under(self, prefix: str) -> Iterator[TreeFile]:
        """Files beneath a repo-relative directory prefix, in walk order."""
        normalized = prefix.rstrip("/") + "/"
        for entry in self._files:
            if entry.path.startswith(normalized):
                yield entry
