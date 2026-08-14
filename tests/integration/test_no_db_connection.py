"""A default run opens no database connection -- `05` S1.4 workstream B.

`05` S1.4 gates live schema reflection behind `--db-url` **and** a tier, and asks
for *"a test proving the default run opens no database connection"*.

**Asserted through the egress guard rather than by inspecting the extractors.**
`adopt_map.netguard` denies at the socket, which is the last common point before
the kernel, and that is the only place an assertion covers *every* route to a
database -- including a library reaching a transport for a reason the import
allowlist permits. A test that scanned the extractors for `create_engine` would
pass while a dependency dialled out.

**The stronger half is structural and is asserted too**: `MetaData.reflect()`
needs a live `Engine`, so `web.sqlalchemy.schema` cannot open a connection because
it holds no SQLAlchemy at all (B1-CR-65). The socket assertion is what keeps that
true after somebody adds the live arm.
"""

import time
from pathlib import Path

import pytest
from adopt_extractors_web import pack
from adopt_map.context import Budget, ExtractorContext
from adopt_map.fileindex import build_index
from adopt_map.netguard import EgressGuard, guarded

pytestmark = [pytest.mark.integration, pytest.mark.unit]

FIXTURE = Path("fixtures/repos/django-orders")

#: Ports a client database listens on. Named so a failure says *which* service the
#: run reached rather than "a socket was opened".
DATABASE_PORTS = {5432: "postgresql", 3306: "mysql", 1433: "mssql", 1521: "oracle"}


def test_a_default_run_opens_no_socket_at_all() -> None:
    """*Defect sentence.* Fails when any web extractor opens a network connection
    on the default path; matters because `01` F8.1 permits a live database read
    only under `--db-url` and a permitting tier, and a client security reviewer is
    told the deterministic pass completes with the network down; no other
    instrument catches it because a connection that succeeds produces *better*
    facts and no error."""
    ctx = ExtractorContext(
        root=str(FIXTURE),
        index=build_index(FIXTURE),
        budget=Budget.starting_at(time.time(), stage1_s=900.0, total_s=3600.0),
        archetype="web",
        tier="T2",
    )
    guard = EgressGuard(strict=True)
    with guarded(guard):
        for extractor in pack():
            with guard.attributed_to(extractor.manifest().id):
                list(extractor.extract(ctx))

    assert guard.attempted == 0, "a default run attempted egress: " + ", ".join(
        f"{attempt.extractor} -> {attempt.host}:{attempt.port}"
        + (f" ({DATABASE_PORTS[attempt.port]})" if attempt.port in DATABASE_PORTS else "")
        for attempt in guard.attempts
    )


def test_the_schema_extractor_holds_no_database_driver() -> None:
    """The structural half: it cannot connect because it imports nothing that can.

    *Defect sentence.* Fails when `web.sqlalchemy.schema` gains a SQLAlchemy or
    driver import; matters because `MetaData` populates only from a live `Engine`
    or by importing client models, so an import here is the first step of one of
    the two things `02` §7 obligation 1 forbids; no other instrument catches it
    because the import alone opens nothing and the socket test stays green.
    """
    import inspect

    from adopt_extractors_web.sqlalchemy_schema import SqlalchemySchemaExtractor

    module = inspect.getmodule(SqlalchemySchemaExtractor())
    assert module is not None and module.__file__ is not None
    source = Path(module.__file__).read_text(encoding="utf-8")
    for banned in ("import sqlalchemy", "from sqlalchemy", "psycopg", "pymysql", "create_engine"):
        assert banned not in source, f"{banned!r} reaches a live database"
