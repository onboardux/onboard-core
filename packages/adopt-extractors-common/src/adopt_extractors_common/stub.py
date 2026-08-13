"""`common.stub` -- a deliberately trivial extractor, and the point of it.

`05` S1.1 asks for *"a deliberately trivial extractor emitting a fixed handful of
facts across three kinds, so the write path is provable before any real
extraction exists"*. That ordering is the sprint's whole argument: against the v3
substrate the long pole is the **write path** -- append-only revisions, canonical
URIs, mandatory scope, environment separation -- not extraction breadth, and
getting the write path wrong silently is far more expensive than a missing
extractor. So the write path is built and proven first, against facts that cannot
themselves be wrong.

It emits across **three kinds** on purpose (`endpoint`, `config_key`, `symbol`):
one whose key takes the path normalization rules, one whose namespace can carry
the `secret:*` routing, and one whose punctuation is data. A single-kind stub
would prove the write path for one shape of key.

**It reads nothing and executes nothing.** `applies_to` returns `True` for any
tree and `extract` ignores its argument, which makes it inert with respect to the
static-only guarantee (`02` §7 obligation 1) rather than merely compliant with it.
"""

from collections.abc import Iterator
from typing import Final

from adopt_map.schemas import ExtractorManifest, FactRelation, SourceRef, SurfaceFact

__all__ = ["MANIFEST", "StubExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="common.stub",
    version="1.0.0",
    pack="common",
    archetypes=["web", "platform", "lowcode", "data", "ai"],
    kinds=["endpoint", "config_key", "symbol"],
    # `grammar`, so the framework assigns `MAP_CONF_GRAMMAR` and the facts land
    # above `MAP_MIN_EMIT_CONFIDENCE`. A stub emitting below the floor would
    # prove the gap path and leave the write path unexercised.
    method="grammar",
)

#: The fixed handful. Ordered, because `02` §7 obligation 3 makes determinism an
#: extractor obligation and a set or dict iteration would satisfy it by accident
#: today and stop satisfying it on a different interpreter.
_FACTS: Final[tuple[SurfaceFact, ...]] = (
    SurfaceFact(
        identity_kind="endpoint",
        namespace="http",
        # Deliberately un-normalized: a `<int:id>` parameter, a doubled slash and
        # a trailing slash, so a run through this stub exercises §3.2 rules 2 and
        # 4 rather than asserting them only in a unit test.
        local_key="get //api/v1//orders/<int:id>/",
        title="GET /api/v1/orders/{id}",
        attributes={"http_method": "GET", "path": "/api/v1/orders/{id}", "framework": "stub"},
        relations=[
            FactRelation(
                predicate="handled_by",
                target_kind="symbol",
                target_namespace="python",
                target_local_key="orders.views.OrderDetailView.get",
            )
        ],
        source_refs=[SourceRef(path="orders/urls.py", start_line=12, end_line=14)],
        prose="Returns a single order. Handled by `OrderDetailView.get`.",
    ),
    SurfaceFact(
        identity_kind="symbol",
        namespace="python",
        local_key="orders.views.OrderDetailView.get",
        title="OrderDetailView.get",
        attributes={"signature": "get(self, request, id)", "return_type": "HttpResponse"},
        source_refs=[SourceRef(path="orders/views.py", start_line=44, end_line=52)],
    ),
    SurfaceFact(
        identity_kind="config_key",
        namespace="django",
        local_key="DATABASES.default.CONN_MAX_AGE",
        title="DATABASES.default.CONN_MAX_AGE",
        attributes={"key_path": "DATABASES.default.CONN_MAX_AGE", "value_type": "int"},
        source_refs=[SourceRef(path="config/settings.py", start_line=88)],
    ),
    SurfaceFact(
        identity_kind="config_key",
        # The `secret:*` routing: this fact's attributes validate against the
        # model with **no value field**, so the stub cannot emit a secret even
        # if someone later edits it carelessly.
        namespace="secret:env",
        local_key="DATABASE_PASSWORD",
        title="DATABASE_PASSWORD",
        attributes={"source": "env", "name": "DATABASE_PASSWORD"},
        source_refs=[SourceRef(path="config/settings.py", start_line=91)],
    ),
)


class StubExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        """Always. The stub describes no real tree, so no tree excludes it."""
        del root
        return True

    def extract(self, root: str) -> Iterator[SurfaceFact]:
        """The fixed facts, in a fixed order. Reads nothing under `root`."""
        del root
        yield from _FACTS
