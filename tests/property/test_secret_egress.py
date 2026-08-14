"""Planted-secret egress -- contracts §10 C16; PRD N9; `03` §7.

One of **the five instruments that survive any budget cut** (`05` Quality notes).
It is a property rather than an example set because the claim is universal: *no
secret value reaches the store, an artifact, a log or the report* -- and an
example set can only ever say "not these secrets".

**Two halves, and this is the empirical one.** The structural half is
`test_surface_schemas.test_a_secret_reference_has_nowhere_to_put_a_value`: the
`secret:*` attribute model declares `source` and `name` and no third field, so
there is nowhere to put a secret. This half plants secrets across every field an
extractor *can* populate and asserts none of them arrives in the store. Keeping
both matters: the structural half cannot see a secret smuggled through a `title`
or a `prose` block, and the empirical half cannot see a `value` field added
tomorrow to a model no fixture happens to exercise.
"""

from pathlib import Path

import pytest
from adopt_extractors_common import MANIFEST
from adopt_map.schemas import SourceRef, SurfaceFact
from adopt_map.scope_resolve import resolve_scope
from adopt_map.writer import SurfaceWriter
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adopt_model import MODEL_FOR_TABLE
from adopt_obs import AdoptError
from adopt_scope import Scope, ScopeNode
from adopt_store import open_store
from tests.build1_conftest import surface_writer_for

pytestmark = pytest.mark.property

#: Shapes a real credential takes. Not random strings: a random string would
#: almost never collide with anything, so the test would pass by luck. These are
#: the literal shapes a `.env`, a Vault response and a cloud key take.
_SECRETS = st.sampled_from(
    [
        "hunter2-correct-horse",
        "sk-live-51H8yQ2eZvKYlo2C",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        "-----BEGIN RSA PRIVATE KEY-----MIIEow",
        "postgres://user:s3cr3t@db.internal:5432/orders",
    ]
)


def _store_text(root: Path) -> str:
    """Every string value in every canonical table, concatenated.

    Read through the export port rather than by grepping the file, because a
    SQLite file contains freed pages that may hold text no row references -- a
    file grep would fail on data the store cannot return and nothing can leak.
    """
    chunks: list[str] = []
    with open_store(root / "store.db", read_only=True) as handle:
        records = handle.export_records()
        for table, model in MODEL_FOR_TABLE.items():
            for row in records.table_rows(table, model):
                chunks.append(str(row.model_dump(mode="json")))
    return "\n".join(chunks)


def _prepare(root: Path) -> tuple[SurfaceWriter, object, Path]:
    root.mkdir(parents=True, exist_ok=True)
    handle = open_store(root / "store.db", migrate=True)
    facade = handle.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    environment = facade.create_environment(system_id=system.id, slug="prod", name="Production")
    handle.boundary().declare(
        scope=Scope(
            firm=ScopeNode(id=firm.id, slug=firm.slug),
            system=ScopeNode(id=system.id, slug=system.slug),
        ),
        tier="T2",
        knowledge_plane_location="customer",
        control_plane_location="customer",
        permitted_outbound_categories=["metadata_only"],
    )
    resolved = resolve_scope(
        handle.export_records(),
        firm_id=firm.id,
        engagement_id=engagement.id,
        system_id=system.id,
        environment_id=environment.id,
        archetype="web",
        tier="T2",
    )
    writer = surface_writer_for(handle)
    return writer, resolved, root


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(secret=_SECRETS)
def test_a_secret_planted_in_a_secret_reference_never_reaches_the_store(
    tmp_path_factory: pytest.TempPathFactory, secret: str
) -> None:
    """*Fails when* the `secret:*` model gains a field that can carry a value.

    *Matters because* PRD N9 is *"zero secret values in store, outputs, logs or
    report"*, and a `config_key` under `secret:*` is the **one kind whose whole
    purpose is to name a credential without holding it**. *No other instrument
    catches it because* a leaked value here is a perfectly ordinary string in a
    perfectly ordinary column, and no schema check would object.
    """
    writer, resolved, root = _prepare(tmp_path_factory.mktemp("secret"))

    # Every field an extractor controls, each carrying the planted secret. The
    # attributes must be refused; the rest must simply never carry it onward.
    hostile = SurfaceFact(
        identity_kind="config_key",
        namespace="secret:vault",
        local_key="orders/db_password",
        title="orders/db_password",
        attributes={"source": "vault", "name": "orders/db_password", "value": secret},
        source_refs=[SourceRef(path="config/settings.py", start_line=1)],
        prose=None,
    )

    with pytest.raises(AdoptError):
        writer.write_run(
            resolved=resolved,  # type: ignore[arg-type]
            manifest=MANIFEST,
            facts=[hostile],
            vcs_revision="abc123",
        )

    assert secret not in _store_text(root)


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(secret=_SECRETS)
def test_a_secret_smuggled_through_a_free_text_field_is_the_extractors_defect_not_a_leak_path(
    tmp_path_factory: pytest.TempPathFactory, secret: str
) -> None:
    """*Fails when* a free-text field silently becomes a credential channel.

    `title` and `prose` are free text by design and an extractor that puts a
    secret in one has a defect the framework cannot detect -- so this test
    asserts the **honest** boundary rather than a false guarantee: the value is
    written, and it is written **only** where the extractor put it, never copied
    into `provenance.source_ref`, `identity.local_key` or the audit summary.

    *Matters because* the interesting failure is not "an extractor misbehaved";
    it is "one misbehaving field contaminated four tables". *No other instrument
    catches it because* every copy would be individually well-formed.
    """
    writer, resolved, root = _prepare(tmp_path_factory.mktemp("smuggle"))

    hostile = SurfaceFact(
        identity_kind="config_key",
        namespace="django",
        local_key="DATABASES.default.PASSWORD",
        title=secret,
        attributes={"key_path": "DATABASES.default.PASSWORD"},
        source_refs=[SourceRef(path="config/settings.py", start_line=1)],
        prose=secret,
    )
    writer.write_run(
        resolved=resolved,  # type: ignore[arg-type]
        manifest=MANIFEST,
        facts=[hostile],
        vcs_revision="abc123",
    )

    with open_store(root / "store.db", read_only=True) as handle:
        records = handle.export_records()
        for table, model in MODEL_FOR_TABLE.items():
            if table in {"knowledge_item", "knowledge_revision"}:
                continue  # where the extractor deliberately put it
            for row in records.table_rows(table, model):
                assert secret not in str(row.model_dump(mode="json")), (
                    f"the planted secret reached {table}, which the extractor never wrote to"
                )
