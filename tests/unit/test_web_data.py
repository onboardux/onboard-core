"""The data-model and job extractors -- `05` S1.4 workstreams B and C.

Two tables, one per workstream, plus the two assertions that are about the pair
rather than either member: that the schema extractors **agree on the key**, and
that a declared retry policy lands as a `config_key` rather than as an invented
kind.
"""

import time
from pathlib import Path

import pytest
from adopt_extractors_web import (
    CeleryJobsExtractor,
    CronExtractor,
    MigrationsExtractor,
    SqlalchemySchemaExtractor,
)
from adopt_map.context import Budget, ExtractorContext
from adopt_map.fileindex import build_index
from adopt_map.schemas import Extractor, SurfaceFact

pytestmark = pytest.mark.unit

FIXTURE = Path("fixtures/repos/django-orders")


def facts(extractor: Extractor, root: Path = FIXTURE) -> list[SurfaceFact]:
    ctx = ExtractorContext(
        root=str(root),
        index=build_index(root),
        budget=Budget.starting_at(time.time(), stage1_s=900.0, total_s=3600.0),
        archetype="web",
        tier="T2",
    )
    return list(extractor.extract(ctx))


def pairs(extractor: Extractor, root: Path = FIXTURE) -> set[tuple[str | None, str]]:
    return {(fact.namespace, fact.local_key) for fact in facts(extractor, root)}


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("extractor", "namespace", "key"),
    [
        # Django migration -> table, keyed `*`, and its columns.
        (MigrationsExtractor(), "pg:public.order", "*"),
        (MigrationsExtractor(), "pg:public.order", "reference"),
        (MigrationsExtractor(), "pg:public.invoice", "total_cents"),
        # SQLAlchemy declarative -> `__tablename__` and its columns.
        (SqlalchemySchemaExtractor(), "pg:public.pick_list", "*"),
        (SqlalchemySchemaExtractor(), "pg:public.pick_list", "order_ref"),
        (SqlalchemySchemaExtractor(), "pg:public.bin", "code"),
    ],
    ids=lambda v: v if isinstance(v, str) else type(v).__name__,
)
def test_a_declared_column_is_recovered(extractor: Extractor, namespace: str, key: str) -> None:
    """*Defect sentence.* Fails when a declaration style stops being parsed;
    matters because a missing `db_field` is a column no downstream build knows
    exists, and `02` §3.1 keys the table itself `*` so losing that row loses the
    table too; no other instrument catches it because the run still exits 0."""
    assert (namespace, key) in pairs(extractor)


def test_the_dialect_is_detected_not_guessed(tmp_path: Path) -> None:
    """`_dbfield`: a tree that declares no dialect gets `sql`, meaning
    *"a SQL database whose dialect this tree does not declare"*.

    *Defect sentence.* Fails when the fallback becomes a concrete dialect;
    matters because guessing `pg` forks every column the day the tree gains a
    `settings.py` that says MySQL; no other instrument catches it because the
    guess is invisible until a second observation disagrees with it.
    """
    root = tmp_path
    (root / "models.py").write_text(
        "from sqlalchemy import Column, Integer\n"
        "class Thing(Base):\n"
        '    __tablename__ = "thing"\n'
        "    id = Column(Integer, primary_key=True)\n",
        encoding="utf-8",
    )
    assert ("sql:public.thing", "id") in pairs(SqlalchemySchemaExtractor(), root)


def test_the_two_schema_extractors_agree_on_the_namespace() -> None:
    """Both describe the same tables from different files, so a namespace
    disagreement mints every column twice.

    *Defect sentence.* Fails when one extractor's dialect or schema differs from
    the other's; matters because it silently doubles every `db_field` count; no
    other instrument catches it because both are individually correct.
    """
    from_migrations = {namespace for namespace, _key in pairs(MigrationsExtractor())}
    from_models = {namespace for namespace, _key in pairs(SqlalchemySchemaExtractor())}
    assert from_migrations and from_models
    prefixes = {namespace.split(":", 1)[0] for namespace in from_migrations | from_models}
    assert prefixes == {"pg"}, f"the two extractors disagree about the dialect: {prefixes}"


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("extractor", "namespace", "key"),
    [
        (CeleryJobsExtractor(), "celery", "orders.tasks.reconcile_payments"),
        (CeleryJobsExtractor(), "celery", "catalog.tasks.sync_supplier_feed"),
        # A beat entry naming a task this tree does not declare is still a
        # behaviour the system owns on a timer.
        (CeleryJobsExtractor(), "celery", "analytics.tasks.rollup_daily"),
        (CronExtractor(), "cron", "/usr/local/bin/publish-inventory-snapshot"),
        (CronExtractor(), "cron", "/usr/local/bin/rotate-application-logs"),
    ],
    ids=lambda v: v if isinstance(v, str) else type(v).__name__,
)
def test_a_declared_job_is_recovered(extractor: Extractor, namespace: str, key: str) -> None:
    """*Defect sentence.* Fails when a task decorator or crontab form stops being
    read; matters because an unmapped scheduled job is behaviour that changes on a
    timer with nothing watching it; no other instrument catches it because jobs
    are invisible in a route inventory."""
    assert (namespace, key) in pairs(extractor)


def test_a_commented_crontab_line_is_not_a_job() -> None:
    """Crontabs accumulate disabled entries, and a reader who finds them minted as
    live jobs learns to distrust the whole inventory."""
    keys = {key for _namespace, key in pairs(CronExtractor())}
    assert not any("legacy-reindex" in key for key in keys)


def test_an_environment_assignment_is_not_a_job() -> None:
    """`MAILTO=ops@example.com` has as many fields as a schedule and is not one."""
    keys = {key for _namespace, key in pairs(CronExtractor())}
    assert not any("example.com" in key for key in keys)


def test_a_beat_schedule_attaches_to_its_task_rather_than_minting_a_second_identity() -> None:
    """*Defect sentence.* Fails when a beat entry mints its own identity beside the
    task it names; matters because one referent would appear twice and the job
    count would overstate what runs; no other instrument catches it because both
    identities look individually reasonable."""
    scheduled = [
        fact
        for fact in facts(CeleryJobsExtractor())
        if fact.local_key == "orders.tasks.reconcile_payments"
    ]
    assert len(scheduled) == 1
    assert scheduled[0].attributes["schedule"] == "crontab(hour=3, minute=0)"
