"""N2 -- two runs over identical trees produce byte-identical output, always.

*Fails when* any ordering in detection starts depending on the filesystem, on a
`set`'s layout, or on a dict built from either. *Matters because* PRD N15 makes
reproducibility the client-audit posture: an FDE and a client running the same
command on the same checkout must get the same bytes, and a detection that
drifted would make every downstream extraction unreproducible from its first
step. *No other instrument catches it because* the corpus table asserts each tree
gets the *right* answer, not that it gets the *same* answer twice.

**Compared as serialized JSON, not as objects.** N2's claim is about output, and
two dicts can compare equal while serializing to different bytes because their
key order differs -- which is precisely the failure a client diffing two runs
would see.
"""

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adopt_cli.commands.detect import build_payload
from adopt_detect import detect

REPOS = Path(__file__).resolve().parent.parent / "fixtures" / "repos"

#: Every tree in the corpus, plus the ambiguous one -- because an ambiguous
#: result carries *more* output (ranked scores, every rule that fired) and is
#: therefore the harder case to keep stable.
TREES: list[Path] = sorted(
    [
        path
        for archetype in REPOS.iterdir()
        if archetype.is_dir()
        for path in archetype.iterdir()
        if path.is_dir()
    ]
    + [path for path in REPOS.iterdir() if path.is_dir()]
)


def _rendered(tree: Path) -> str:
    return json.dumps(build_payload(detect(tree)), sort_keys=False)


@pytest.mark.property
@pytest.mark.parametrize("tree", TREES, ids=lambda path: f"{path.parent.name}/{path.name}")
def test_two_runs_over_one_tree_are_byte_identical(tree: Path) -> None:
    assert _rendered(tree) == _rendered(tree)


@pytest.mark.property
@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    names=st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=1, max_size=12),
        min_size=1,
        max_size=12,
        unique=True,
    )
)
def test_output_does_not_depend_on_the_order_files_were_created(
    tmp_path_factory: pytest.TempPathFactory, names: list[str]
) -> None:
    """The property the corpus cannot show, because it was written once.

    *Fails when* `rules_fired` starts recording whichever path the filesystem
    happened to return first. Two trees with the same *contents* built in
    opposite orders must produce the same bytes -- on one filesystem that is
    nearly always true by luck, which is exactly why it needs asserting rather
    than observing.
    """
    forwards = tmp_path_factory.mktemp("forwards")
    backwards = tmp_path_factory.mktemp("backwards")

    for name in names:
        (forwards / f"{name}.py").write_text("from fastapi import FastAPI\n", encoding="utf-8")
    for name in reversed(names):
        (backwards / f"{name}.py").write_text("from fastapi import FastAPI\n", encoding="utf-8")

    # Comparable directly: every path in the payload is relative to its own
    # root, which is what makes a bundle of detection output portable at all.
    assert _rendered(forwards) == _rendered(backwards)
