"""After any write sequence: no chain forks, and every head resolves.

*Fails when* some ordering of creates, appends and retirements across the four
families leaves a parent with two revisions that nothing supersedes, or with a
`current_revision_id` pointing at a row that is not there. *Matters because*
those are the two ways the audit record stops being able to answer "what did this
say then", and both are silent -- a forked store reads normally until someone
asks which revision is current. *No other instrument catches it because* the unit
tests drive the sequences somebody thought of, and `no-revision-update` reads
source text and cannot see a chain damaged at runtime by writers that are each
individually well-formed.

Stated over `doctor` rather than over hand-written queries: `doctor` is what an
operator actually runs, so a bug in the finder is a bug in the field, and a
property asserting the invariant some other way would leave the instrument
untested.
"""

import datetime as _dt
from collections.abc import Iterator

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adopt_obs import AdoptError, ErrorCode, ManualClock
from adopt_scope import Scope
from adopt_store import (
    BindingRevisionDraft,
    IdentityRevisionDraft,
    KnowledgeRevisionDraft,
    ProbeDefinitionRevisionDraft,
    doctor,
    open_store,
)
from adopt_store.api import SqliteStoreHandle
from adopt_store.revisions import FAMILIES

_START = _dt.datetime(2026, 8, 3, 9, 0, 0, tzinfo=_dt.UTC)

#: The operations a caller has. `retire` is included because a terminal revision
#: is still an append, and a sequence that retires and then appends again is
#: exactly the shape a careless writer produces.
_OPERATIONS = ("append", "retire", "append_with_stale_head")

#: Each family, so the property covers the stored head and the derived one.
_FAMILY_NAMES = ("identity", "knowledge", "binding", "probe_definition")


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SqliteStoreHandle]:
    """One store for the run: creating schema version 3 costs the full 37-table
    DDL, and every example works under its own parent rows."""
    path = tmp_path_factory.mktemp("chains") / "store.db"
    handle = open_store(path, migrate=True, clock=ManualClock(_START))
    yield handle
    handle.close()


@pytest.fixture(scope="module")
def scope(store: SqliteStoreHandle) -> Scope:
    facade = store.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="orders-api", name="Orders API")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    return facade.resolve("northwind/acme-erp/orders-api/prod")


def _draft(family_name: str, tag: str) -> object:
    if family_name == "identity":
        return IdentityRevisionDraft(status="active", extractor=tag)
    if family_name == "knowledge":
        return KnowledgeRevisionDraft(authority_class="artifact_observed", body_md=tag)
    if family_name == "binding":
        return BindingRevisionDraft(status="active", extractor=tag)
    return ProbeDefinitionRevisionDraft(
        interaction=tag, safe_path="mock", diff_method="exact", capability_manifest="{}"
    )


def _new_parent(store: SqliteStoreHandle, scope: Scope, family_name: str, tag: str) -> str:
    if family_name == "identity":
        return (
            store.identities()
            .observe(scope=scope, kind="symbol", namespace=None, key=f"sym-{tag}")
            .id
        )
    if family_name == "knowledge":
        item_id, _ = store.items().create(
            scope=scope,
            kind="answer",
            title=f"item-{tag}",
            revision=KnowledgeRevisionDraft(authority_class="human_confirmed", body_md="v1"),
        )
        return item_id
    if family_name == "binding":
        item_id, _ = store.items().create(
            scope=scope,
            kind="answer",
            title=f"bound-{tag}",
            revision=KnowledgeRevisionDraft(authority_class="human_confirmed", body_md="v1"),
        )
        identity = store.identities().observe(
            scope=scope, kind="symbol", namespace=None, key=f"bound-{tag}"
        )
        binding_id, _ = store.bindings().create(
            item_id=item_id, identity_id=identity.id, is_load_bearing=True
        )
        return binding_id
    probe_id, _ = store.probes().create(
        scope=scope,
        name=f"probe-{tag}",
        revision=ProbeDefinitionRevisionDraft(
            interaction="GET /health",
            safe_path="sandbox",
            diff_method="exact",
            capability_manifest="{}",
        ),
    )
    return probe_id


@pytest.mark.property
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    family_name=st.sampled_from(_FAMILY_NAMES),
    operations=st.lists(st.sampled_from(_OPERATIONS), min_size=1, max_size=8),
    seed=st.integers(min_value=0, max_value=2**32),
)
def test_no_write_sequence_forks_a_chain_or_dangles_a_head(
    store: SqliteStoreHandle,
    scope: Scope,
    family_name: str,
    operations: list[str],
    seed: int,
) -> None:
    """The invariant, over an arbitrary sequence including refused writes.

    A refused write is part of the sequence on purpose: `REVISION_CHAIN_FORK` is
    supposed to leave the store exactly as it was, and a property that only ever
    performed *successful* writes would never notice a refusal that half-committed.
    """
    tag = f"{family_name}-{seed:08x}"
    parent_id = _new_parent(store, scope, family_name, tag)
    writer = store.revisions()

    for index, operation in enumerate(operations):
        if operation == "append":
            writer.append_revision(
                parent_id=parent_id,
                draft=_draft(family_name, f"{tag}-{index}"),  # type: ignore[arg-type]
                expected_head_id=writer.current_head(parent_id),
            )
        elif operation == "retire":
            writer.retire(parent_id=parent_id, reason=f"{tag}-{index}")
        else:
            with pytest.raises(AdoptError) as raised:
                writer.append_revision(
                    parent_id=parent_id,
                    draft=_draft(family_name, f"{tag}-{index}"),  # type: ignore[arg-type]
                    expected_head_id="krev_01JSTALEHEADSTALEHEADSTAL",
                )
            assert raised.value.code is ErrorCode.REVISION_CHAIN_FORK

    findings = doctor(store)
    assert findings == [], [finding.render() for finding in findings]

    # Every head resolves, stated directly as well as through `doctor`: the
    # finder could in principle miss a family, and this cannot.
    family = FAMILIES[parent_id.split("_", 1)[0]]
    head = writer.current_head(parent_id)
    assert head is not None
    assert store.revision_records().revision_exists(family.revision_table, head)


@pytest.mark.property
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    family_name=st.sampled_from(_FAMILY_NAMES),
    appends=st.integers(min_value=1, max_value=6),
    seed=st.integers(min_value=0, max_value=2**32),
)
def test_every_revision_but_the_head_is_superseded_exactly_once(
    store: SqliteStoreHandle, scope: Scope, family_name: str, appends: int, seed: int
) -> None:
    """A chain is a line, not a tree.

    Stronger than "one head": a chain where two revisions both supersede a third
    has one head and is still not a history, because two different pasts lead to
    one present.
    """
    tag = f"line-{family_name}-{seed:08x}"
    parent_id = _new_parent(store, scope, family_name, tag)
    writer = store.revisions()
    for index in range(appends):
        writer.append_revision(
            parent_id=parent_id,
            draft=_draft(family_name, f"{tag}-{index}"),  # type: ignore[arg-type]
            expected_head_id=writer.current_head(parent_id),
        )

    family = FAMILIES[parent_id.split("_", 1)[0]]
    records = store.revision_records()
    revisions = list(records.revision_ids(family.revision_table, parent_id))
    superseded = list(records.superseded_ids(family.revision_table, parent_id))

    assert len(superseded) == len(set(superseded)), "no revision is superseded twice"
    assert set(superseded) < set(revisions)
    assert len(set(revisions) - set(superseded)) == 1, "exactly one head"
