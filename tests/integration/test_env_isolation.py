"""Environment separation -- PRD F6, N6, CUJ-6; contracts §10 C9; `03` §8.

**A hard gate, and one of the five instruments that survive any budget cut.**
PRD F6.1's claim is not that the framework is careful: it is that a staging run
*cannot* produce a production URI **structurally**, because the environment
segment comes from `ResolvedScope` and a `SurfaceFact` has no field through which
an extractor could influence it.

That claim is worth a fuzz suite precisely because it is structural. A
well-behaved extractor proves nothing here -- the interesting question is what a
**hostile** one achieves, and the answer has to be nothing, whatever it puts in
the two fields it does control.

| Behavior | Tier | Defect it catches |
|---|---|---|
| A staging run emits zero production URIs under fuzzed output | **T1** | The whole of F6.1 |
| The two identity sets are disjoint | **T1** | Two environments merging into one registry |
| A staging run writes no revision in the production set | **T1** | Staging rewriting production's history |
| `SurfaceFact` carries no scope field at all | **T1** | The structural guarantee becoming a procedural one |
"""

import contextlib
from collections.abc import Callable

import pytest
from adopt_map.schemas import ExtractorManifest, SurfaceFact
from adopt_map.scope_resolve import ResolvedScope
from adopt_map.writer import SurfaceWriter, SurfaceWriteResult
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adopt_model import Identity, IdentityRevision
from adopt_obs import AdoptError
from adopt_store.api import SqliteStoreHandle

pytestmark = [pytest.mark.integration, pytest.mark.env_isolation]

_MANIFEST = ExtractorManifest(
    id="common.fixture", version="1.0.0", pack="common", kinds=["config_key"], method="grammar"
)

#: What a hostile extractor would try in the only two fields it controls.
#: `build_uri()` refuses input that already carries an escape, so some of these
#: abort -- which is also a pass: an abort emits no URI at all.
_HOSTILE = st.sampled_from(
    [
        "prod",
        "/prod/",
        "../prod",
        "..%2Fprod",
        "%2Fprod%2F",
        "onboard-v1://northwind/acme-erp/orders-api/prod/config_key/env/X",
        "a/../../prod",
        "prod⁄x",  # noqa: RUF001 -- a fraction slash is exactly the point
        "\u0000prod",
        "PROD",
    ]
)


def _environment_segment(uri: str) -> str:
    """The URI's fourth path segment -- `01` §12's grammar."""
    return uri.removeprefix("onboard-v1://").split("/")[3]


@pytest.fixture
def run(surface_writer: SurfaceWriter) -> Callable[..., SurfaceWriteResult]:
    def _run(scope: ResolvedScope, *facts: SurfaceFact) -> SurfaceWriteResult:
        return surface_writer.write_run(
            resolved=scope, manifest=_MANIFEST, facts=list(facts), vcs_revision=None
        )

    return _run


def _identities(handle: SqliteStoreHandle) -> list[Identity]:
    return list(handle.export_records().table_rows("identity", Identity))


def test_a_surface_fact_has_no_field_through_which_to_name_an_environment() -> None:
    """The mechanism, asserted directly -- `02` §7's closing note.

    Fails when a scope-shaped field is added to `SurfaceFact`; matters because
    every other assertion in this file is downstream of the absence: with such a
    field the guarantee stops being structural and becomes a rule the writer has
    to remember to enforce; no other instrument catches it because a new field
    would simply be ignored until the first extractor set it.
    """
    forbidden = {
        "uri",
        "confidence",
        "source_version",
        "environment",
        "environment_id",
        "environment_slug",
        "scope",
        "firm_id",
        "engagement_id",
        "system_id",
    }
    assert forbidden.isdisjoint(SurfaceFact.model_fields)


@settings(
    max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(namespace=_HOSTILE, local_key=_HOSTILE)
def test_a_staging_run_emits_zero_production_uris_under_fuzzed_output(
    run: Callable[..., SurfaceWriteResult],
    staging_scope: ResolvedScope,
    s4_store: SqliteStoreHandle,
    namespace: str,
    local_key: str,
) -> None:
    """N6, the gate itself."""
    fact = SurfaceFact(
        identity_kind="config_key", namespace=namespace, local_key=local_key, title="fuzzed"
    )
    # A refused key emits nothing, which is the guarantee holding rather than an
    # exemption from it: `build_uri()` rejects a pre-encoded segment outright.
    with contextlib.suppress(AdoptError):
        run(staging_scope, fact)

    for identity in _identities(s4_store):
        assert _environment_segment(identity.uri) == "staging", (
            f"a staging run minted {identity.uri!r}"
        )


def test_the_two_environments_hold_disjoint_identity_sets(
    run: Callable[..., SurfaceWriteResult],
    resolved_scope: ResolvedScope,
    staging_scope: ResolvedScope,
    s4_store: SqliteStoreHandle,
) -> None:
    """PRD F6.4 -- re-running against a second environment never merges the two."""
    fact = SurfaceFact(
        identity_kind="config_key",
        namespace="env",
        local_key="DATABASE_URL",
        title="DATABASE_URL",
        attributes={"key_path": "DATABASE_URL"},
    )
    run(resolved_scope, fact)
    run(staging_scope, fact)

    identities = _identities(s4_store)
    assert len(identities) == 2, "one referent, two environments, two identities"
    assert {_environment_segment(row.uri) for row in identities} == {"prod", "staging"}
    assert len({row.id for row in identities}) == 2


def test_a_staging_run_writes_no_revision_in_the_production_set(
    run: Callable[..., SurfaceWriteResult],
    resolved_scope: ResolvedScope,
    staging_scope: ResolvedScope,
    s4_store: SqliteStoreHandle,
) -> None:
    """CUJ-6 step 2 -- the production identity set is untouched."""
    fact = SurfaceFact(
        identity_kind="config_key",
        namespace="env",
        local_key="DATABASE_URL",
        title="DATABASE_URL",
        attributes={"key_path": "DATABASE_URL"},
    )
    run(resolved_scope, fact)
    production_ids = {row.id for row in _identities(s4_store)}
    before = [
        row
        for row in s4_store.export_records().table_rows("identity_revision", IdentityRevision)
        if row.identity_id in production_ids
    ]

    run(staging_scope, fact)

    after = [
        row
        for row in s4_store.export_records().table_rows("identity_revision", IdentityRevision)
        if row.identity_id in production_ids
    ]
    assert [row.id for row in after] == [row.id for row in before]
