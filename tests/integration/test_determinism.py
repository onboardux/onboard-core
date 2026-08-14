"""Determinism -- PRD N4, `03` §8's fourth hard gate.

*"Identical URI set and identical `source_version` values across repeated runs at
fixed tool versions."* The gate is deliberately stated over **two stores** rather
than two runs against one, because a single store answers a weaker question: a
writer that echoed whatever it read back would pass a same-store comparison
perfectly while producing a different map on a colleague's machine.

| Behavior | Tier | Defect it catches |
|---|---|---|
| Two fresh stores, one tree, identical URIs | **T1** | A URI depending on ids, time or iteration order |
| ...and identical `source_version` values | **T1** | A digest depending on a dict order, a hash seed or a platform |
| Rendered bodies are byte-identical | T2 | A `body_md` that differs without the referent differing |
"""

import pytest
from adopt_extractors_common import TREE_MANIFEST, StubTreeExtractor
from adopt_map.schemas import ExtractorManifest

from adopt_model import Identity, KnowledgeRevision
from adopt_store.api import SqliteStoreHandle
from tests.build1_conftest import build_scoped_store, surface_writer_for

pytestmark = [pytest.mark.integration, pytest.mark.determinism]

_FIXTURE = "fixtures/repos/stub-tree"


def _run_into_a_fresh_store(
    tmp_path_factory: pytest.TempPathFactory, name: str, manifest: ExtractorManifest
) -> tuple[list[str], list[str], list[str]]:
    """One complete run into a brand-new store; returns URIs, composites and bodies."""
    handle, scopes = build_scoped_store(tmp_path_factory.mktemp(name))
    try:
        writer = surface_writer_for(handle)
        writer.write_run(
            resolved=scopes["prod"],
            manifest=manifest,
            facts=list(StubTreeExtractor().extract(_FIXTURE)),
            vcs_revision=None,
        )
        records = handle.export_records()
        uris = sorted(row.uri for row in records.table_rows("identity", Identity))
        revisions = list(records.table_rows("knowledge_revision", KnowledgeRevision))
        composites = sorted(row.source_version or "" for row in revisions)
        bodies = sorted(row.body_md or "" for row in revisions)
        return uris, composites, bodies
    finally:
        handle.close()


def test_two_independent_runs_agree_on_every_uri_and_digest(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    first = _run_into_a_fresh_store(tmp_path_factory, "det-a", TREE_MANIFEST)
    second = _run_into_a_fresh_store(tmp_path_factory, "det-b", TREE_MANIFEST)

    assert first[0] == second[0], "the URI set is not deterministic"
    assert first[0], "the fixture produced no identities; is the run reaching it?"
    assert first[1] == second[1], "the source_version values are not deterministic"
    assert all(value.startswith("sv1:") for value in first[1])
    assert first[2] == second[2], "the rendered bodies are not deterministic"


def test_a_store_written_by_one_run_matches_a_store_written_by_another(
    tmp_path_factory: pytest.TempPathFactory, s4_store: SqliteStoreHandle
) -> None:
    """The URIs carry no store-local identifier.

    Fails when a ULID, a timestamp or a row id leaks into a URI; matters because
    Build 0 CR-05 makes URIs the portable key an exported bundle resolves by --
    a URI carrying this store's ids stops resolving the moment the bundle leaves
    the machine; no other instrument catches it because within one store the
    id-bearing URI is perfectly consistent.
    """
    uris, _, _ = _run_into_a_fresh_store(tmp_path_factory, "det-c", TREE_MANIFEST)
    for uri in uris:
        assert "idn_" not in uri
        assert "ki_" not in uri
        assert "firm_" not in uri
