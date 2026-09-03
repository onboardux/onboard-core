"""Every sweep of a client tree is bounded, and none of them leaves the tree.

*Fails when* a walk over an untrusted repository has no containment rule, no
symlink rule, no depth bound or no count bound. *Matters because* both failures
are reachable from a checkout somebody else controls: a `docs/elsewhere -> /etc`
symlink made `adopt ingest docs/` copy out-of-tree material into the knowledge
store and cite it by **absolute** path, and a tree of oversized files walked past
`MAP_TREE_TOO_LARGE` entirely because only readable files counted toward the
bound. *No other instrument catches either because* both produce a successful
run: the ingest reports documents, the map reports a tree, and nothing in either
payload says where the bytes came from or how many files were seen.

`adopt_detect.walk_files` has had all four rules since Build 0 and its docstring
calls itself *"the one walk"*. `documents._candidates` used `Path.rglob("*")`
beside it (B2-05); `SourceTree.scan` used it correctly and then counted the
wrong thing (B1-006).
"""

import sys
from pathlib import Path

import pytest
from adopt_knowledge.documents import discover
from adopt_map.tree import SourceTree

from adopt_const import MAP_MAX_FILE_BYTES
from adopt_obs import AdoptError, ErrorCode


@pytest.mark.unit
def test_two_oversized_files_still_reach_the_tree_bound(tmp_path: Path) -> None:
    """B1-006, reproduced and closed.

    *Fails when* `SourceTree.scan` counts only the files it keeps. An oversized
    file is skipped for its size, so before T1.6 it added nothing to the count
    and a tree of a million of them refused nothing at all -- the
    resource-exhaustion path the bound exists to close. Reproduced exactly as
    the review described it: two oversized files and `max_files=1` returned
    without raising.
    """
    oversized = b"x" * (MAP_MAX_FILE_BYTES + 1)
    (tmp_path / "a.py").write_bytes(oversized)
    (tmp_path / "b.py").write_bytes(oversized)
    (tmp_path / "c.py").write_bytes(b"ok")

    with pytest.raises(AdoptError) as refusal:
        SourceTree.scan(tmp_path, max_files=1)

    assert refusal.value.code == ErrorCode.MAP_TREE_TOO_LARGE


@pytest.mark.unit
def test_a_tree_inside_the_bound_is_still_scanned(tmp_path: Path) -> None:
    """The control: counting every candidate must not refuse an ordinary tree.

    Without this, a `scan` that raised unconditionally would satisfy the row
    above and make `adopt map` unusable.
    """
    (tmp_path / "a.py").write_bytes(b"ok")
    (tmp_path / "b.py").write_bytes(b"x" * (MAP_MAX_FILE_BYTES + 1))

    tree = SourceTree.scan(tmp_path, max_files=2)

    assert [entry.path for entry in tree.files] == ["a.py"]
    assert tree.oversized == ("b.py",)


@pytest.mark.unit
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="creating a symlink needs Developer Mode or elevation on Windows; the "
    "knowledge-journey and unit jobs run this on ubuntu-24.04, where it is the "
    "assertion that matters",
)
def test_a_symlink_out_of_the_tree_is_not_ingested(tmp_path: Path) -> None:
    """B2-05, the half that reaches outside the repository.

    A checkout is somebody else's content. `Path.rglob("*")` follows a directory
    symlink, so a link committed into a client repository made `adopt ingest`
    read and store files the operator never pointed at -- and `_relative_to`
    recorded the absolute path of them, because a path outside the root has no
    relative form.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("# Secret\n\nNot ours.\n", encoding="utf-8")
    tree = tmp_path / "tree"
    (tree / "docs").mkdir(parents=True)
    (tree / "docs" / "ours.md").write_text("# Ours\n\nFine.\n", encoding="utf-8")
    (tree / "docs" / "elsewhere").symlink_to(outside, target_is_directory=True)

    documents = discover([tree / "docs"], root=tree)

    assert [document.path for document in documents] == ["docs/ours.md"]


@pytest.mark.unit
def test_directory_ingest_sees_exactly_what_the_one_walk_sees(tmp_path: Path) -> None:
    """The symlink row's platform-independent half.

    A symlink cannot be created on Windows without elevation, so the row above
    skips there and runs in CI. This one runs everywhere and asserts the
    *routing* rather than one of its consequences: what a directory ingest
    considers is exactly what `walk_files` yields, so every rule that walk
    carries -- containment, symlinks, depth, `.gitignore`, `.git` -- applies to
    ingest by construction rather than by a second implementation agreeing.

    `.git` is the discriminator: `Path.rglob("*")` walks into it and
    `walk_files` never does, so a tree with a `.md` file inside `.git` tells the
    two walks apart on any platform.
    """
    from adopt_detect import walk_files

    docs = tmp_path / "docs"
    (docs / ".git").mkdir(parents=True)
    (docs / ".git" / "COMMIT_EDITMSG.md").write_text("# not a document\n", encoding="utf-8")
    (docs / "real.md").write_text("# Real\n\nBody.\n", encoding="utf-8")

    walked = sorted(
        relative for relative, _absolute in walk_files(docs) if relative.endswith(".md")
    )
    ingested = [document.path for document in discover([docs], root=docs)]

    assert walked == ["real.md"], "the fixture must actually distinguish the two walks"
    assert ingested == walked


@pytest.mark.unit
def test_a_directory_beyond_the_count_bound_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count bound, with the constant lowered rather than the tree grown.

    Two hundred thousand files is the real bound and building one is not a unit
    test. Patching the name `documents` reads is the honest small version: it
    exercises the branch, and `DETECT_MAX_FILES` itself stays where `03` §2 put
    it.
    """
    import adopt_knowledge.documents as documents_module

    monkeypatch.setattr(documents_module, "DETECT_MAX_FILES", 1)
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("a.md", "b.md", "c.md"):
        (docs / name).write_text(f"# {name}\n\nBody.\n", encoding="utf-8")

    with pytest.raises(AdoptError) as refusal:
        discover([docs], root=tmp_path)

    assert refusal.value.code == ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE


@pytest.mark.unit
def test_a_named_file_outside_the_tree_is_still_ingested(tmp_path: Path) -> None:
    """The policy the bound must not change.

    An operator naming a path outright is making a choice; the walk's rules exist
    to stop a *sweep* reaching where nobody pointed it. Without this control, a
    containment rule applied to named files too would break `adopt ingest
    ../shared/runbook.md`, which is an ordinary thing to do on an engagement.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    named = outside / "runbook.md"
    named.write_text("# Runbook\n\nDeliberate.\n", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()

    documents = discover([named], root=tree)

    assert len(documents) == 1
    assert documents[0].path.endswith("runbook.md")


@pytest.mark.unit
def test_ordinary_directory_ingest_still_finds_its_documents(tmp_path: Path) -> None:
    """The control for the whole file: a bounded walk that found nothing would
    pass every assertion above."""
    docs = tmp_path / "docs"
    (docs / "nested").mkdir(parents=True)
    (docs / "one.md").write_text("# One\n\nBody.\n", encoding="utf-8")
    (docs / "nested" / "two.md").write_text("# Two\n\nBody.\n", encoding="utf-8")
    (docs / "picture.png").write_bytes(b"\x89PNG")

    documents = discover([docs], root=tmp_path)

    assert [document.path for document in documents] == ["docs/nested/two.md", "docs/one.md"]
