"""Idempotence and the attribute digest -- Build 1's two structural promises.

Idempotence is a property of the *write path* (v6.1 §6): `observe` is keyed on
the URI, so a second run over an unchanged tree writes nothing. That is asserted
here against a real store rather than by counting observations, because the
observations are identical either way -- what must not change is the store.

The digest half is H5. A digest that moved on a comment would make every
formatting change a `SEMANTICS-CHANGED` event in Build 6, which is the false
staleness that makes a reviewer stop trusting the queue.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from adopt_map import SourceTree, attribute_digest, registry, run_map, select_packs
from hypothesis import given, settings
from hypothesis import strategies as st

from adopt_scope import Scope
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle

_SOURCE = (
    "import os\n"
    "from fastapi import FastAPI\n"
    "app = FastAPI()\n"
    "DB = os.environ['DATABASE_URL']\n"
    "\n"
    "@app.post('/v1/orders')\n"
    "def create(payload: dict, idempotency_key: str):\n"
    "    return {}\n"
)


@pytest.fixture
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SqliteStoreHandle]:
    handle = open_store(tmp_path_factory.mktemp("map") / "store.db", migrate=True)
    yield handle
    handle.close()


@pytest.fixture
def scope(store: SqliteStoreHandle) -> Scope:
    facade = store.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme", name="ACME")
    system = facade.create_system(engagement_id=engagement.id, slug="orders", name="Orders")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    return facade.resolve("northwind/acme/orders/prod")


def _repo(root: Path, source: str) -> SourceTree:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text(source, encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["fastapi"]\n', encoding="utf-8"
    )
    return SourceTree.scan(root)


def _counts(store: SqliteStoreHandle) -> tuple[int, int]:
    counts = store.counts()
    return counts.get("identity", 0), counts.get("identity_revision", 0)


def _run(tree: SourceTree, store: SqliteStoreHandle, scope: Scope) -> None:
    run_map(
        tree=tree,
        scope=scope,
        packs=select_packs("web", available=registry()),
        writer=store.identities(),
        records=store.revision_records(),
    )


@pytest.mark.property
def test_a_second_run_over_an_unchanged_tree_writes_nothing(
    tmp_path: Path, store: SqliteStoreHandle, scope: Scope
) -> None:
    """The demo's third line, and the promise every later build rests on.

    *Fails when* re-observation starts appending. *Matters because* an FDE runs
    `adopt map` repeatedly on a live engagement, and a store that grows a
    revision per scan turns the revision chain into a log of scans rather than a
    record of changes -- at which point Build 6's diff is measuring how often
    someone ran the tool. *No other instrument catches it because* the second run
    succeeds and reports the same identity count either way; only the store
    differs."""
    tree = _repo(tmp_path, _SOURCE)

    _run(tree, store, scope)
    after_first = _counts(store)
    _run(tree, store, scope)

    assert _counts(store) == after_first
    assert after_first[0] > 0, "a run that found nothing would pass this test vacuously"


@pytest.mark.property
def test_a_comment_only_edit_changes_no_digest(
    tmp_path: Path, store: SqliteStoreHandle, scope: Scope
) -> None:
    """Invariant H5, at the level Build 1 can prove it.

    *Fails when* a digest covers file bytes rather than extracted attributes.
    *Matters because* Build 6 maps a digest change to `SEMANTICS-CHANGED` and
    stales every bound knowledge item -- so a reformatting sweep would stale an
    entire corpus at once, which is precisely the flood that makes reviewers
    abandon the queue. *No other instrument catches it because* a content hash
    produces a perfectly stable, perfectly wrong answer that no round-trip or
    schema check can distinguish from a correct one."""
    commented = _SOURCE.replace(
        "@app.post('/v1/orders')", "# an explanatory comment\n@app.post('/v1/orders')"
    )
    before = _digests(_repo(tmp_path / "before", _SOURCE))
    after = _digests(_repo(tmp_path / "after", commented))

    assert before == after, "a comment moved a digest; the digest is hashing the wrong thing"


@pytest.mark.property
def test_a_changed_parameter_does_change_the_digest(
    tmp_path: Path, store: SqliteStoreHandle, scope: Scope
) -> None:
    """The positive control, without which the test above passes by hashing a
    constant.

    *Fails when* the digest stops covering the attributes it is supposed to
    cover. *Matters because* a digest that never changes is invisible to every
    test asserting stability, and Build 6 would then detect nothing at all. *No
    other instrument catches it because* "nothing changed" is the expected result
    everywhere else."""
    renamed = _SOURCE.replace("idempotency_key: str", "request_id: str")
    before = _digests(_repo(tmp_path / "before", _SOURCE))
    after = _digests(_repo(tmp_path / "after", renamed))

    assert before != after


def _digests(tree: SourceTree) -> set[str]:
    from adopt_map.packs import web

    extractor = web.EndpointExtractor()
    return {
        attribute_digest(observation.attributes, extractor_version=extractor.version)
        for observation in extractor.extract(tree)
    }


@pytest.mark.property
@settings(max_examples=25, deadline=None)
@given(
    attributes=st.dictionaries(
        st.text(min_size=1, max_size=12),
        st.one_of(st.text(max_size=20), st.integers(), st.booleans(), st.none()),
        max_size=6,
    )
)
def test_a_digest_is_independent_of_key_insertion_order(attributes: dict[str, object]) -> None:
    """*Fails when* canonical rendering stops sorting keys. *Matters because* a
    dict's insertion order depends on the order an extractor happened to assign
    fields, so an unsorted digest would change when someone reordered two lines
    of extractor code -- manufacturing a change storm across every identity that
    extractor has ever seen. *No other instrument catches it because* small dicts
    frequently round-trip in the same order by chance, so an example-based test
    passes most of the time."""
    reversed_order = dict(reversed(list(attributes.items())))

    assert attribute_digest(attributes, extractor_version="1") == attribute_digest(
        reversed_order, extractor_version="1"
    )


@pytest.mark.property
def test_digests_from_two_extractor_versions_never_compare_equal() -> None:
    """*Fails when* the extractor version stops being part of the digest input.
    *Matters because* v6.1 §6 makes comparisons valid only within one version --
    an extractor upgrade must re-baseline ("we changed how we look"), not report
    a change storm. If two versions could produce equal digests for different
    attribute meanings, Build 6 would silently compare across the boundary. *No
    other instrument catches it because* both digests are well-formed and stable;
    only their comparability is wrong."""
    attributes = {"method": "GET", "path": "/x", "parameters": []}

    assert attribute_digest(attributes, extractor_version="1") != attribute_digest(
        attributes, extractor_version="2"
    )
