"""The poisoned fixture must not detonate -- `01` F7.2, N8, `03` §7.

*Defect sentence.* Fails the moment any part of a run imports, executes or
evaluates client code; matters because `01` §1.6 makes static-only a
non-negotiable invariant and `01` U4 invites a client security reviewer to check
exactly this; no other instrument catches it because a run that imported client
code would produce a **better** map -- resolved imports, real signatures -- and
look like an improvement.

**The canary is a file the fixture writes to itself at import scope.** No
environment variable, no monkeypatch, no mock: the tree is copied, the run
happens, and the canary is either in the copy or it is not. An assertion that
depended on a test double could be satisfied by a double that was never wired up.
"""

import shutil
from pathlib import Path

import pytest
from adopt_extractors_common import pack
from adopt_map.orchestrator import run as run_map
from adopt_map.plugins import ExtractorRegistry

from tests.build1_conftest import build_scoped_store

pytestmark = pytest.mark.integration

_FIXTURE = Path("fixtures/repos/poisoned-import")

#: Every canary the fixture can write. Named here so a new poisoned module with a
#: new canary fails this list rather than going unchecked -- the same argument
#: `AUDIT_RULES` makes for the audit suite.
_CANARIES = ("DETONATED.txt", "orders/REGISTERED.txt", "orders/PACKAGE_IMPORTED.txt")


def _tree(tmp_path: Path) -> Path:
    """A per-test copy. The fixture is mutated by detonation, so a shared tree
    would leave the repository dirty and the second run would pass for the wrong
    reason."""
    destination = tmp_path / "poisoned"
    shutil.copytree(_FIXTURE, destination)
    return destination


def test_the_fixture_really_does_detonate_when_imported(tmp_path: Path) -> None:
    """**The control.** Without this the whole file is vacuous.

    A fixture that had stopped detonating -- a syntax error, a moved canary, a
    refactor that made the side effect conditional -- would make every assertion
    below pass while testing nothing. So the poison is proven live first, by
    importing it the one time we ever do, in a throwaway copy.
    """
    tree = _tree(tmp_path)
    source = (tree / "detonator.py").read_text(encoding="utf-8")
    namespace: dict[str, object] = {"__file__": str(tree / "detonator.py")}
    exec(compile(source, str(tree / "detonator.py"), "exec"), namespace)  # noqa: S102
    assert (tree / "DETONATED.txt").exists(), "the poisoned fixture no longer detonates"


def test_a_full_run_over_the_poisoned_tree_leaves_every_canary_absent(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """`01` F7.2 end to end: index, plan, audit, extract, write, emit.

    The whole lifecycle runs over a tree engineered to punish an import, and
    nothing detonates -- because every stage reads bytes.
    """
    tree = _tree(tmp_path)
    handle, scopes = build_scoped_store(tmp_path_factory.mktemp("poisoned"))
    registry = ExtractorRegistry()
    registry.register_all(pack())

    from tests.build1_conftest import surface_writer_for

    result = run_map(
        resolved=scopes["prod"],
        root=tree,
        registry=registry,
        adopt_version="0.3.0",
        writer=surface_writer_for(handle),
        coverage_records=handle.coverage_records(),
        cache=handle.backend,
        out_dir=tmp_path / "out",
        sequential=True,
    )

    for canary in _CANARIES:
        assert not (tree / canary).exists(), f"{canary} appeared: client code was imported"
    # And the run was not empty -- a run that extracted nothing would leave every
    # canary absent perfectly.
    assert result.total_facts() > 0


def test_the_run_reads_the_poisoned_tree_without_executing_it(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """It is not enough that nothing detonated: the tree must actually be read.

    A file index that skipped `.py` files entirely would satisfy the canary
    assertion and map nothing. So this asserts the symbols in the poisoned
    modules were recovered -- read as text, by name, from files whose import
    would have written a canary.
    """
    tree = _tree(tmp_path)
    handle, scopes = build_scoped_store(tmp_path_factory.mktemp("poisoned-read"))
    registry = ExtractorRegistry()
    registry.register_all(pack())

    from tests.build1_conftest import surface_writer_for

    result = run_map(
        resolved=scopes["prod"],
        root=tree,
        registry=registry,
        adopt_version="0.3.0",
        writer=surface_writer_for(handle),
        coverage_records=handle.coverage_records(),
        cache=handle.backend,
        out_dir=tmp_path / "out",
        sequential=True,
    )

    keys = {entry.fact.local_key for entry in result.minted()}
    assert any("OrderDetailView" in key for key in keys), "the tree was not read at all"
    assert not (tree / "DETONATED.txt").exists()


def test_no_secret_value_from_the_poisoned_tree_reaches_any_artifact(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """`01` N9, over a tree carrying planted credentials.

    The fixture's `.env` holds two values marked `should-never-appear`. They are
    unrepresentable rather than filtered: `02` §5.1 rule 4 gives the `secret:*`
    namespace a model with **no value field**, so there is nowhere for them to
    live. This asserts the outcome across every byte the run wrote.
    """
    tree = _tree(tmp_path)
    handle, scopes = build_scoped_store(tmp_path_factory.mktemp("poisoned-secrets"))
    registry = ExtractorRegistry()
    registry.register_all(pack())
    out = tmp_path / "out"

    from tests.build1_conftest import surface_writer_for

    run_map(
        resolved=scopes["prod"],
        root=tree,
        registry=registry,
        adopt_version="0.3.0",
        writer=surface_writer_for(handle),
        coverage_records=handle.coverage_records(),
        cache=handle.backend,
        out_dir=out,
        formats=("md", "json", "mermaid", "d2"),
        sequential=True,
    )

    written = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(out.rglob("*"))
        if path.is_file()
    )
    assert "hunter2-should-never-appear" not in written
    assert "sk-should-never-appear" not in written
    # The *reference* is present, which is what makes the absence meaningful: a
    # run that had skipped the file would also contain no secret.
    assert "DATABASE_PASSWORD" in written
