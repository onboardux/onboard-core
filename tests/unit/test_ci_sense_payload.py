"""What `adopt ci-sense` posts, pinned — and what it must never post.

*Fails when* the payload's shape drifts, when it stops being a pure function of
what was found, or when a line of the customer's source reaches it. *Matters
because* two repositories read this shape and only one of them emits it:
`adopt-cli` builds it, `plane-freshness` parses it, and `adopt-cli` is
deliberately not a plane dependency. Nothing links the two but the committed
fixture, so without a byte-for-byte pin here and a matching one in the plane the
two agree only by accident, and disagree silently the first time a field moves.
*No other instrument catches it because* both sides keep working alone: the CLI
posts happily, the plane parses what it recognises and ignores what it does not,
and the missing field becomes an identity nobody classified.

The privacy assertion is the one that would be expensive to learn late. The
whole argument for sensing in the customer's CI is that their source never
leaves the network; a payload that carried file contents would keep every other
promise and break that one, and it would break it for every customer at once.
"""

import datetime as _dt
from pathlib import Path
from typing import Final

import pytest
from adopt_map import MapReport, SourceTree
from adopt_map.filestate import FileState
from adopt_map.observation import Observation, Span
from adopt_map.observe import Pack, observe_tree

from adopt_cli.commands._ci_sense_probes import ProbeSection
from adopt_cli.commands._ci_sense_support import (
    PAYLOAD_VERSION,
    build_payload,
    render_payload,
)
from adopt_scope import Scope, ScopeNode

pytestmark = pytest.mark.unit

_RUN: Final[str] = "run_01JCISENSEFIXTURE0000000"
_AT: Final[_dt.datetime] = _dt.datetime(2026, 8, 28, 12, 0, 0, tzinfo=_dt.UTC)
_FIXTURE: Final[Path] = Path(__file__).resolve().parents[1] / "fixtures" / "ci_sense_payload.json"

# The secret the tree contains, so the privacy assertion has something real to
# look for. A payload that carried file bodies would carry this.
_SECRET: Final[str] = "super-secret-connection-string-do-not-transmit"


class _Endpoints:
    """A stand-in extractor, so the fixture does not move when a pack does.

    The real packs are exercised by Build 1's own tests and by the reference
    journeys. Pinning the *payload shape* against eleven real extractors would
    make this fixture change every time an unrelated extractor learned a new
    attribute, and a fixture that churns is one people regenerate without
    reading.
    """

    name = "test.endpoints"
    version = "3"

    def extract(self, tree: SourceTree) -> "list[Observation]":
        return [
            Observation(
                kind="endpoint",
                key=("POST /v1/orders",),
                namespace=None,
                attributes={"method": "POST", "path": "/v1/orders", "params": ["sku", "qty"]},
                span=Span(path="app.py", start_line=10, end_line=18),
            ),
            Observation(
                kind="config_key",
                key=("DATABASE_URL",),
                namespace="env",
                attributes={"name": "DATABASE_URL", "type": "str", "default": None},
                span=Span(path="settings.py", start_line=3, end_line=3),
            ),
        ]


@pytest.fixture
def scope() -> Scope:
    return Scope(
        firm=ScopeNode(id="f1", slug="northwind"),
        engagement=ScopeNode(id="e1", slug="acme-erp"),
        system=ScopeNode(id="s1", slug="orders-api"),
        environment=ScopeNode(id="v1", slug="prod"),
    )


@pytest.fixture
def tree(tmp_path: Path) -> SourceTree:
    (tmp_path / "app.py").write_text("# endpoints\n", encoding="utf-8")
    (tmp_path / "settings.py").write_text(f'DATABASE_URL = "{_SECRET}"\n', encoding="utf-8")
    return SourceTree.scan(tmp_path)


def _probe_section() -> ProbeSection:
    """One completed probe, as the payload carries it. Build 8 S8.3.

    **Hand-built rather than executed**, because this file pins a *rendering*
    and executing a probe needs a socket and a store. The shape is kept honest
    by `test_the_rendered_probe_result_has_the_shape_the_producer_builds`, which
    compares these keys against what `run_payload_probes` actually produces
    against a real loopback server -- so a field added to one and not the other
    fails rather than drifting into the fixture unnoticed.
    """
    section = ProbeSection(ran=True)
    section.results.append(
        {
            "probe": "orders-checkout",
            "probe_definition_id": "prb_01JCISENSEFIXTUREPROBE00",
            "probe_definition_revision_id": "pdrev_01JCISENSEFIXTUREREV0000",
            "outcome": "success",
            "exercises": [
                "onboard-v1://northwind/acme-erp/orders-api/prod/endpoint/-/POST%20%2Fv1%2Forders"
            ],
            "outputs": ['{"body":"{"status":"ok"}","kind":"http","status":200}'],
            "fingerprints": [
                "sha256:0000000000000000000000000000000000000000000000000000000000000000"
            ],
            "refusal": None,
        }
    )
    section.heartbeats.append({"probe": "orders-checkout", "outcome": "success", "detail": None})
    return section


def _payload(tree: SourceTree, scope: Scope) -> dict[str, object]:
    packs = (Pack(name="test", extractors=(_Endpoints(),)),)
    sightings = observe_tree(tree, packs=packs)
    report = MapReport(scope=scope.path(), packs=("test",))
    report.files_walked = len(tree.files)
    file_state = [
        FileState(path="app.py", sha256="a" * 64),
        FileState(path="settings.py", sha256="b" * 64),
    ]
    return build_payload(
        run_id=_RUN,
        scope=scope,
        sightings=sightings,
        report=report,
        file_state=file_state,
        cadence_seconds=86_400,
        connector_version="adopt-cli/0.4.0",
        observed_at=_AT,
        probes=_probe_section().payload(),
    )


def test_the_payload_matches_the_committed_fixture(tree: SourceTree, scope: Scope) -> None:
    """The pin both repositories read.

    Regenerate deliberately, never reflexively: this fixture changing means the
    plane's parser must change in the same commit, and `payload_version` with
    it if the change is not additive.
    """
    rendered = render_payload(_payload(tree, scope))
    assert rendered == _FIXTURE.read_text(encoding="utf-8").strip()


def test_the_payload_carries_no_line_of_the_repository(tree: SourceTree, scope: Scope) -> None:
    """The privacy claim, asserted against a secret actually present in the tree.

    Sensing runs in the customer's CI precisely so their source stays there.
    Digests travel; content does not.
    """
    rendered = render_payload(_payload(tree, scope))

    assert _SECRET not in rendered
    assert "DATABASE_URL" in rendered, "the identity's *key* is not content, and must travel"


def test_the_payload_is_ordered_by_what_was_found_not_by_iteration(
    tree: SourceTree, scope: Scope
) -> None:
    """Two builds of one observation render identically.

    The fixture is compared byte for byte, so a payload whose ordering came from
    dictionary iteration would fail here intermittently rather than never —
    which is the failure mode that gets a test deleted instead of read.
    """
    first = render_payload(_payload(tree, scope))
    second = render_payload(_payload(tree, scope))

    assert first == second


def test_a_failed_extractor_travels_so_the_plane_can_exempt_it(
    tree: SourceTree, scope: Scope
) -> None:
    """*Fails when* extractor failures stop reaching the plane.

    *Matters because* the plane retires identities the CI no longer reports, and
    a crashed extractor reports nothing — so without this fact the plane would
    read a crash as a mass deletion and retire an inventory nobody removed.
    Build 6's failed-extractor exemption depends on exactly this field.
    """

    class _Broken:
        name = "test.broken"
        version = "1"

        def extract(self, tree: SourceTree) -> "list[Observation]":
            raise PermissionError("locked")

    packs = (Pack(name="test", extractors=(_Broken(),)),)
    sightings = observe_tree(tree, packs=packs)
    report = MapReport(scope=scope.path(), packs=("test",))

    payload = build_payload(
        run_id=_RUN,
        scope=scope,
        sightings=sightings,
        report=report,
        file_state=[],
        cadence_seconds=3600,
        connector_version="adopt-cli/0.4.0",
        observed_at=_AT,
    )

    extractors = payload["extractors"]
    assert isinstance(extractors, list)
    assert extractors[0]["status"] == "failed"
    assert extractors[0]["detail"] == "PermissionError"
    assert payload["observed"] == []


def test_the_payload_declares_its_version(tree: SourceTree, scope: Scope) -> None:
    """The plane refuses a version it does not know rather than guessing."""
    assert _payload(tree, scope)["payload_version"] == PAYLOAD_VERSION


def test_the_declared_cadence_is_what_silence_is_measured_against(
    tree: SourceTree, scope: Scope
) -> None:
    """The sensor's cadence travels, or the plane cannot tell silence from health."""
    sensor = _payload(tree, scope)["sensor"]
    assert isinstance(sensor, dict)
    assert sensor == {"kind": "ci", "cadence_seconds": 86_400}


def test_the_scope_travels_so_the_plane_can_refuse_a_foreign_payload(
    tree: SourceTree, scope: Scope
) -> None:
    """A payload describing another system must be refusable, not merged."""
    assert _payload(tree, scope)["scope"] == "northwind/acme-erp/orders-api/prod"
