"""Archetype detection: a bounded, deterministic walk over a file tree.

PRD F10.1-F10.3 and implementation spec §4.11. Four properties are load-bearing
and each is a refusal rather than a preference:

**No code executes in the target tree.** Nothing here imports, evaluates or runs
anything it finds. Files are opened for reading, at most
`DETECT_MAX_SNIFF_BYTES` of each, and matched against literal substrings. This
is what lets an FDE point the tool at a client repository they do not own.

**No network.** Detection is pure filesystem. `01` N10 makes that measurable
rather than asserted; `tests/property/test_offline.py` is the instrument.

**No model call in this module.** `04` §4 is explicit: the deterministic path
runs to completion, and below `DETECT_CONFIDENCE_MIN` the answer is *ambiguous
with ranked scores*, never a guess. The model-backed disambiguation pass lives in
`adopt_detect.disambiguate`, is reached only with `ADOPT_FEATURE_AGENT_DISAMBIGUATION`
on **and** a runner handed in, and **there is still no call site for it here** --
`detect()` cannot reach a model even transitively, which is what makes steps 1-3
of `04` §4 model-free by construction rather than by intent.

`bounded_listing` is the one thing this module lends that pass: the evidence a
proposal is allowed to see includes a directory listing, and it must be *this*
walk's listing -- same bounds, same `.gitignore` scope, same symlink refusal, same
deterministic order. A second walk written next to the prompt would be a second
answer to "what is in this tree", and the one that drifted would be the one that
decided what left the environment.

**Byte-identical across runs and machines** (`01` N2, N15). Every ordering in
this module is explicit: entries are sorted before the walk, rules are scored in
declaration order, and the archetype ranking breaks ties by the fixed
`ARCHETYPES` order. Nothing depends on filesystem iteration order, on a `set`'s
layout, or on a dict built from either.

**The walk is bounded three ways**, and hitting a bound is reported rather than
silently truncating the evidence: `DETECT_MAX_DEPTH` on nesting,
`DETECT_MAX_FILES` on files considered, `DETECT_MAX_SNIFF_BYTES` on bytes read
per file. A client monorepo is allowed to be enormous; it is not allowed to make
detection unbounded.
"""

import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final

from adopt_const import DETECT_CONFIDENCE_MIN, DETECT_MAX_DEPTH, DETECT_MAX_FILES
from adopt_const import DETECT_MAX_SNIFF_BYTES as _SNIFF_BYTES
from adopt_detect.gitignore import GitignoreFilter
from adopt_detect.rules import ARCHETYPES, ArchetypeRules, load_rule_sets, needs_content
from adopt_model._enums import Archetype
from adopt_obs import AdoptError, ErrorCode

__all__ = ["DetectionResult", "RuleHit", "bounded_listing", "detect", "walk_files"]

#: The flag whose name the ambiguity report prints. It is named here, not
#: implemented here: `04` §4 step 4 is a later sprint's, and the re-run hint has
#: to name the flag a caller would actually set.
DISAMBIGUATION_FLAG: Final[str] = "ADOPT_FEATURE_AGENT_DISAMBIGUATION"

#: Directories never descended into. `.git` is the one that matters for
#: correctness rather than speed: its object store contains compressed copies of
#: everything, and a `contains` rule would fire on packfile bytes that no longer
#: reflect the working tree.
#: `.adopt` is this tool's own working directory -- the store, the runtime annex
#: and whatever else it writes beside the tree it is looking at. Walking it makes
#: the tool part of the system it is describing: detection would score an
#: archetype partly from our own files, and `adopt map` would mint identities for
#: our own database. Both are the same error, which is why the exclusion lives
#: with the walk rather than in either caller.
_ALWAYS_SKIPPED: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".adopt",
    }
)


@dataclass(frozen=True, slots=True)
class RuleHit:
    """One rule that fired, and the first path that fired it."""

    archetype: Archetype
    rule_id: str
    path: str
    weight: float
    why: str


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """What detection concluded, and everything it concluded it from.

    `archetype` is `None` exactly when `ambiguous` is true. The two are kept as
    separate fields rather than one optional because callers branch on ambiguity
    and reading `archetype is None` as "ambiguous" invites the other reading,
    "not yet computed".
    """

    root: str
    archetype: Archetype | None
    confidence: float
    scores: dict[Archetype, float]
    rules_fired: tuple[RuleHit, ...]
    files_considered: int
    truncated: bool
    ambiguous: bool = field(default=False)

    def ranked(self) -> tuple[tuple[Archetype, float], ...]:
        """Archetypes by score descending, ties broken by declaration order."""
        return tuple(
            sorted(self.scores.items(), key=lambda pair: (-pair[1], ARCHETYPES.index(pair[0])))
        )


def _sorted_entries(directory: Path) -> list[os.DirEntry[str]]:
    """Directory entries in one fixed order, whatever the filesystem returns.

    `scandir` order is filesystem- and machine-dependent. Sorting here is what
    makes `rules_fired` -- which records the *first* path that fired each rule --
    the same on a developer laptop and on the reference runner.
    """
    with os.scandir(directory) as entries:
        return sorted(entries, key=lambda entry: entry.name)


def _is_within(candidate: Path, root: Path) -> bool:
    """Whether a resolved path is inside a resolved root.

    The symlink check. A client tree may legitimately contain a symlink to
    somewhere outside itself, and following one would let detection read files
    the operator never pointed us at -- and, under a `contains` rule, let content
    outside the tree change the archetype. Such a link is skipped, not an error:
    refusing the whole tree over one stray link would make the tool unusable on
    real repositories.
    """
    return candidate == root or root in candidate.parents


def _walk(root: Path, ignore: GitignoreFilter) -> Iterator[tuple[str, Path, bool]]:
    """Yield `(relative_posix_path, absolute_path, is_file)` breadth-first.

    Breadth-first so that a bound is hit at the *bottom* of the tree rather than
    inside whichever subdirectory happened to sort first -- a depth-first walk
    that stops at `DETECT_MAX_FILES` on a monorepo would see all of one package
    and none of the others, which is how a bound turns into a wrong answer
    instead of a partial one.
    """
    queue: list[tuple[Path, str, int]] = [(root, "", 0)]
    while queue:
        directory, prefix, depth = queue.pop(0)
        try:
            entries = _sorted_entries(directory)
        except OSError:
            # Unreadable directory: skipped, never fatal. A client checkout with
            # one permission-denied path is still a tree we can classify.
            continue
        for entry in entries:
            relative = f"{prefix}{entry.name}"
            absolute = Path(entry.path)
            if entry.is_symlink() and not _is_within(absolute.resolve(), root):
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name in _ALWAYS_SKIPPED or ignore.is_ignored(relative, is_dir=True):
                    continue
                if depth < DETECT_MAX_DEPTH:
                    queue.append((absolute, f"{relative}/", depth + 1))
                continue
            if ignore.is_ignored(relative, is_dir=False):
                continue
            yield relative, absolute, True


def walk_files(root: Path | str) -> Iterator[tuple[str, Path]]:
    """Every file this programme considers in a tree, as `(relative, absolute)`.

    **This is the one walk.** `detect`, `bounded_listing` and `adopt map` all
    reach a tree through it, so "what is in this repository" has a single answer
    with a single set of bounds, one `.gitignore` scope, one symlink rule and one
    deterministic order. A second walk written beside a new consumer would be a
    second answer, and the one that drifted would be the one deciding what the
    map contains.

    It opens nothing: callers that need bytes read them under their own bound.
    Depth is bounded here by `DETECT_MAX_DEPTH`; a **file-count** bound belongs
    to the caller, because what to do when a tree is too large differs -- the
    listing truncates and says so, the map refuses with `MAP_TREE_TOO_LARGE`.
    """
    resolved = Path(root).resolve()
    ignore = GitignoreFilter.for_tree(resolved)
    for relative, absolute, _is_file in _walk(resolved, ignore):
        yield relative, absolute


def bounded_listing(root: Path | str, *, limit: int) -> tuple[tuple[str, ...], bool]:
    """The first `limit` paths this walk would consider. Returns `(paths, truncated)`.

    **Paths only.** No file is opened, so nothing here can carry file contents --
    which is the `04` §4 step 4 privacy invariant, held by the function not having
    the capability rather than by its caller remembering not to use it.

    Truncation is reported rather than silent, for the reason `DetectionResult`
    reports it: a proposal reasoning over a listing that stopped somewhere is
    reasoning over less evidence than it appears to have, and a model told the
    listing was truncated can say so in its confidence.
    """
    paths: list[str] = []
    for relative, _absolute in walk_files(root):
        if len(paths) >= limit:
            return tuple(paths), True
        paths.append(relative)
    return tuple(paths), False


def _head(path: Path) -> bytes | None:
    """The first `DETECT_MAX_SNIFF_BYTES`, or `None` if unreadable."""
    try:
        with path.open("rb") as handle:
            return handle.read(_SNIFF_BYTES)
    except OSError:
        return None


def detect(
    root: Path | str, *, rule_sets: Sequence[ArchetypeRules] | None = None
) -> DetectionResult:
    """Classify a file tree into exactly one archetype, or report ambiguity.

    Args:
        root: The tree to classify. Never read as code, never executed.
        rule_sets: Injected rule sets, for tests that need a small closed corpus.
            Defaults to the packaged `rules/*.yaml`.

    Returns:
        A `DetectionResult`. When the winning score is below
        `DETECT_CONFIDENCE_MIN` the result is `ambiguous` with `archetype` of
        `None` and the full ranked scores -- **never a best guess**. PRD F10.3
        makes that a product statement: a wrong archetype is not a wrong answer
        but a different set of extractors, which nothing downstream can detect.

    Raises:
        AdoptError: ``DETECT_AMBIGUOUS`` is *not* raised here -- ambiguity is a
            result, and the CLI maps it to exit `2`. This function raises only
            ``MANIFEST_INVALID``, from rule loading, and
            ``ADOPT_CONFIG_UNRESOLVED`` when `root` is not a directory.
    """
    tree = Path(root)
    if not tree.is_dir():
        raise AdoptError(
            ErrorCode.ADOPT_CONFIG_UNRESOLVED,
            message=f"{tree} is not a directory",
            hint="Detection classifies a file tree. Point it at a checkout root.",
        )
    resolved = tree.resolve()
    sets = tuple(rule_sets) if rule_sets is not None else load_rule_sets()
    read_content = needs_content(sets)
    ignore = GitignoreFilter.for_tree(resolved)

    hits: dict[tuple[Archetype, str], RuleHit] = {}
    files_considered = 0
    truncated = False

    for relative, absolute, _ in _walk(resolved, ignore):
        if files_considered >= DETECT_MAX_FILES:
            truncated = True
            break
        files_considered += 1
        head: bytes | None = None
        head_read = False
        for rule_set in sets:
            for rule in rule_set.rules:
                key = (rule_set.archetype, rule.id)
                if key in hits:
                    continue
                if rule.contains is not None:
                    if not read_content:  # pragma: no cover -- defensive
                        continue
                    if not head_read:
                        head = _head(absolute)
                        head_read = True
                if rule.matches(relative, head):
                    hits[key] = RuleHit(
                        archetype=rule_set.archetype,
                        rule_id=rule.id,
                        path=relative,
                        weight=rule.weight,
                        why=rule.why,
                    )

    scores = _score(sets, hits)
    ranked = sorted(scores.items(), key=lambda pair: (-pair[1], ARCHETYPES.index(pair[0])))
    winner, confidence = ranked[0]
    ambiguous = confidence < DETECT_CONFIDENCE_MIN

    return DetectionResult(
        root=PurePosixPath(resolved.as_posix()).as_posix(),
        archetype=None if ambiguous else winner,
        confidence=confidence,
        scores=scores,
        rules_fired=tuple(
            sorted(hits.values(), key=lambda hit: (ARCHETYPES.index(hit.archetype), hit.rule_id))
        ),
        files_considered=files_considered,
        truncated=truncated,
        ambiguous=ambiguous,
    )


def _score(
    rule_sets: Sequence[ArchetypeRules], hits: dict[tuple[Archetype, str], RuleHit]
) -> dict[Archetype, float]:
    """Each archetype's **share of the total matched weight**, rounded.

    **The denominator is the evidence found, not the evidence available**, and
    that is the whole design. Dividing by an archetype's own total weight would
    ask "how many of the web rules did this tree fire?" -- and a Django service
    fires none of the Rails, Go or Express rules, so a perfectly clear web system
    would score around 0.3 and be reported ambiguous. The question worth asking
    is "how much of what we found points one way?", which is what
    `DETECT_CONFIDENCE_MIN = 0.70` reads as in prose: seventy per cent of the
    evidence agrees.

    Two consequences follow, both wanted. Scores sum to 1, so the ranked list in
    an ambiguity report is a genuine distribution rather than five unrelated
    ratios. And a tree that fires **nothing** scores 0.0 everywhere and is
    ambiguous -- the correct answer for a directory with no signals in it, and
    one that costs no special case downstream.

    **Rounded deliberately.** The score crosses a threshold, is printed as JSON
    and is compared byte-for-byte by `01` N2's determinism property, so a value
    whose last bits depend on summation order would make that property fail for a
    reason that has nothing to do with detection. Six places is far finer than
    `DETECT_CONFIDENCE_MIN`'s two and removes the question.
    """
    matched: dict[Archetype, float] = {
        rule_set.archetype: sum(
            hit.weight for key, hit in hits.items() if key[0] == rule_set.archetype
        )
        for rule_set in rule_sets
    }
    total = sum(matched.values())
    if total == 0:
        return dict.fromkeys(matched, 0.0)
    # const-sync: ok -- 6 is JSON rendering precision for a ratio in [0,1], not a
    # behavioural threshold; `DETECT_CONFIDENCE_MIN` is the threshold.
    return {archetype: round(weight / total, 6) for archetype, weight in matched.items()}
