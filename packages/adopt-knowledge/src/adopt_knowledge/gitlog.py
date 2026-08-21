"""The one place a `git` subprocess is spawned, and the only thing that parses it.

Plan decision D1: harvest reads history through the **system git binary** rather
than a library. The research doc's own first stage is "test the
no-new-dependency baseline", and this is that baseline holding -- everything
`adopt harvest` needs is what `git log`'s plumbing already emits. The target
tree *is* a git repository by construction (`--since <ref>` is meaningless
otherwise), the Build 2 demo itself shells to `git mv`, and every CI runner
ships git. Zero Python dependencies and zero licence surface in the binary.

**Confined on purpose.** If a later build needs a no-git environment, or if
subprocess parsing proves brittle across versions, Dulwich lands behind this
module's interface (`read_commits`, `head_sha`) and nothing above it changes.

**Three parsing decisions, each one a defect this module is written not to have:**

* **`\\x1e`/`\\x1f` framing, not newlines.** A commit message contains newlines
  and a `--name-only` file list is newline-separated, so any newline-framed
  parse has to guess where the message stopped. The two ASCII separators cannot
  appear in a commit message written by a human tool.
* **Bytes in, decoded here, newlines normalised here.** `text=True` would hand
  the platform's newline translation a say in what the digest of a commit
  message is; a `\\r\\n` checkout on Windows must mine the same candidates as a
  `\\n` checkout on Linux. That is the CRLF lesson the 0.3.1 release paid for,
  applied where the next reader meets it.
* **`core.quotepath=false` and `--encoding=UTF-8`.** Otherwise a non-ASCII path
  arrives octal-escaped and a non-UTF-8 commit message arrives in its original
  encoding -- both silently, and both as text that would go into the store.
"""

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from adopt_obs import AdoptError, ErrorCode, get_logger

__all__ = [
    "Commit",
    "head_sha",
    "read_commits",
]

_log = get_logger("adopt_knowledge")

#: ASCII record and unit separators. Chosen because git will emit them verbatim
#: from a `--format` string and nothing else in the output can produce them.
_RECORD: Final[str] = "\x1e"
_UNIT: Final[str] = "\x1f"

#: `%H` sha · `%P` parents · `%aI` author date, strict ISO · `%s` subject ·
#: `%b` body **without** the subject. `%b` rather than `%B` so "did the author
#: write a rationale beyond the headline" is a question about a field rather
#: than about string surgery on one.
_FORMAT: Final[str] = f"{_RECORD}%H{_UNIT}%P{_UNIT}%aI{_UNIT}%s{_UNIT}%b{_UNIT}"

#: Flags that make the output a function of the repository rather than of the
#: machine reading it. `-c` settings beat any `.gitconfig` the operator has.
_STABLE: Final[tuple[str, ...]] = (
    "-c",
    "core.quotepath=false",
    "-c",
    "log.showSignature=false",
)

#: How much of git's own stderr travels in a refusal's hint. Enough to carry
#: git's sentence, short enough that a person reads it. It happens to share a
#: value with `CLI_COLD_START_MS` and has nothing to do with it: promoting a
#: message-truncation length to a shared tunable would tie an error string to a
#: startup budget, where changing one silently changes the other.
# const-sync: ok -- a message-truncation length, not a tunable. See above.
_HINT_CHARS: Final[int] = 400


@dataclass(frozen=True, slots=True)
class Commit:
    """One commit, as harvest needs to see it.

    Deliberately not "as git sees it": there is no tree, no diff and no author
    identity here. Harvest mines *decisions*, and a decision is a subject, a
    rationale, a shape (merge or not) and the files it landed on.
    """

    sha: str
    parents: tuple[str, ...]
    #: Strict ISO-8601 with offset, straight from `%aI`. Kept as text: it is
    #: evidence to be shown, never a value to compute with.
    authored_at: str
    subject: str
    body: str
    #: POSIX-relative paths the commit touched, sorted. **Empty for a merge**,
    #: because `git log --name-only` emits no diff for a commit with two
    #: parents -- which is correct and worth stating, since it means a merge
    #: candidate binds to nothing.
    files: tuple[str, ...]

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


def _binary() -> str:
    """The `git` executable, resolved once and by absolute path.

    `shutil.which` first, on `adopt_schema.lint.manifest_at_ref`'s precedent: an
    absolute argv[0] leaves no room for PATH to mean something different halfway
    through a run, and it is what lets the subprocess call be read as fixed argv
    rather than as untrusted input.
    """
    git = shutil.which("git")
    if git is None:
        raise AdoptError(
            ErrorCode.HARVEST_NOT_A_GIT_REPO,
            message="git is not on PATH, so there is no local history to mine",
            hint="Harvest reads history that is present on this machine (v6.1 §6 F7). "
            "Install `git`; there is no flag that mines a repository which is not "
            "there.",
        )
    return git


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """One git invocation. Bytes out; nothing here trusts the locale.

    Raises:
        AdoptError: ``HARVEST_NOT_A_GIT_REPO`` when git cannot be run at all --
            no binary, or a `cwd` that is not a directory. Raised here rather
            than at the call site because to every caller above those are the
            same condition and have the same one-sentence answer.
    """
    try:
        return subprocess.run(  # noqa: S603 -- fixed argv, git resolved by shutil.which
            [_binary(), *_STABLE, *args],
            cwd=str(root),
            check=False,
            capture_output=True,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError) as error:
        raise AdoptError(
            ErrorCode.HARVEST_NOT_A_GIT_REPO,
            message=f"git could not be run in {str(root)!r}",
            hint="Point the command at a directory that exists and is a checkout. "
            "Harvest mines local history and nothing else.",
        ) from error


def _text(raw: bytes) -> str:
    """Decode and normalise newlines, in that order and only here.

    `errors="replace"` rather than a refusal: a commit message in an encoding
    git could not convert is still evidence about a decision, and losing the
    whole range over one 2009 commit would be the wrong trade. The replacement
    characters are visible in review, which is where a human can judge them.
    """
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _require_repo(root: Path) -> None:
    probe = _run(root, "rev-parse", "--git-dir")
    if probe.returncode != 0:
        raise AdoptError(
            ErrorCode.HARVEST_NOT_A_GIT_REPO,
            message=f"{str(root)!r} is not a git repository",
            hint="Run `adopt harvest` inside the checkout whose history you want mined, "
            "or pass its path. Harvest is offline by construction (v6.1 §6 F7) and "
            "reads nothing but local history.",
        )


def _require_ref(root: Path, ref: str) -> None:
    """Resolve `--since` before the range is built, so the error names the ref.

    `git log badref..HEAD` fails with git's own "ambiguous argument" prose,
    which names a range the operator never typed. Verifying first means the
    refusal names exactly the word they did type.
    """
    probe = _run(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if probe.returncode != 0:
        raise AdoptError(
            ErrorCode.HARVEST_RANGE_UNKNOWN,
            message=f"{ref!r} does not name a commit in this repository",
            hint="Pass a tag, branch or sha that exists locally -- `git tag -l` lists "
            "them. A tag that exists on the forge but was never fetched is not "
            "local history, and harvest reads nothing else.",
        )


def head_sha(root: Path) -> str:
    """The commit `--since` is mined *to*. Part of the review batch's key.

    Raises:
        AdoptError: ``HARVEST_NOT_A_GIT_REPO`` when there is no repository, and
            ``HARVEST_RANGE_UNKNOWN`` when it has no commits at all -- an
            initialised repository with nothing in it has no HEAD, and mining a
            range that ends nowhere would otherwise report an empty success.
    """
    _require_repo(root)
    _require_ref(root, "HEAD")
    resolved = _run(root, "rev-parse", "HEAD")
    return _text(resolved.stdout).strip()


def read_commits(root: Path, *, since: str) -> tuple[Commit, ...]:
    """Every commit reachable from HEAD but not from `since`, newest first.

    The range is the operator's bound and the only one: `<since>..HEAD` is
    finite by construction, which is why harvest introduces no walk tunable
    (v6.1 §8 names none for Build 2).

    Raises:
        AdoptError: ``HARVEST_NOT_A_GIT_REPO`` / ``HARVEST_RANGE_UNKNOWN``.
    """
    _require_repo(root)
    _require_ref(root, since)

    completed = _run(
        root,
        "log",
        f"{since}..HEAD",
        "--name-only",
        "--no-color",
        "--encoding=UTF-8",
        f"--format={_FORMAT}",
    )
    if completed.returncode != 0:
        # Reached only when the range resolves and the walk still fails -- a
        # corrupt object database, say. The repository code is the honest one:
        # what harvest cannot do is read this repository's history.
        raise AdoptError(
            ErrorCode.HARVEST_NOT_A_GIT_REPO,
            message=f"git log {since}..HEAD failed in {str(root)!r}",
            hint=f"git said: {_text(completed.stderr).strip()[:_HINT_CHARS] or '(nothing)'}",
        )

    commits = _parse(_text(completed.stdout))
    _log.info("harvest.history_read", commits=len(commits), since=since)
    return commits


def _parse(output: str) -> tuple[Commit, ...]:
    """`git log` output -> commits. Pure, so the fixture tests can drive it."""
    return tuple(
        commit
        for chunk in output.split(_RECORD)
        if chunk.strip()
        for commit in (_parse_one(chunk),)
        if commit is not None
    )


def _parse_one(chunk: str) -> Commit | None:
    """One record, or `None` when it is not one.

    The body is rejoined from every field between the subject and the file
    list rather than taken as `fields[4]`. A commit message *can* contain a
    `\\x1f` -- nothing stops a script writing one -- and the difference between
    the two readings is a body silently truncated at that byte versus a body
    carried whole. The file list is `fields[-1]` either way, because a path
    containing a unit separator is not a path any tool would produce.
    """
    fields = chunk.split(_UNIT)
    if len(fields) < 6:
        return None
    sha, parents, authored_at, subject = (field.strip() for field in fields[:4])
    body = _UNIT.join(fields[4:-1])
    files = tuple(sorted({line.strip() for line in fields[-1].splitlines() if line.strip()}))
    return Commit(
        sha=sha,
        parents=tuple(parent for parent in parents.split() if parent),
        authored_at=authored_at,
        subject=subject,
        body=body.strip("\n"),
        files=files,
    )


def parse_for_test(raw: bytes) -> Sequence[Commit]:
    """Decode, normalise and parse -- the whole reader, minus the subprocess.

    **Takes bytes, exactly as `read_commits` receives them.** A driver that took
    `str` would skip `_text`, and the newline normalisation this module exists
    to guarantee lives there: the test would then assert against a step it had
    quietly stepped over. The fixture-repo tests above are still the ones that
    matter -- real git output is what this module survives -- but a parser whose
    only driver is a subprocess can only be shown its failure modes by building
    a repository that has them, and no repository on this machine emits CRLF.
    """
    return _parse(_text(raw))
