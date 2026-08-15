"""The unlabelled bucket and its queue -- `01` F12.6, §8, PRD Q7, `05` S1.6.

`01` §8's autonomy matrix has one row whose auto-promotion cell reads **never**:
*"Label opaque platform fields -- Human -- Required."* This module is the
instrument for that row, plus the distinction S1.6 had to draw to make the bucket
mean anything (B1-CR-77): **unlabelled is not opaque.**

Built on hand-made facts rather than on a fixture run, deliberately. The rules
here are about *which* components reach a human and what the queue may carry, and
a fixture can only show the cases it happens to contain -- including, dangerously,
none of them.
"""

import datetime as _dt
import json
from typing import Any

import pytest
from adopt_map.emit.labeling_queue import labeling_queue_payload, render_labeling_queue, wanted
from adopt_map.fileindex import FileIndex
from adopt_map.report import RunResult
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from adopt_map.scope_resolve import ResolvedScope
from adopt_map.unlabeled import unlabeled_components
from adopt_map.writer import FactBatch

from adopt_scope import Scope, ScopeNode

pytestmark = pytest.mark.unit

#: An empty index and a hand-built scope. Both are scaffolding for `RunResult`:
#: the claims in this module are about which components reach a human, and a
#: store-backed scope would make every one of them an integration test of the
#: scope resolver.
_EMPTY_INDEX = FileIndex(
    root=".",
    files=(),
    discovered=0,
    sampled=False,
    skipped_large=0,
    skipped_binary=0,
    vcs_revision=None,
)


def _scope(archetype: str) -> ResolvedScope:
    firm = ScopeNode(id="firm_01", slug="northwind")
    engagement = ScopeNode(id="eng_01", slug="acme-erp")
    system = ScopeNode(id="sys_01", slug="orders-api")
    environment = ScopeNode(id="env_01", slug="prod")
    return ResolvedScope(
        firm_id=firm.id,
        engagement_id=engagement.id,
        system_id=system.id,
        environment_id=environment.id,
        scope=Scope(firm=firm, engagement=engagement, system=system, environment=environment),
        environment_slug=environment.slug,
        archetype=archetype,  # type: ignore[arg-type]
        tier="T2",
    )


_MANIFEST = ExtractorManifest(
    id="platform.sf_metadata",
    version="1.0.0",
    pack="platform",
    archetypes=["platform"],
    kinds=["metadata_component"],
    method="reflection",
)


def _component(local_key: str, *, opaque: bool = False, **attributes: Any) -> SurfaceFact:
    return SurfaceFact(
        identity_kind="metadata_component",
        namespace="salesforce",
        local_key=local_key,
        title=local_key,
        attributes=attributes,
        source_refs=[SourceRef(path="objects/Order__c.object-meta.xml")],
        opaque=opaque,
    )


def _result(*facts: SurfaceFact) -> RunResult:
    return RunResult(
        run_id="run_01TEST",
        adopt_version="test",
        generated_at=_dt.datetime(2026, 8, 15, tzinfo=_dt.UTC),
        resolved=_scope("platform"),
        index=_EMPTY_INDEX,
        batches=(FactBatch(manifest=_MANIFEST, facts=tuple(facts)),),
    )


def test_a_component_the_export_did_not_label_reaches_the_bucket() -> None:
    """*Defect sentence.* Fails when an unlabelled component stops reaching the
    queue, or when a labelled one starts; matters because the queue is the only
    route by which a packaged platform's meaningless API names become readable,
    and a queue that silently drops entries looks exactly like a well-labelled
    org; no other instrument catches it, because every count and every coverage
    figure is identical either way.
    """
    result = _result(
        _component("CustomField.Order__c.Status__c", api_name="Status__c", label="Order Status"),
        _component("CustomField.Order__c.ZFIELD_003__c", api_name="ZFIELD_003__c"),
        _component("CustomField.Order__c.ZFIELD_007__c", api_name="ZFIELD_007__c", label=""),
    )
    keys = [entry.api_name for entry in unlabeled_components(result)]
    # An empty label is not a label. Salesforce writes `<label></label>` on
    # generated fields, and treating that as labelled would hide the exact
    # components the bucket exists for.
    assert keys == ["ZFIELD_003__c", "ZFIELD_007__c"]


def test_unlabelled_is_not_opaque() -> None:
    """B1-CR-77, the distinction the whole bucket rests on.

    *Defect sentence.* Fails when an unlabelled-but-defined component is marked
    opaque; matters because `opaque` nulls the semantic digest (`02` §4.1 `s-`),
    so the component's **data type could then change with no revision written and
    the run reporting that nothing happened** -- B1-CR-44's silent failure,
    arriving through a flag rather than through a projection; no other instrument
    catches it, because both components are in the bucket either way and the
    bucket is what everybody looks at.
    """
    defined = _component("CustomField.Order__c.ZFIELD_003__c", api_name="ZFIELD_003__c")
    referenced = _component("CustomField.Order__c.Ghost__c", opaque=True, api_name="Ghost__c")
    result = _result(defined, referenced)

    assert defined.opaque is False
    assert referenced.opaque is True
    entries = {entry.api_name: entry for entry in unlabeled_components(result)}
    # Both need a human; only one of them has nothing to compare.
    assert set(entries) == {"ZFIELD_003__c", "Ghost__c"}
    assert entries["ZFIELD_003__c"].opaque is False
    assert "referenced but not defined" in entries["Ghost__c"].evidence()


def test_the_queue_has_nowhere_to_put_a_label() -> None:
    """`01` §8: auto-promotion **never**, made structural.

    *Defect sentence.* Fails the moment the queue gains a writable answer slot --
    a `label`, a `candidate`, a `suggestion`, a `score`; matters because S1.7's
    agentic pass produces label *candidates* (`01` F12.6) and a queue with a
    fillable field is a queue something eventually fills; no other instrument
    catches it, because a populated field would look like a feature in review and
    like a labelled org in every artefact downstream.
    """
    result = _result(_component("CustomField.Order__c.ZFIELD_003__c", api_name="ZFIELD_003__c"))
    payload = labeling_queue_payload(result)
    forbidden = {"label", "candidate", "candidates", "suggestion", "suggested_label", "score"}
    for entry in payload["entries"]:
        assert not (forbidden & set(entry)), f"the queue can carry a label: {sorted(entry)}"
    # And nothing in the rendered bytes offers one either.
    assert '"label"' not in render_labeling_queue(result)


def test_the_queue_reports_both_numbers() -> None:
    """ "48 unlabelled" is not a finding; "48 of 51" is."""
    result = _result(
        _component("CustomField.Order__c.Status__c", api_name="Status__c", label="Order Status"),
        _component("CustomField.Order__c.ZFIELD_003__c", api_name="ZFIELD_003__c"),
    )
    payload = json.loads(render_labeling_queue(result))
    assert payload["components"] == 2
    assert payload["unlabeled"] == 1


def test_a_run_with_no_components_writes_no_queue() -> None:
    """A web or AI run has no unlabelled bucket to report.

    Emitting an empty queue beside every `surface.md` would teach a reader to
    ignore the file on the one archetype where it carries work.
    """
    empty = RunResult(
        run_id="run_01TEST",
        adopt_version="test",
        generated_at=_dt.datetime(2026, 8, 15, tzinfo=_dt.UTC),
        resolved=_scope("web"),
        index=_EMPTY_INDEX,
        batches=(),
    )
    assert wanted(empty) is False
    assert wanted(_result(_component("CustomObject.Order__c", api_name="Order__c"))) is True
