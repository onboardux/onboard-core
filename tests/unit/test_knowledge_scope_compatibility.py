"""Build 2's reads answer for one environment, and a binding stays inside one system.

*Fails when* `stored_documents`, `harvested_commits` or `pending_items` matches
on `system_id` alone, or when `BindingFacade` accepts an item and an identity
from different scopes. *Matters because* both defects corrupt canon rather than
hiding it: an ingest run under `staging` recognised the **prod** item at the same
relative path as already stored and appended the staging document's body to the
prod item's revision chain -- no staging item was created, the prod item's
current text became the staging document, and the command exited `0` with the
chain reading as an ordinary edit. And `adopt bind` accepted two ids from two
different firms, writing a `binding` row whose two ends resolve to different
tenants. *No other instrument catches either because* every row is well formed
and every foreign key resolves; only an assertion about **which** environment
and **which** system the rows belong to can see it.

Both were found by the independent Build 2 review (B2-01, B2-06) and both were
still present on `build10/fleet-console` when this file was written.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from adopt_obs import AdoptError, ErrorCode, ExitCode
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINT = REPO_ROOT / "packages" / "adopt-cli" / "src" / "adopt_cli" / "main.py"

ANSWERS = {"artifact_access": True, "deploy_signal": True, "safe_interaction": True}


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRY_POINT), *argv],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def two_environments(tmp_path: Path) -> tuple[Path, Path]:
    """A store with `.../orders-api/prod` and `.../orders-api/staging`, and a tree.

    Built through the CLI, because the defect was in what the CLI's own helpers
    read -- a fixture assembled through the facades would have exercised
    different code from the one that was wrong.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")
    (tree / "refunds.md").write_text("# Refunds\n\nThe prod procedure.\n", encoding="utf-8")
    store = tmp_path / "adopt.db"
    for environment in ("prod", "staging"):
        done = _run(
            "init",
            str(tree),
            "--scope",
            f"northwind/acme-erp/orders-api/{environment}",
            "--answers",
            str(answers),
            "--store",
            str(store),
            "--archetype",
            "web",
            "--json",
        )
        assert done.returncode == ExitCode.SUCCESS, done.stderr
    return store, tree


def _items(store: Path) -> list[tuple[str, str, int]]:
    """`(item_id, environment slug, revision count)` -- read from the file directly.

    Read with `sqlite3` rather than through `adopt_store`, so one defect cannot
    both write the wrong row and vouch for it.
    """
    import sqlite3

    connection = sqlite3.connect(store)
    try:
        return [
            (str(row[0]), str(row[1]), int(row[2]))
            for row in connection.execute(
                "SELECT ki.id, e.slug, COUNT(kr.id) FROM knowledge_item ki "
                "JOIN environment e ON e.id = ki.environment_id "
                "LEFT JOIN knowledge_revision kr ON kr.item_id = ki.id "
                "GROUP BY ki.id ORDER BY e.slug"
            )
        ]
    finally:
        connection.close()


@pytest.mark.unit
def test_a_staging_ingest_does_not_append_to_the_prod_item(
    two_environments: tuple[Path, Path],
) -> None:
    """B2-01, reproduced and closed.

    The `created: 1` assertion on the second run is the load-bearing one. Before
    T1.3 the second ingest reported `updated: 1`: it had found the prod item at
    the same relative path, decided the document had changed, and appended the
    staging body to the prod chain.
    """
    store, tree = two_environments

    first = _run(
        "ingest",
        str(tree / "refunds.md"),
        "--scope",
        "northwind/acme-erp/orders-api/prod",
        "--store",
        str(store),
        "--json",
    )
    assert first.returncode == ExitCode.SUCCESS, first.stderr
    assert json.loads(first.stdout)["created"] == 1

    (tree / "refunds.md").write_text("# Refunds\n\nThe STAGING procedure.\n", encoding="utf-8")
    second = _run(
        "ingest",
        str(tree / "refunds.md"),
        "--scope",
        "northwind/acme-erp/orders-api/staging",
        "--store",
        str(store),
        "--json",
    )

    assert second.returncode == ExitCode.SUCCESS, second.stderr
    payload = json.loads(second.stdout)
    assert payload["created"] == 1, "the staging ingest appended to the prod item"
    assert payload["updated"] == 0
    assert [entry[1:] for entry in _items(store)] == [("prod", 1), ("staging", 1)]


@pytest.mark.unit
def test_the_prod_chain_is_untouched_by_the_staging_ingest(
    two_environments: tuple[Path, Path],
) -> None:
    """The other half, asserted on the file rather than on the report.

    A payload saying `created: 1` would be satisfied by a run that created a
    staging item *and* appended to prod. One revision per item is the claim.
    """
    store, tree = two_environments
    for environment, body in (("prod", "prod procedure"), ("staging", "STAGING procedure")):
        (tree / "refunds.md").write_text(f"# Refunds\n\nThe {body}.\n", encoding="utf-8")
        done = _run(
            "ingest",
            str(tree / "refunds.md"),
            "--scope",
            f"northwind/acme-erp/orders-api/{environment}",
            "--store",
            str(store),
            "--json",
        )
        assert done.returncode == ExitCode.SUCCESS, done.stderr

    assert [(slug, revisions) for _, slug, revisions in _items(store)] == [
        ("prod", 1),
        ("staging", 1),
    ]


@pytest.mark.unit
def test_a_re_ingest_in_the_same_environment_is_still_idempotent(
    two_environments: tuple[Path, Path],
) -> None:
    """The control. Without it, a predicate that matched *nothing* would pass.

    Scope compatibility that was too narrow would look identical to the fix in
    every assertion above -- two items, one revision each -- while quietly
    turning every re-ingest into a duplicate.
    """
    store, tree = two_environments
    argv = (
        "ingest",
        str(tree / "refunds.md"),
        "--scope",
        "northwind/acme-erp/orders-api/prod",
        "--store",
        str(store),
        "--json",
    )
    assert _run(*argv).returncode == ExitCode.SUCCESS

    second = _run(*argv)

    assert second.returncode == ExitCode.SUCCESS, second.stderr
    payload = json.loads(second.stdout)
    assert payload["unchanged"] == 1
    assert payload["created"] == 0
    assert len(_items(store)) == 1


def _two_firms(store: SqliteStoreHandle) -> tuple[Scope, Scope]:
    facade = store.scope()
    scopes = []
    for firm_slug, engagement_slug, system_slug in (
        ("northwind", "acme-erp", "orders-api"),
        ("otherfirm", "eng", "sysc"),
    ):
        firm = facade.create_firm(slug=firm_slug, name=firm_slug)
        engagement = facade.create_engagement(
            firm_id=firm.id, slug=engagement_slug, name=engagement_slug
        )
        system = facade.create_system(
            engagement_id=engagement.id, slug=system_slug, name=system_slug
        )
        facade.create_environment(system_id=system.id, slug="prod", name="Production")
        scopes.append(facade.resolve(f"{firm_slug}/{engagement_slug}/{system_slug}/prod"))
    return scopes[0], scopes[1]


def _an_item(store: SqliteStoreHandle, scope: Scope) -> str:
    item_id, _ = store.items().record(
        scope=scope,
        kind="procedure",
        title="A procedure",
        body_md="Something true.",
        authority_class="human_confirmed",
    )
    return item_id


def _an_identity(store: SqliteStoreHandle, scope: Scope, key: str) -> str:
    return str(
        store.identities()
        .observe(
            scope=scope,
            kind="endpoint",
            namespace=None,
            key=key,
            extractor="test",
            extractor_version="1",
        )
        .id
    )


@pytest.mark.unit
def test_a_binding_across_two_firms_is_refused(s4_store: SqliteStoreHandle) -> None:
    """B2-06, at the one place every binding is created.

    *Fails when* `BindingFacade.create` stops comparing the two ends' scopes.
    The check is in the facade rather than in `commands/knowledge.py` because
    ingest's URI tier, harvest, `adopt bind`, a review confirmation and Build 6's
    rebind all arrive here -- a check in the command would guard one of five.
    """
    here, elsewhere = _two_firms(s4_store)
    item_id = _an_item(s4_store, here)
    identity_id = _an_identity(s4_store, elsewhere, "GET /v1/other")

    with pytest.raises(AdoptError) as refusal:
        s4_store.bindings().create(item_id=item_id, identity_id=identity_id, is_load_bearing=True)

    assert refusal.value.code == ErrorCode.SCOPE_VIOLATION
    assert refusal.value.exit_code == ExitCode.POLICY_REFUSAL


@pytest.mark.unit
def test_nothing_is_written_when_a_cross_scope_binding_is_refused(
    s4_store: SqliteStoreHandle,
) -> None:
    """The refusal is *before* the insert, not a rollback of one.

    An exit code says the call stopped; only a row count says it stopped before
    writing, and a check placed one line lower would satisfy the test above
    perfectly.
    """
    here, elsewhere = _two_firms(s4_store)
    item_id = _an_item(s4_store, here)
    identity_id = _an_identity(s4_store, elsewhere, "GET /v1/other")

    with pytest.raises(AdoptError):
        s4_store.bindings().create(item_id=item_id, identity_id=identity_id, is_load_bearing=True)

    assert s4_store.bindings().for_identity(identity_id) == ()


@pytest.mark.unit
def test_an_in_scope_binding_still_succeeds(s4_store: SqliteStoreHandle) -> None:
    """The control: a guard that refused everything would pass both rows above."""
    here, _ = _two_firms(s4_store)
    item_id = _an_item(s4_store, here)
    identity_id = _an_identity(s4_store, here, "GET /v1/orders")

    binding_id, revision_id = s4_store.bindings().create(
        item_id=item_id, identity_id=identity_id, is_load_bearing=True
    )

    assert binding_id and revision_id
    assert len(s4_store.bindings().for_identity(identity_id)) == 1


@pytest.mark.unit
def test_an_identity_view_and_an_item_agree_about_what_in_scope_means() -> None:
    """The predicate's own contract, stated where a reader will look for it.

    `identity.environment_id` is `NOT NULL` and `knowledge_item.environment_id`
    is nullable -- *"an item may span environments"* in the manifest -- so
    `identity_views` never had to answer what a NULL means and `in_scope` does.
    A spanning item is in scope for every environment of its system; excluding
    it would turn every re-ingest of one into a duplicate.
    """
    from adopt_cli.commands._knowledge_support import in_scope
    from adopt_model import KnowledgeItem
    from adopt_scope import ScopeNode

    def item(environment_id: str | None) -> KnowledgeItem:
        return KnowledgeItem.model_construct(
            id="ki_1", system_id="sys_1", environment_id=environment_id
        )

    firm = ScopeNode(id="firm_1", slug="northwind")
    prod = Scope(
        firm=firm,
        engagement=ScopeNode(id="eng_1", slug="acme-erp"),
        system=ScopeNode(id="sys_1", slug="orders-api"),
        environment=ScopeNode(id="env_prod", slug="prod"),
    )
    other_system = Scope(firm=firm, system=ScopeNode(id="sys_2", slug="other"))

    assert in_scope(item("env_prod"), prod) is True
    assert in_scope(item("env_staging"), prod) is False
    assert in_scope(item(None), prod) is True, "an item that spans environments is in every one"
    assert in_scope(item("env_prod"), other_system) is False
