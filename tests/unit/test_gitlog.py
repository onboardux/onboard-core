"""`adopt_knowledge.gitlog` against **real git**, plus the parser on its own.

The fixture repositories here are built by the tests -- a handful of files and
scripted commits -- rather than mocked, because this module's entire job is
surviving what `git log` actually emits. A mock of git output is a mock of my
own belief about git output, and Build 1's lesson (S1.1, four defects that every
hand-written fixture passed) is that the belief is the thing that is wrong.

**These tests need `git` on PATH and skip without it.** That is honest on a
machine with no git and never the state of CI: every runner ships git, and the
`unit` job would have to lose the binary for these to vanish silently.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from adopt_knowledge.gitlog import Commit, head_sha, parse_for_test, read_commits

from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit

_GIT = shutil.which("git")


def _git(root: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args], cwd=str(root), check=False, capture_output=True, text=True
    )
    assert completed.returncode == 0, f"git {' '.join(args)}: {completed.stderr}"


def _repo(root: Path) -> Path:
    """An initialised repository with a fixed identity and no signing.

    The identity is repo-local so the suite never depends on the developer's
    `~/.gitconfig`, and `commit.gpgsign=false` because a machine that signs by
    default would otherwise fail every commit here with no tty to sign on.
    """
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    return root


def _commit(root: Path, message: str, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", message)


needs_git = pytest.mark.skipif(_GIT is None, reason="git is not on PATH")


@pytest.fixture
def history(tmp_path: Path) -> Path:
    """A repository with a tag, then three commits of distinguishable shapes."""
    root = _repo(tmp_path / "repo")
    _commit(root, "Initial commit", {"README.md": "# Fixture\n"})
    _git(root, "tag", "v0")
    _commit(
        root,
        "Add the refund endpoint\n\nWe hold refunds for manual approval because the\n"
        "provider settles asynchronously.",
        {"src/payments/refund.ts": "export const refund = () => {};\n"},
    )
    _commit(root, "Bump the pinned dependency", {"pyproject.toml": '[project]\nname = "x"\n'})
    return root


@needs_git
def test_a_range_yields_its_commits_newest_first_with_the_files_they_touched(
    history: Path,
) -> None:
    """The whole of what harvest reads, read from real git.

    *Fails when* the framing, the field order or the `--name-only` association
    breaks -- a commit loses its files, gains its neighbour's, or the range
    boundary is off by one. *Matters because* every candidate harvest builds is
    a projection of this tuple: the sha becomes the evidence key, the files
    become the bindings, and a file attached to the wrong commit binds a
    decision to code it never touched. *No other instrument catches it because*
    the parser is pure and passes on any input shaped like git's -- only real
    `git log` output can show that the shape is the one git actually emits.
    """
    commits = read_commits(history, since="v0")

    assert [commit.subject for commit in commits] == [
        "Bump the pinned dependency",
        "Add the refund endpoint",
    ], "newest first, and the tagged commit is excluded from its own range"
    assert commits[0].files == ("pyproject.toml",)
    assert commits[1].files == ("src/payments/refund.ts",)
    assert all(len(commit.sha) == 40 for commit in commits)
    assert commits[1].body.startswith("We hold refunds for manual approval")
    assert commits[0].authored_at.startswith("20"), commits[0].authored_at


@needs_git
def test_a_body_carrying_blank_lines_does_not_swallow_the_file_list(history: Path) -> None:
    """The framing decision, exercised where a newline parse would break.

    *Fails when* `gitlog` frames on newlines again: a message with a blank line
    in it ends the record early, and every path after it is read as more
    message. *Matters because* the files are what bind a candidate to an
    identity -- a candidate that silently loses them is one that can never
    contribute coverage, and it looks exactly like a commit that touched
    nothing. *No other instrument catches it because* single-line commit
    messages parse identically under both framings, and most commits are
    single-line.
    """
    _commit(
        history,
        "Rework the approval step\n\nFirst paragraph.\n\nSecond paragraph, after a blank line.\n",
        {"src/payments/approve.ts": "export const approve = () => {};\n"},
    )

    newest = read_commits(history, since="v0")[0]

    assert newest.subject == "Rework the approval step"
    assert "Second paragraph, after a blank line." in newest.body
    assert newest.files == ("src/payments/approve.ts",)
    assert "src/payments/approve.ts" not in newest.body


@needs_git
def test_a_merge_is_recorded_as_one_and_reports_no_files(tmp_path: Path) -> None:
    """A merge's shape, from git rather than from an assumption about git.

    *Fails when* `parents` stops being parsed, so merge commits are
    indistinguishable from ordinary ones. *Matters because* "this was an
    integration decision" is one of the five signals harvest mines, and it is
    the only one that is a property of the commit's *shape* rather than its
    text. *No other instrument catches it because* a merge's message often
    looks ordinary, so nothing else in the record says what it is -- and the
    empty file list, which is git's own behaviour and not ours, is exactly what
    would otherwise be misread as a defect later.
    """
    root = _repo(tmp_path / "merged")
    _commit(root, "Initial commit", {"README.md": "# Fixture\n"})
    _git(root, "tag", "v0")
    _git(root, "checkout", "--quiet", "-b", "feature")
    _commit(root, "Feature work", {"feature.txt": "feature\n"})
    _git(root, "checkout", "--quiet", "main")
    _commit(root, "Main work", {"main.txt": "main\n"})
    _git(root, "merge", "--quiet", "--no-ff", "-m", "Merge branch 'feature'", "feature")

    commits = read_commits(root, since="v0")
    merges = [commit for commit in commits if commit.is_merge]

    assert len(merges) == 1, [commit.subject for commit in commits]
    assert len(merges[0].parents) == 2
    assert merges[0].files == (), (
        "git emits no diff for a merge under --name-only; a merge candidate "
        "therefore binds to nothing, and that is the honest outcome rather than a bug"
    )


@needs_git
def test_a_tree_that_is_not_a_repository_is_refused_by_code(tmp_path: Path) -> None:
    """*Fails when* harvest treats a non-repository as an empty history.

    *Matters because* an empty success and a repository that is not there are
    the same output -- zero candidates -- and the operator would read "nothing
    to mine here" when the truth is "you are in the wrong directory". *No other
    instrument catches it because* every other test in this file runs inside a
    repository, where the refusal path is never taken.
    """
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    with pytest.raises(AdoptError) as raised:
        read_commits(plain, since="v0")

    assert raised.value.code is ErrorCode.HARVEST_NOT_A_GIT_REPO


@needs_git
def test_an_unknown_ref_is_refused_with_its_own_code(history: Path) -> None:
    """*Fails when* a mistyped `--since` is reported as an absent repository.

    *Matters because* the two have different fixes -- `git tag -l` versus "run
    this in a checkout" -- and one code covering both sends half the operators
    who hit it to the wrong place. It is also the likelier of the two: a tag
    that exists on the forge but was never fetched is not local history.
    *No other instrument catches it because* both refusals exit `2` with a
    message, so only the code distinguishes them.
    """
    with pytest.raises(AdoptError) as raised:
        read_commits(history, since="v9.9.9-never-tagged")

    assert raised.value.code is ErrorCode.HARVEST_RANGE_UNKNOWN
    assert "v9.9.9-never-tagged" in str(raised.value)


@needs_git
def test_head_is_resolvable_and_is_what_the_range_ends_at(history: Path) -> None:
    """*Fails when* `head_sha` stops naming the commit the range was mined to.

    *Matters because* the review batch is keyed `harvest:<since>..<head>`, and a
    key that does not name the range it covers makes two different harvests
    indistinguishable in the queue. *No other instrument catches it because* the
    key is a string: a wrong one is still well-formed, still unique enough to
    open a batch, and still wrong.
    """
    head = head_sha(history)

    assert len(head) == 40
    assert read_commits(history, since="v0")[0].sha == head


def test_crlf_and_a_separator_in_a_body_survive_the_parser() -> None:
    """The two things the parser is written to do, driven directly.

    *Fails when* CRLF reaches a candidate body, or a body containing a unit
    separator is truncated at it. *Matters because* the body becomes
    `knowledge_revision.body_md` and its digest: a Windows checkout must mine
    byte-identical knowledge to a Linux one, or every re-harvest across
    platforms looks like new decisions. *No other instrument catches it
    because* a fixture repository created on this machine emits this machine's
    newlines, so the CRLF path is unreachable from the tests above -- and no
    commit anyone would author contains a `\\x1f` to drive the second case.
    """
    record = (
        "\x1e" + "a" * 40 + "\x1f" + "b" * 40 + "\x1f2026-08-21T09:00:00+00:00\x1f"
        "Subject line\x1fFirst\r\nSecond\x1fstill the body\x1fsrc/a.py\nsrc/b.py\n"
    )

    parsed = parse_for_test(record.encode("utf-8"))

    assert len(parsed) == 1
    commit = parsed[0]
    assert "\r" not in commit.body
    assert commit.body == "First\nSecond\x1fstill the body"
    assert commit.files == ("src/a.py", "src/b.py")


def test_a_record_that_is_not_one_is_dropped_rather_than_half_parsed() -> None:
    """*Fails when* a truncated record yields a `Commit` with empty fields.

    *Matters because* a half-parsed record becomes a candidate with no sha --
    and the sha is the evidence key idempotence rests on, so every re-harvest
    would create it again. *No other instrument catches it because* a `Commit`
    with empty strings is a perfectly valid dataclass and travels all the way to
    the store before anything notices.
    """
    assert parse_for_test(b"\x1edeadbeef\x1f\x1fnot enough fields") == ()
    assert parse_for_test(b"") == ()
    assert parse_for_test(b"   \n  ") == ()


def test_a_commit_is_frozen_so_a_miner_cannot_edit_the_evidence() -> None:
    """*Fails when* `Commit` stops being immutable.

    *Matters because* the commit record is the evidence a candidate cites; a
    miner that could rewrite a subject before it becomes a title would produce
    knowledge whose provenance says it was observed in an artifact that never
    said it. *No other instrument catches it because* the mutation would be
    invisible -- the candidate is well-formed either way, and the store has no
    copy of the original to disagree with.
    """
    commit = Commit(
        sha="a" * 40,
        parents=(),
        authored_at="2026-08-21T09:00:00+00:00",
        subject="s",
        body="b",
        files=(),
    )

    with pytest.raises(AttributeError):
        commit.subject = "rewritten"  # type: ignore[misc]
