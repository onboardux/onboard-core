"""A packaged-platform run, end to end -- `01` F8.3, F12.6, `02` §8, `05` S1.6.

**Through the orchestrator rather than against the extractors**, because every
claim here is about what a *run* does: the bundle has to become the indexed tree,
the queue has to be written beside the other artefacts, the honesty line has to
survive onto the first screen, and none of the client's source may appear in any
of it. Each of those is a place the behaviour could be dropped without an
extractor changing at all.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from adopt_extractors_common import pack as common_pack
from adopt_extractors_platform import pack as platform_pack
from adopt_map.emit.labeling_queue import LABELING_QUEUE_NAME
from adopt_map.emit.markdown import SURFACE_MD_NAME
from adopt_map.orchestrator import run as run_map
from adopt_map.plugins import ExtractorRegistry
from adopt_map.report import RunResult

from tests.build1_conftest import build_scoped_store, surface_writer_for

pytestmark = pytest.mark.integration

_BUNDLE = Path("fixtures/repos/sf-metadata-bundle")
_LABELS = Path("fixtures/labeled/sf-metadata-bundle.identities.json")


class Run(NamedTuple):
    result: RunResult
    out_dir: Path


@pytest.fixture
def run(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Run]:
    """One real run whose **subject is the bundle**, not the tree it was invoked in.

    `root` is deliberately the repository root here -- somewhere with no platform
    metadata in it at all -- so that every fact this run produces is evidence the
    bundle became the indexed subject. A test passing `root=_BUNDLE` would pass
    identically if `--export-bundle` were still ignored, which is the state S1.6
    found (`05` S1.1 shipped the flag accepted and unhonoured).
    """
    work = tmp_path_factory.mktemp("platform")
    handle, scopes = build_scoped_store(work, archetype="platform")
    out_dir = work / "out"
    registry = ExtractorRegistry(enabled_packs=frozenset({"common", "platform"}))
    registry.register_all(common_pack())
    registry.register_all(platform_pack())
    try:
        result = run_map(
            resolved=scopes["prod"],
            root=Path(),
            export_bundle=_BUNDLE,
            registry=registry,
            adopt_version="test",
            writer=surface_writer_for(handle),
            coverage_records=handle.coverage_records(),
            cache=handle.backend,
            out_dir=out_dir,
            sequential=True,
        )
        yield Run(result, out_dir)
    finally:
        handle.close()


def test_the_bundle_is_the_indexed_subject(run: Run) -> None:
    """*Defect sentence.* Fails when `--export-bundle` stops reaching the index --
    which is the state this sprint inherited, where the flag was accepted,
    validated and then ignored; matters because a packaged platform has no source
    tree, so a run that indexed the invocation directory would report an empty
    map for a system full of components and exit 0 while doing so; no other
    instrument catches it, because the refusal path (exit 4 with no bundle) is
    green either way.
    """
    assert run.result.exit_code == 0
    assert run.result.total_facts() > 0
    # Every source ref is bundle-relative, and nothing from the repository the
    # run was invoked in appears.
    paths = {ref.path for entry in run.result.minted() for ref in entry.fact.source_refs}
    assert paths, "no provenance recorded"
    assert all(not path.startswith("packages/") for path in paths), paths


def test_the_labeling_queue_is_written_and_matches_the_labeled_truth(run: Run) -> None:
    """`01` F12.6 and PRD Q7's file artifact, against the fixture's ground truth.

    *Defect sentence.* Fails when the queue stops being written, or when its
    count drifts from the components the export actually left unlabelled;
    matters because the queue is the work item a human is handed and the count is
    what a first screen promises; no other instrument catches a drift, because
    the recall figure counts identities and never looks at a label.
    """
    queue_path = run.out_dir / LABELING_QUEUE_NAME
    assert queue_path.exists(), sorted(p.name for p in run.out_dir.iterdir())
    queue = json.loads(queue_path.read_text(encoding="utf-8"))

    labels = json.loads(_LABELS.read_text(encoding="utf-8"))["identities"]
    expected = sum(1 for row in labels if row.get("unlabeled"))
    assert queue["unlabeled"] == expected
    assert queue["components"] == len(labels)


def test_the_first_screen_states_the_honest_limit(run: Run) -> None:
    """Design Appendix B, on the first screen rather than in an appendix.

    *Defect sentence.* Fails when a packaged-platform run stops naming its
    unlabelled share where a reader who stops after one screen will see it;
    matters because `02` §9.1 makes the first screen the honest headline and this
    archetype's headline **is** the limit -- *"day-one competence is genuinely
    worse until a human does a labelling pass"*; no other instrument catches it,
    because the queue file would still be written and the counts would all be
    right.
    """
    markdown = (run.out_dir / SURFACE_MD_NAME).read_text(encoding="utf-8")
    first_screen = markdown.split("## 8. Inventory")[0]
    assert "carry no human label" in first_screen
    assert LABELING_QUEUE_NAME in first_screen
    # Unnumbered, on B1-CR-75's precedent: numbering it would make the
    # inventory's number depend on the client's archetype.
    assert "## 9." not in markdown


def test_no_client_source_reaches_any_artifact(run: Run) -> None:
    """`03` §5.9 invariant 4 and `02` §9.3, over every file the run wrote.

    *Defect sentence.* Fails when a ServiceNow `<payload>` -- a Script Include's
    source, a business rule's condition -- reaches an artefact; matters because
    every artefact here is something a client hands to a reviewer, and `01` N16
    is a security-review promise rather than a preference; no other instrument
    catches it: the planted-secret suite watches for *secrets*, and a business
    rule's condition is not one.
    """
    forbidden = ("Class.create", "current.state.changes()", "record_update")
    for artifact in sorted(run.out_dir.iterdir()):
        text = artifact.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text, f"{artifact.name} carries client source: {marker}"
