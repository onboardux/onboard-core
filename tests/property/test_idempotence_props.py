"""Idempotence over arbitrary run sequences -- `03` §5.5 test focus, PRD F4.

*"∀ n runs on an unchanged tree, `revisions_written` = 0 for n ≥ 2."* One of the
**five instruments that survive any budget cut** (`05` Quality notes).

*Fails when* the comparison depends on anything other than what the facts say --
the number of prior revisions, the run count, the order facts arrive in, a clock,
or a commit sha. *Matters because* every downstream build reads these revisions
as a change feed, so a writer that appends on a scan does not make the feed noisy,
it makes it meaningless: Build 3 resolves change events through it and Build 10
classifies impact from it. *No other instrument catches it because* the
table-driven cases in `tests/integration/test_idempotence.py` fix the fact set
and the run count, and an order- or count-sensitive rule passes all of them.
"""

from typing import Any

import pytest
from adopt_map.schemas import ExtractorManifest, SurfaceFact
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.build1_conftest import build_scoped_store, surface_writer_for

pytestmark = [pytest.mark.property, pytest.mark.idempotence]

_MANIFEST = ExtractorManifest(
    id="common.fixture", version="1.0.0", pack="common", kinds=["endpoint"], method="grammar"
)

#: A fact whose every field a run could plausibly vary, drawn from both
#: projections so the property covers a semantic and a presentation payload.
_FACTS = st.lists(
    st.builds(
        lambda key, path, summary, prose, codes: SurfaceFact(
            identity_kind="endpoint",
            namespace="http",
            local_key=key,
            title=key,
            attributes={"path": path, "summary": summary, "status_codes": codes},
            prose=prose,
        ),
        key=st.sampled_from(["GET /a", "POST /b", "GET /c/{id}", "DELETE /d"]),
        path=st.sampled_from(["/a", "/b", "/c/{id}", "/d"]),
        summary=st.sampled_from(["", "Orders", None]),
        prose=st.sampled_from(["", "Returns a thing.", None]),
        codes=st.lists(st.sampled_from([200, 404]), max_size=2),
    ),
    min_size=1,
    max_size=4,
    unique_by=lambda fact: fact.local_key,
)


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(facts=_FACTS, extra_runs=st.integers(min_value=1, max_value=4))
def test_every_run_after_the_first_writes_zero_revisions(
    tmp_path_factory: pytest.TempPathFactory, facts: list[SurfaceFact], extra_runs: int
) -> None:
    root = tmp_path_factory.mktemp("idempotence")
    handle, scopes = build_scoped_store(root)
    try:
        writer = surface_writer_for(handle)

        def _run() -> dict[str, int]:
            return writer.write_run(
                resolved=scopes["prod"], manifest=_MANIFEST, facts=facts, vcs_revision=None
            ).revisions_written

        first = _run()
        assert sum(first.values()) > 0, "the first run must actually write something"

        for _ in range(extra_runs):
            assert _run() == {"identity": 0, "knowledge": 0, "binding": 0}
    finally:
        handle.close()


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(facts=_FACTS, seed=st.randoms(use_true_random=False))
def test_the_order_facts_arrive_in_does_not_change_what_is_written(
    tmp_path_factory: pytest.TempPathFactory, facts: list[SurfaceFact], seed: Any
) -> None:
    """A re-ordered extractor is not a changed system.

    An extractor's emission order is a property of how its parser walks a tree,
    and `02` §7 obligation 3 forbids it depending on filesystem or set ordering.
    A writer sensitive to it would turn a tree-walk change into a store full of
    revisions.
    """
    root = tmp_path_factory.mktemp("order")
    handle, scopes = build_scoped_store(root)
    try:
        writer = surface_writer_for(handle)
        writer.write_run(
            resolved=scopes["prod"], manifest=_MANIFEST, facts=facts, vcs_revision=None
        )

        shuffled = list(facts)
        seed.shuffle(shuffled)
        second = writer.write_run(
            resolved=scopes["prod"], manifest=_MANIFEST, facts=shuffled, vcs_revision=None
        )
        assert second.revisions_written == {"identity": 0, "knowledge": 0, "binding": 0}
    finally:
        handle.close()
