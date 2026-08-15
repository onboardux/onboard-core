"""The low-code and data packs -- `01` F8.4, F8.5, `02` §3.1, §5.2, `05` S1.6.

Two packs in one module because their claims are the same *kind* of claim -- key
shape, label honesty, and one thing each that nothing else can see:

* **low-code**: a connection reference is a `config_key` under `secret:*`, whose
  attribute model has no field a credential could occupy.
* **data**: lineage has a **direction**, and reversing it still produces edges --
  which is exactly why the direction needs an assertion rather than a count.

The eight obligations and the observation set are covered by the shared
conformance suite and the goldens; nothing here repeats them.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from adopt_extractors_common import pack as common_pack
from adopt_extractors_data import pack as data_pack
from adopt_extractors_lowcode import pack as lowcode_pack
from adopt_map.documents import declares_owned_document
from adopt_map.schemas import Extractor, SurfaceFact

from tests.build1_conftest import context_for

pytestmark = pytest.mark.unit

_LOWCODE = Path("fixtures/repos/powerapps-export")
_DATA = Path("fixtures/repos/dbt-warehouse")


def _facts(
    tree: Path, archetype: str, pack: Callable[[], tuple[Extractor, ...]]
) -> dict[str, SurfaceFact]:
    ctx = context_for(tree, archetype=archetype)
    found: dict[str, SurfaceFact] = {}
    for extractor in pack():
        for fact in extractor.extract(ctx):
            found[f"{fact.identity_kind}/{fact.namespace}/{fact.local_key}"] = fact
    return found


# --------------------------------------------------------------------------- low-code

_LOWCODE_KEYS: list[tuple[str, str | None]] = [
    ("metadata_component/powerapps/Solution.OrderIntake", "Order Intake"),
    ("metadata_component/powerapps/Workflow.CreateOrderApproval", "Create order approval"),
    ("metadata_component/powerapps/Workflow.ZFLOW_042", None),
    ("metadata_component/powerapps/CanvasApp.OrderIntakeApp", "Order Intake App"),
    ("metadata_component/powerapps/Entity.new_order", "Order"),
    ("metadata_component/powerapps/Entity.new_orderline", None),
    # A form's `<Name>` is its name. `ZFORM_017` is why that is not a label, and
    # the rule is applied to the one that reads well too.
    ("metadata_component/powerapps/Form.new_order.Main Order Form", None),
    ("metadata_component/powerapps/Form.new_order.ZFORM_017", None),
]


@pytest.mark.parametrize(("key", "label"), _LOWCODE_KEYS, ids=[row[0] for row in _LOWCODE_KEYS])
def test_a_solution_component_carries_only_the_label_its_export_states(
    key: str, label: str | None
) -> None:
    """*Defect sentence.* Fails when a component's key shape moves or when one
    acquires a label the export never wrote; matters because the first version of
    this pack labelled every form with its own `<Name>`, which labelled
    `ZFORM_017` "ZFORM_017" and would have emptied the unlabelled bucket on every
    real solution; no other instrument catches it -- the count stayed right, the
    recall stayed 1.0, and only the *content* of the label was a fiction.
    """
    facts = _facts(_LOWCODE, "lowcode", lowcode_pack)
    assert key in facts, f"{key} not emitted; got {sorted(facts)[:8]}"
    assert facts[key].attributes.get("label") == label


def test_a_connection_reference_is_a_value_free_secret_reference() -> None:
    """`02` §3.1 rule 2 and §5.1 rule 4, and `01` F8.6 for the flag.

    *Defect sentence.* Fails when a connection reference stops being a
    `secret:*` `config_key`, gains an attribute outside `source`/`name`, or loses
    its `outside_vcs` flag; matters because the value-free model is what makes
    "no credential can be recorded" structural rather than careful, and because
    the connection can be repointed in a vendor UI with no commit anywhere in the
    export; no other instrument catches the flag, since the planted-secret suite
    watches values and this reference has none to watch.
    """
    facts = _facts(_LOWCODE, "lowcode", lowcode_pack)
    key = "config_key/secret:connection/new_sharedsql_ordersdb"
    assert key in facts
    fact = facts[key]
    assert set(fact.attributes) == {"source", "name"}
    assert fact.outside_vcs is True
    # The model is the guarantee, not this assertion: validating proves the
    # closed schema rejects anything else.
    fact.validated_attributes()


# ------------------------------------------------------------------------------- data

_LINEAGE: list[tuple[str, list[str]]] = [
    ("metadata_component/dbt/model.stg_orders", ["source.raw.orders"]),
    ("metadata_component/dbt/model.orders_daily", ["model.stg_orders", "model.stg_customers"]),
    ("metadata_component/dbt/model.customer_revenue", ["model.orders_daily"]),
    ("metadata_component/dbt/semantic_model.orders", ["model.orders_daily"]),
    ("metadata_component/dbt/metric.revenue", ["semantic_model.orders"]),
]


@pytest.mark.parametrize(("key", "upstream"), _LINEAGE, ids=[row[0] for row in _LINEAGE])
def test_lineage_points_from_the_derived_thing_to_its_source(key: str, upstream: list[str]) -> None:
    """`02` §5.2: the extractor emits the direction it observed, and the framework
    creates no inverses.

    *Defect sentence.* Fails when `derives_from` is emitted backwards, or when an
    edge is dropped; matters because a reversed lineage graph is not an obviously
    broken one -- it has the same nodes, the same edge count and the same
    `derives_from` predicate, and it answers "what breaks if I change this?" with
    exactly the wrong set; no other instrument catches it, because recall counts
    identities and never looks at an edge.
    """
    facts = _facts(_DATA, "data", data_pack)
    assert key in facts
    edges = [(relation.predicate, relation.target_local_key) for relation in facts[key].relations]
    assert edges == [("derives_from", target) for target in upstream]


def test_no_inverse_edge_is_invented() -> None:
    """The upstream end of a lineage edge carries no edge back.

    Half of "the framework does not auto-create inverses" is that nobody else
    does either: `source.raw.orders` is derived from nothing, and a graph that
    quietly gained the inverse would answer impact questions in both directions
    while only one was observed.
    """
    facts = _facts(_DATA, "data", data_pack)
    assert facts["metadata_component/dbt/source.raw.orders"].relations == []


@pytest.mark.parametrize(
    ("path", "owner"),
    [
        ("models/schema.yml", "data.dbt"),
        ("dbt_project.yml", "data.dbt"),
        ("models/semantic/semantic_models.yml", "data.semantic_model"),
    ],
)
def test_the_dbt_documents_are_owned_and_common_config_defers(path: str, owner: str) -> None:
    """B1-CR-74, arriving a third time.

    *Defect sentence.* Fails when `common.config` starts minting `config_key`s
    out of a dbt document this pack already reads; matters because one setting
    under two kinds is double-counted in every coverage figure downstream, and
    the symptom is a *higher* number rather than an error; no other instrument
    catches it -- the labeled set would, which is why it says so out loud.
    """
    text = (_DATA / path).read_text(encoding="utf-8")
    assert declares_owned_document(text) == owner


def test_common_config_emits_no_config_key_over_the_dbt_tree() -> None:
    """The deferral, observed rather than argued.

    The rule above is about a string; this is about the run. `common.config` is
    the extractor that reads every YAML file in a tree, and over a dbt project it
    must produce nothing at all -- every document is claimed.
    """
    facts = _facts(_DATA, "data", common_pack)
    assert [key for key in facts if key.startswith("config_key/")] == []
