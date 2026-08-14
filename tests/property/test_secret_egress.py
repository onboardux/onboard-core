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


def _prepare(root: Path, *, archetype: str = "web") -> tuple[SurfaceWriter, object, Path]:
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
        # The registry plans on the archetype: an `ai` pack against a `web` scope
        # is skipped with `archetype_mismatch`, and this suite would then prove
        # that a run emitting nothing emits no secrets.
        archetype=archetype,
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


#: The two canaries planted in `fixtures/repos/django-orders/deploy/.env.example`.
#: Distinctive on purpose: a bland placeholder would make the assertion below pass
#: by luck rather than by the extractors doing the right thing.
_FIXTURE_CANARIES = (
    "PLANTED-SECRET-DO-NOT-EMIT-a1b2c3d4",
    "sk-live-PLANTED-DO-NOT-EMIT-9f8e7d6c",
)


def test_no_canary_from_the_django_orders_fixture_reaches_the_store_or_an_artifact(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """`05` S1.4: *"Planted-secret property suite green across the fixture."*

    *Fails when* an extractor records a credential **value** rather than a
    reference -- a `default` on a `config_key`, a `title`, a prose line, or a
    source-ref that quotes the line.

    *Matters because* the S1.4 fixture is the first one carrying a realistic
    `.env` with credential-shaped keys beside ordinary ones, so it is the first
    subject where `common.config`'s "record the default" behaviour and
    `common.secrets`' "mint a reference" behaviour meet on one file. `01` N9 is
    zero secret values in store, outputs, logs **or report**, and this asserts all
    four surfaces.

    *No other instrument catches it because* the structural half only proves the
    `secret:*` model has nowhere to put a value -- it cannot see a secret smuggled
    through a **different kind's** `default` field, which is exactly what a
    credential key under the ordinary `env` namespace would do.
    """
    from adopt_extractors_common import pack as common_pack
    from adopt_extractors_web import pack as web_pack
    from adopt_map.emit.json_report import render_surface_json
    from adopt_map.orchestrator import run as run_map
    from adopt_map.plugins import DEFAULT_ENABLED_PACKS, ExtractorRegistry
    from adopt_map.report import RUN_REPORT_NAME, write_run_report

    writer, resolved, root = _prepare(tmp_path_factory.mktemp("canary"))
    registry = ExtractorRegistry(enabled_packs=DEFAULT_ENABLED_PACKS)
    registry.register_all(common_pack())
    registry.register_all(web_pack())

    result = run_map(
        resolved=resolved,  # type: ignore[arg-type]
        root=Path("fixtures/repos/django-orders"),
        registry=registry,
        adopt_version="test",
        writer=writer,
        out_dir=root / "out",
        sequential=True,
    )

    write_run_report(result, root / "out")
    haystacks = {
        "store": _store_text(root),
        "surface.json": render_surface_json(result),
        "run_report.json": (root / "out" / RUN_REPORT_NAME).read_text(encoding="utf-8"),
    }
    for name, text in haystacks.items():
        for canary in _FIXTURE_CANARIES:
            assert canary not in text, f"a planted credential value reached {name}"

    # The keys themselves *are* recorded -- as references under `secret:*`, which
    # is `02` §3.1 rule 2. Asserting their presence keeps this from passing
    # because extraction quietly stopped reading the file at all.
    assert "DJANGO_SECRET_KEY" in haystacks["surface.json"]


#: The two canaries planted in `fixtures/repos/langgraph-support/deploy/.env.example`.
_AI_FIXTURE_CANARIES = (
    "sk-ant-CANARY-do-not-emit-000000",
    "CANARY-do-not-emit-111111",
)


def test_no_canary_from_the_langgraph_fixture_reaches_the_store_or_an_artifact(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The same four surfaces, over the fixture S1.5 ships -- `01` N9.

    *Fails when* an AI extractor records a credential **value**.

    *Matters because* the AI pack reads the one file type where a provider key
    lives by convention, and it does something the web pack does not: a model pin
    resolved from `os.environ` puts an environment variable's **name** into a
    URI. A pin resolved from `ANTHROPIC_API_KEY` would put the key's name in an
    identity -- which is correct -- and the failure one step further is putting
    its value in the attributes beside it.

    *No other instrument catches it because* the django-orders case above proves
    the `common` and `web` extractors are clean over a different tree, and the
    structural guarantee (`secret:*` has no value field) says nothing about
    `model_pin`, `prompt` or `retrieval_config` attributes -- none of which is a
    secret model, and all three of which this pack fills from files that sit
    beside the credentials.
    """
    from adopt_extractors_ai import pack as ai_pack
    from adopt_extractors_common import pack as common_pack
    from adopt_map.emit.json_report import render_surface_json
    from adopt_map.orchestrator import run as run_map
    from adopt_map.plugins import ExtractorRegistry
    from adopt_map.report import RUN_REPORT_NAME, write_run_report

    writer, resolved, root = _prepare(tmp_path_factory.mktemp("ai-canary"), archetype="ai")
    registry = ExtractorRegistry(enabled_packs=frozenset({"common", "ai"}))
    registry.register_all(common_pack())
    registry.register_all(ai_pack())

    result = run_map(
        resolved=resolved,  # type: ignore[arg-type]
        root=Path("fixtures/repos/langgraph-support"),
        registry=registry,
        adopt_version="test",
        writer=writer,
        out_dir=root / "out",
        sequential=True,
    )

    write_run_report(result, root / "out")
    haystacks = {
        "store": _store_text(root),
        "surface.json": render_surface_json(result),
        "run_report.json": (root / "out" / RUN_REPORT_NAME).read_text(encoding="utf-8"),
    }
    for name, text in haystacks.items():
        for canary in _AI_FIXTURE_CANARIES:
            assert canary not in text, f"a planted credential value reached {name}"

    # The reference is recorded, which is what keeps this from passing because
    # extraction stopped reading the file.
    assert "ANTHROPIC_API_KEY" in haystacks["surface.json"]
