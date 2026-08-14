"""`adopt-extractors-web` -- the web/service pack (`03` §5.10, `05` S1.4).

Eleven extractors over one archetype, normalizing into the same thirteen-kind
vocabulary every other pack uses. **The pack's contract is the shape, not the
framework**: a Django route, a FastAPI route and an OpenAPI operation describing
one endpoint mint one URI, which is `02` §3.1 rule 3 and the reason `_routes.py`
exists.

**None of them imports the framework it reads.** Django's own resolver, FastAPI's
own app object and SQLAlchemy's own `MetaData` would each give better answers and
each requires executing client code to get them (`02` §7 obligation 1). So every
extractor here is a reader of text through a grammar, and the `poisoned-import`
fixture is the standing proof that it stayed that way.
"""

from adopt_map.schemas import Extractor

from adopt_extractors_web.celery_jobs import CeleryJobsExtractor
from adopt_extractors_web.cron import CronExtractor
from adopt_extractors_web.django_routes import DjangoRoutesExtractor
from adopt_extractors_web.express_routes import ExpressRoutesExtractor
from adopt_extractors_web.fastapi_routes import FastapiRoutesExtractor
from adopt_extractors_web.graphql_schema import GraphqlExtractor
from adopt_extractors_web.grpc import GrpcExtractor
from adopt_extractors_web.integrations import IntegrationsExtractor
from adopt_extractors_web.migrations import MigrationsExtractor
from adopt_extractors_web.openapi import OpenapiExtractor
from adopt_extractors_web.sqlalchemy_schema import SqlalchemySchemaExtractor

__all__ = [
    "CeleryJobsExtractor",
    "CronExtractor",
    "DjangoRoutesExtractor",
    "ExpressRoutesExtractor",
    "FastapiRoutesExtractor",
    "GraphqlExtractor",
    "GrpcExtractor",
    "IntegrationsExtractor",
    "MigrationsExtractor",
    "OpenapiExtractor",
    "SqlalchemySchemaExtractor",
    "pack",
]


def pack() -> tuple[Extractor, ...]:
    """Every `web` extractor a real run may use, in manifest-id order.

    Ordered here as well as in the registry so a reader of this list and a reader
    of the run plan see the same sequence -- `02` §7 obligation 3 is an extractor
    obligation, and the pack keeping its own half of it costs one `sorted`.
    """
    return (
        CeleryJobsExtractor(),
        CronExtractor(),
        DjangoRoutesExtractor(),
        ExpressRoutesExtractor(),
        FastapiRoutesExtractor(),
        GraphqlExtractor(),
        GrpcExtractor(),
        IntegrationsExtractor(),
        MigrationsExtractor(),
        OpenapiExtractor(),
        SqlalchemySchemaExtractor(),
    )
