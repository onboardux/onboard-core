"""The route extractors -- `05` S1.4 workstream A, contract C1.

**One table per framework, not one file per extractor.** `test-generation-discipline`
makes *extend* the default answer and a new file the exception: three frameworks
answering one question (what routes does this tree serve?) are one behaviour family
and read as a specification when they sit in one table.

The case that earns its own test is the **cross-framework** one, because it is the
only assertion here that no single extractor can satisfy alone.
"""

import time
from pathlib import Path

import pytest
from adopt_extractors_web import (
    DjangoRoutesExtractor,
    ExpressRoutesExtractor,
    FastapiRoutesExtractor,
    GraphqlExtractor,
    GrpcExtractor,
    IntegrationsExtractor,
    OpenapiExtractor,
)
from adopt_map.context import Budget, ExtractorContext
from adopt_map.fileindex import build_index
from adopt_map.minting import normalize_local_key
from adopt_map.schemas import Extractor

pytestmark = pytest.mark.unit

FIXTURE = Path("fixtures/repos/django-orders")


def context(root: Path) -> ExtractorContext:
    return ExtractorContext(
        root=str(root),
        index=build_index(root),
        budget=Budget.starting_at(time.time(), stage1_s=900.0, total_s=3600.0),
        archetype="web",
        tier="T2",
    )


def keys(extractor: Extractor, root: Path = FIXTURE) -> set[str]:
    """Every fact's key, **normalized** -- which is the form that reaches a URI."""
    return {
        normalize_local_key(fact.identity_kind, fact.local_key)
        for fact in extractor.extract(context(root))
    }


def tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------- #
# Per-framework recovery: one row per (extractor, expected key).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("extractor", "expected"),
    [
        (DjangoRoutesExtractor(), "GET /api/v1/orders"),
        (DjangoRoutesExtractor(), "DELETE /api/v1/orders/{order_id}"),
        # An `include()` prefix is applied, which is the whole reason the
        # extractor expands trees rather than reading each urlconf alone.
        (DjangoRoutesExtractor(), "POST /api/v1/billing/payments/{payment_id}/refund"),
        (OpenapiExtractor(), "GET /api/v1/orders/{order_id}"),
        (GraphqlExtractor(), "Query.orders"),
        (GraphqlExtractor(), "Mutation.cancelOrder"),
        (GrpcExtractor(), "orders.v1.OrderService.GetOrder"),
        (GrpcExtractor(), "orders.v1.ShipmentService.TrackShipment"),
        (IntegrationsExtractor(), "POST /billing.example.com/v1/charges"),
    ],
    ids=lambda value: value if isinstance(value, str) else type(value).__name__,
)
def test_a_declared_route_is_recovered(extractor: Extractor, expected: str) -> None:
    """*Defect sentence.* Fails when a framework's declaration syntax stops being
    recognised; matters because a missed route is an endpoint absent from the
    identity set, which every downstream build treats as "this system does not
    serve it"; no other instrument catches it because a run with fewer facts still
    exits 0 and reports a smaller number confidently."""
    assert expected in keys(extractor)


def test_a_route_with_no_declared_method_is_keyed_any_not_guessed() -> None:
    """`01` §1.6: silence beats guessing.

    Django's `path()` routes every method to its view, so `ANY` is a statement
    about the framework. Keying it `GET` would be an invention that also forks
    against an OpenAPI document that says `GET` for real.
    """
    assert "ANY /healthz" in keys(DjangoRoutesExtractor())


def test_an_unresolvable_include_mounts_the_prefix_and_invents_nothing() -> None:
    """A third-party urlconf is not in the tree, so its routes are not ours to
    imagine -- but the mount point is real and is recorded."""
    recovered = keys(DjangoRoutesExtractor())
    assert "ANY /admin" in recovered
    assert not any(key.endswith("/admin/login") for key in recovered)


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (
            {
                "api/main.py": (
                    "from fastapi import APIRouter, FastAPI\n"
                    "app = FastAPI()\n"
                    'router = APIRouter(prefix="/api/v2")\n'
                    '@app.get("/health")\n'
                    "def health():\n    return None\n"
                    '@router.post("/widgets")\n'
                    "def create_widget():\n    return None\n"
                ),
            },
            {"GET /health", "POST /api/v2/widgets"},
        ),
        (
            {
                "web/routes.js": (
                    "const express = require('express');\n"
                    "const router = express.Router();\n"
                    "router.get('/widgets', listWidgets);\n"
                    "router.delete('/widgets/:id', removeWidget);\n"
                    "router.use('/static', serveStatic);\n"
                ),
            },
            {"GET /widgets", "DELETE /widgets/{id}"},
        ),
    ],
    ids=["fastapi-router-prefix", "express-verbs-and-params"],
)
def test_framework_route_syntax(tmp_path: Path, files: dict[str, str], expected: set[str]) -> None:
    """*Defect sentence.* Fails when a router prefix stops being applied or a
    non-route call (`app.use`, `@app.middleware`) starts minting; matters because
    the first understates every path under a router and the second fills the
    endpoint set with things no caller addresses; no other instrument catches it
    because both produce a plausible-looking count."""
    root = tree(tmp_path, files)
    recovered = keys(FastapiRoutesExtractor(), root) | keys(ExpressRoutesExtractor(), root)
    assert expected <= recovered
    assert not any("static" in key for key in recovered)


# --------------------------------------------------------------------------- #
# C1: two frameworks describing one endpoint mint one URI.
# --------------------------------------------------------------------------- #


def test_two_frameworks_describing_one_endpoint_agree_on_the_key() -> None:
    """`02` §3.1 rule 3 and contract C1 -- **the assertion no single extractor
    can make**.

    *Defect sentence.* Fails when a route extractor and the OpenAPI extractor
    disagree about the normalized key for one endpoint; matters because the
    identity forks, the same endpoint is minted twice under two URIs and every
    coverage figure double-counts it; no other instrument catches it because each
    extractor is individually correct and the run reports a larger, confident
    number.

    This is the case that found **B1-CR-66**: Django strips the leading slash
    before matching, so `path("api/v1/orders/")` genuinely has none while an
    OpenAPI `paths` key always does, and `02` §3.2 rule 4 canonicalized every
    slash except that one.
    """
    documented = {key for key in keys(OpenapiExtractor()) if " " in key}
    served = {key for key in keys(DjangoRoutesExtractor()) if " " in key}

    assert documented, "the fixture's OpenAPI document declares operations"
    assert documented <= served, (
        "every documented operation must mint the key its Django route mints; "
        f"forked: {sorted(documented - served)}"
    )
