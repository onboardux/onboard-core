"""The SKILL.md loader: AI spec §6.

*Fails when* the whole-directory digest narrows to `SKILL.md`, when a malformed
skill is accepted, when a `skill_ref` escapes the skills root, or when
`scripts/` are executed rather than copied. *Matters because* `skill_sha256` is
what lets an audit **inside a client environment** reconstruct what was asked
without the provider (AI spec §5.2), and a digest that misses the reference
material a prompt loads binds a run to bytes that are not the ones that were
sent -- while an executed script is the permanently cut non-goal in PRD §10.
*No other instrument catches them because* the conformance suite asserts a trace
*carries* a `skill_sha256` (case 11) and never that the digest covers the right
bytes, and no gate reads this directory.
"""

from pathlib import Path

import pytest

from adopt_agent.skills import load_skill
from adopt_const import SKILL_DESCRIPTION_MAX_CHARS, SKILL_NAME_MAX_CHARS
from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit

_GOOD = "---\nname: detect\ndescription: Classify a system.\n---\n\nBody text.\n"


def _skill(root: Path, ref: str, text: str = _GOOD) -> Path:
    path = root / ref
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(text, encoding="utf-8")
    return path


def test_a_valid_skill_loads_with_its_frontmatter_and_body(tmp_path: Path) -> None:
    _skill(tmp_path, "detect/v1")

    loaded = load_skill("detect/v1", root=tmp_path)

    assert loaded.name == "detect"
    assert loaded.description == "Classify a system."
    assert loaded.body == "Body text.\n"
    assert len(loaded.sha256) == 64


def test_the_digest_covers_the_whole_directory_not_just_skill_md(tmp_path: Path) -> None:
    """Two skills with identical SKILL.md and different references are not the
    same skill, and a digest that says they are makes the audit trail wrong."""
    a = _skill(tmp_path, "a/v1")
    b = _skill(tmp_path, "b/v1")
    (a / "references").mkdir()
    (b / "references").mkdir()
    (a / "references" / "notes.md").write_text("erp", encoding="utf-8")
    (b / "references" / "notes.md").write_text("crm", encoding="utf-8")

    assert load_skill("a/v1", root=tmp_path).sha256 != load_skill("b/v1", root=tmp_path).sha256


def test_renaming_a_reference_changes_the_digest(tmp_path: Path) -> None:
    """Content-only hashing reports a rename as no change at all, and a rename
    changes what the skill means to whoever reads it."""
    path = _skill(tmp_path, "detect/v1")
    (path / "references").mkdir()
    (path / "references" / "erp.md").write_text("same bytes", encoding="utf-8")
    before = load_skill("detect/v1", root=tmp_path).sha256

    (path / "references" / "erp.md").rename(path / "references" / "crm.md")

    assert load_skill("detect/v1", root=tmp_path).sha256 != before


def test_the_digest_is_stable_across_loads(tmp_path: Path) -> None:
    """`detect-001@1` means one byte sequence forever (AI spec §5); a digest
    that moved between loads would make every trace unreproducible."""
    _skill(tmp_path, "detect/v1")

    first = load_skill("detect/v1", root=tmp_path).sha256
    second = load_skill("detect/v1", root=tmp_path).sha256

    assert first == second


@pytest.mark.parametrize(
    ("case", "text"),
    [
        ("no frontmatter at all", "Just a body.\n"),
        ("unterminated frontmatter", "---\nname: x\ndescription: y\n"),
        ("frontmatter is not a mapping", "---\n- a\n- b\n---\nBody\n"),
        ("frontmatter is not valid yaml", "---\nname: [unclosed\n---\nBody\n"),
        ("no name", "---\ndescription: y\n---\nBody\n"),
        ("blank name", "---\nname: '   '\ndescription: y\n---\nBody\n"),
        ("name is not a string", "---\nname: 7\ndescription: y\n---\nBody\n"),
        ("no description", "---\nname: x\n---\nBody\n"),
        ("blank description", "---\nname: x\ndescription: '  '\n---\nBody\n"),
        (
            "name over the limit",
            f"---\nname: {'n' * (SKILL_NAME_MAX_CHARS + 1)}\ndescription: y\n---\nBody\n",
        ),
        (
            "description over the limit",
            f"---\nname: x\ndescription: {'d' * (SKILL_DESCRIPTION_MAX_CHARS + 1)}\n---\nB\n",
        ),
    ],
)
def test_a_malformed_skill_is_refused_before_anything_else(
    tmp_path: Path, case: str, text: str
) -> None:
    """One row per way a skill can be wrong.

    The refusal must happen at load, because the runner loads before it
    dispatches -- a skill validated after dispatch bills a client for a request
    that could never have been answered.
    """
    _skill(tmp_path, "bad/v1", text)

    with pytest.raises(AdoptError) as raised:
        load_skill("bad/v1", root=tmp_path)

    assert raised.value.code is ErrorCode.MANIFEST_INVALID, case


def test_a_directory_without_skill_md_is_refused(tmp_path: Path) -> None:
    (tmp_path / "empty" / "v1").mkdir(parents=True)

    with pytest.raises(AdoptError) as raised:
        load_skill("empty/v1", root=tmp_path)

    assert raised.value.code is ErrorCode.MANIFEST_INVALID


@pytest.mark.parametrize("ref", ["../outside", "detect/../../outside", "", "   "])
def test_a_skill_ref_that_escapes_the_root_is_refused(tmp_path: Path, ref: str) -> None:
    """A caller that can name `../..` can hash and copy any directory on the
    machine. Containment is checked against the resolved path, so a symlink out
    of the tree is caught too -- a string check for `..` is not."""
    root = tmp_path / "skills"
    root.mkdir()
    _skill(tmp_path, "outside")

    with pytest.raises(AdoptError) as raised:
        load_skill(ref, root=root)

    assert raised.value.code is ErrorCode.MANIFEST_INVALID


def test_scripts_are_copied_into_scratch_and_never_run(tmp_path: Path) -> None:
    """The sentinel is the assertion.

    A script that *would* create `ran.txt` if executed proves the loader did not
    execute it -- checking only that the file was copied would pass whether or
    not something ran it afterwards.
    """
    path = _skill(tmp_path, "detect/v1")
    (path / "scripts").mkdir()
    sentinel = tmp_path / "ran.txt"
    (path / "scripts" / "setup.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    loaded = load_skill("detect/v1", root=tmp_path, scratch=scratch)

    assert loaded.materialized_scripts == (scratch / "scripts" / "setup.py",)
    assert (scratch / "scripts" / "setup.py").read_text(encoding="utf-8").startswith("from")
    assert not sentinel.exists()


def test_no_scratch_directory_means_no_materialization(tmp_path: Path) -> None:
    """Hashing a skill is not a reason to write anything to disk."""
    path = _skill(tmp_path, "detect/v1")
    (path / "scripts").mkdir()
    (path / "scripts" / "setup.py").write_text("pass\n", encoding="utf-8")

    assert load_skill("detect/v1", root=tmp_path).materialized_scripts == ()
