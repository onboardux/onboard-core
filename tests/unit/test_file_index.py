"""One walk, disclosed sampling, and the read-only tree -- `03` §5.8, §6.

*Defect sentence.* Fails when the tree is walked more than once, when sampling
stops being disclosed, or when a skip is silent; matters because every extractor
seeing the same file set is what makes `01` N4's determinism a property of the run
rather than of which extractors were enabled, and because a sampled run that reads
as a complete one is the most expensive claim this build can make; no other
instrument catches it because a second walk produces a *plausible* index and a
silent sample produces a *plausible* map.
"""

import os
from pathlib import Path

import pytest
from adopt_map.fileindex import build_index, detect_language, git_blob_sha

pytestmark = pytest.mark.unit


def _tree(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def one():\n    pass\n", encoding="utf-8")
    (root / "pkg" / "b.ts").write_text("export function two() {}\n", encoding="utf-8")
    (root / "README.md").write_text("# tree\n", encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    (root / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
    (root / "noise.log").write_text("ignored\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "out.py").write_text("def gone():\n    pass\n", encoding="utf-8")
    return root


def test_the_tree_is_walked_exactly_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A call counter over `os.walk`, because "one walk" is a claim about calls.

    Two walks are two chances to disagree about what the tree contains, and a
    fact emitted against a file another extractor never saw is a fact nobody can
    reproduce.
    """
    calls = 0
    original = os.walk

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "walk", counted)
    build_index(_tree(tmp_path))
    assert calls == 1


def test_ignored_paths_are_pruned_and_binaries_are_skipped_and_counted(tmp_path: Path) -> None:
    """`.gitignore` prunes; a binary is skipped **and counted**.

    Counted rather than dropped: `03` §5.8's disclosure argument applies to every
    exclusion, and a skip nobody counts is indistinguishable from a file that was
    read and found empty.
    """
    index = build_index(_tree(tmp_path))
    paths = {entry.path for entry in index.files}
    assert "pkg/a.py" in paths
    assert "noise.log" not in paths, "the .gitignore glob did not prune"
    assert "build/out.py" not in paths, "the directory-only pattern did not prune"
    assert "logo.png" not in paths
    assert index.skipped_binary == 1


def test_files_are_sorted_by_path_not_by_directory_order(tmp_path: Path) -> None:
    """The index order is a property of the tree, not of the filesystem."""
    index = build_index(_tree(tmp_path))
    assert [entry.path for entry in index.files] == sorted(entry.path for entry in index.files)


def test_a_large_file_is_skipped_and_counted(tmp_path: Path) -> None:
    (tmp_path / "big.py").write_text("x = 1\n" * 100, encoding="utf-8")
    index = build_index(tmp_path, max_file_bytes=10)
    assert index.skipped_large == 1
    assert index.files == ()


def test_sampling_above_the_threshold_is_always_disclosed(tmp_path: Path) -> None:
    """`03` §5.8: sampling is disclosed or it does not happen.

    The disclosure carries **both denominators**, because "we examined 12 files"
    without "of 40" is a sentence that reads like completeness.
    """
    for index in range(40):
        (tmp_path / f"f{index:03d}.py").write_text("def f():\n    pass\n", encoding="utf-8")
    sampled = build_index(tmp_path, max_tree_files=10, sampling_ratio=0.25)
    assert sampled.sampled is True
    assert len(sampled.files) < sampled.discovered
    disclosure = sampled.disclosure()
    assert disclosure is not None
    assert str(sampled.discovered) in disclosure
    assert str(len(sampled.files)) in disclosure


def test_an_unsampled_index_discloses_nothing(tmp_path: Path) -> None:
    """The disclosure is `None` when nothing was sampled.

    A run that always printed a sampling notice would train readers to ignore it,
    which is the failure the notice exists to prevent.
    """
    assert build_index(_tree(tmp_path)).disclosure() is None


def test_the_sample_is_a_pure_function_of_the_path(tmp_path: Path) -> None:
    """`01` N4. The same tree yields the same sample on every run and machine.

    A stride or a random sample would make which files were read depend on how
    many happened to be indexed, and `source_version` stability with it.
    """
    for index in range(40):
        (tmp_path / f"f{index:03d}.py").write_text("def f():\n    pass\n", encoding="utf-8")
    first = build_index(tmp_path, max_tree_files=10, sampling_ratio=0.25)
    second = build_index(tmp_path, max_tree_files=10, sampling_ratio=0.25)
    assert [entry.path for entry in first.files] == [entry.path for entry in second.files]


def test_the_blob_sha_is_gits_own_object_id() -> None:
    """`02` §6's `<blob-sha>` is one a client can look up with `git cat-file`.

    Asserted against git's documented framing for the empty blob, which is the
    one value the algorithm cannot get right by coincidence.
    """
    assert git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def test_language_detection_answers_none_rather_than_guessing() -> None:
    """An unknown suffix is `None`, which the ladder reads as *no evidence*.

    A default of "text" would put every unrecognised file into a family and let
    a rung claim it.
    """
    assert detect_language("pkg/a.py") == "python"
    assert detect_language("pkg/b.ts") == "typescript"
    assert detect_language("pkg/mystery.zzz") is None


def test_a_tree_that_is_not_a_checkout_reports_no_vcs_revision(tmp_path: Path) -> None:
    """B1-CR-36: `None` is a real answer, not a failure.

    A tree with no commit produces **no `provenance` row and a recorded gap**
    rather than a `commit` claim nobody observed.
    """
    assert build_index(_tree(tmp_path)).vcs_revision is None
