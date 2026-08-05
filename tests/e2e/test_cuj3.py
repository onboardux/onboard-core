"""CUJ-3 -- the coverage cache disagrees with the recompute.

*Fails when* drift between `identity.covered_cache` and `recompute_coverage`
passes unreported, or when the reconciliation resolves toward the cache. *Matters
because* in the withdrawn `0.1.x` line `covered` **was** truth, and a wrong value
became permanent with nothing able to contradict it -- the invisible coverage
decay this whole rebuild exists to delete. *No other instrument catches it
because* the unit table proves the alarm fires from an injected row and the
property proves the function is right, but neither walks the journey an operator
actually takes: run the command, read the exit code, act.

PRD §4 CUJ-3, four steps and one failure branch.
"""

import json
from collections.abc import Callable

import pytest

from adopt_cli.main import main
from adopt_coverage import rebuild_cache, recompute_coverage
from adopt_obs import ExitCode
from adopt_scope import Scope
from adopt_store import BindingRevisionDraft, KnowledgeRevisionDraft, doctor
from adopt_store.api import SqliteStoreHandle


def _covered_identity(
    store: SqliteStoreHandle,
    scope: Scope,
    add_boundary: Callable[..., str],
    add_audience: Callable[..., None],
) -> str:
    assert scope.system is not None
    identity = store.identities().observe(
        scope=scope, kind="endpoint", namespace=None, key="POST /v1/orders"
    )
    item_id, _ = store.items().create(
        scope=scope,
        kind="answer",
        title="How a refund is issued",
        revision=KnowledgeRevisionDraft(
            authority_class="human_confirmed", body_md="v1", verification="verified"
        ),
    )
    add_audience(item_id=item_id)
    store.bindings().create(
        item_id=item_id,
        identity_id=identity.id,
        is_load_bearing=True,
        revision=BindingRevisionDraft(status="active", locator_rung=1),
    )
    add_boundary(system_id=scope.system.id)
    return identity.id


@pytest.mark.e2e
def test_cuj3_a_disagreeing_cache_alarms_and_the_recompute_wins(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    add_boundary: Callable[..., str],
    add_audience: Callable[..., None],
    inject_cache: Callable[..., None],
) -> None:
    assert s4_scope.system is not None
    identity_id = _covered_identity(s4_store, s4_scope, add_boundary, add_audience)
    # Something wrote the cache. In the field this is a stale classification run
    # or a writer nobody remembers; here it is injected, because a journey that
    # could not produce the defect could not walk to the remedy.
    inject_cache(identity_id=identity_id, covered=False)

    # Step 1 and 2 -- recompute, and compare against the cache for every identity
    # in scope.
    result = recompute_coverage(s4_store.coverage_records(), s4_scope.system.id)

    # Step 3 -- the disagreement raises a finding naming the identity.
    findings = doctor(s4_store)
    named = [finding for finding in findings if finding.subject_id == identity_id]

    assert result.disagreements[0].identity_id == identity_id
    assert named, findings
    assert named[0].code == "COVERAGE_CACHE_DISAGREEMENT"

    # Step 4 -- the cache is rebuilt **from the recompute result**. The result is
    # never adjusted to match the cache, which is the direction this whole
    # journey exists to fix.
    rebuild_cache(s4_store.backend, result)

    after = s4_store.identities().get(identity_id)
    assert after is not None
    assert after.covered_cache is True
    assert recompute_coverage(s4_store.coverage_records(), s4_scope.system.id).disagreements == ()


@pytest.mark.e2e
def test_cuj3_failure_branch_looking_at_the_drift_does_not_repair_it(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    add_boundary: Callable[..., str],
    add_audience: Callable[..., None],
    inject_cache: Callable[..., None],
) -> None:
    """*A cache that silently self-heals is the failure this journey exists to
    prevent.*

    Running the diagnosis twice must report the same defect twice. A doctor whose
    first run cleared the finding would leave the second operator looking at a
    healthy store and the writer that caused it still running.
    """
    assert s4_scope.system is not None
    identity_id = _covered_identity(s4_store, s4_scope, add_boundary, add_audience)
    inject_cache(identity_id=identity_id, covered=False)

    first = doctor(s4_store)
    second = doctor(s4_store)

    assert [f.subject_id for f in first] == [f.subject_id for f in second]
    still = s4_store.identities().get(identity_id)
    assert still is not None
    assert still.covered_cache is False


@pytest.mark.e2e
def test_cuj3_the_cli_reports_disagreements_and_exits_degraded(
    s4_store: SqliteStoreHandle,
    s4_scope: Scope,
    add_boundary: Callable[..., str],
    add_audience: Callable[..., None],
    inject_cache: Callable[..., None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`adopt coverage recompute` -- contracts §14, keys and exit code.

    Exit `4` is **degraded success with findings**, not failure: the recompute
    worked and its answer is the one to trust. Exiting `1` would tell an operator
    the tool broke, and they would stop looking at the thing that actually did.
    """
    assert s4_scope.system is not None
    identity_id = _covered_identity(s4_store, s4_scope, add_boundary, add_audience)
    inject_cache(identity_id=identity_id, covered=False)
    store_path = str(s4_store.backend.path)
    s4_store.backend.close()

    exit_code = main(
        ["coverage", "recompute", "--system", s4_scope.system.id, "--store", store_path, "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == ExitCode.DEGRADED_WITH_FINDINGS
    assert set(payload) == {"covered", "uncovered", "disagreements"}
    assert payload["disagreements"][0]["identity_id"] == identity_id
    assert payload["disagreements"][0]["cached"] is False
    assert payload["disagreements"][0]["recomputed"] is True
