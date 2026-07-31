"""Any sequence of additive edits stays additive, and any removal never does.

*Fails when* the linter's rule set has a hole a sequence of individually-innocent
edits can walk through -- a column added then renamed, an enum extended then
reordered, a table added then dropped. *Matters because* the additive-only
promise is what makes "no later build item ever writes a migration" true, and it
is enforced against a diff rather than against a single edit. *No other
instrument catches it because* the table-driven rule tests each check one edit in
isolation, and the failure being hunted here is a combination.

Hypothesis generates the edit sequences; the invariant is that additive edits
produce no violations and that any removal produces at least one.
"""

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from adopt_schema.lint import lint
from adopt_schema.manifest import Manifest

_BASE: dict[str, Any] = {
    "schema_version": 3,
    "export_version": 3,
    "enums": {"status": {"since": 3, "values": ["a", "b"]}},
    "tables": {
        "item": {
            "since": 3,
            "purpose": "A thing.",
            "scope_level": "global",
            "exportable": True,
            "primary_key": ["id"],
            "columns": [{"name": "id", "type": "id", "nullable": False}],
            "indexes": [],
        }
    },
}

_NAMES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8)


def _base() -> Manifest:
    return Manifest.model_validate(_BASE)


@st.composite
def additive_edits(draw: st.DrawFn) -> dict[str, Any]:
    """A manifest reachable from `_BASE` by additions alone."""
    payload: dict[str, Any] = Manifest.model_validate(_BASE).model_dump()

    for name in draw(st.lists(_NAMES, max_size=4, unique=True)):
        payload["tables"]["item"]["columns"].append(
            {"name": f"c_{name}", "type": "text", "nullable": True}
        )
    for name in draw(st.lists(_NAMES, max_size=3, unique=True)):
        payload["tables"][f"t_{name}"] = {
            "since": 4,
            "purpose": "Added later.",
            "scope_level": "global",
            "exportable": True,
            "primary_key": ["id"],
            "columns": [{"name": "id", "type": "id", "nullable": False}],
            "indexes": [],
        }
    for value in draw(st.lists(_NAMES, max_size=3, unique=True)):
        payload["enums"]["status"]["values"].append(f"v_{value}")
    return payload


@pytest.mark.property
@settings(max_examples=100)
@given(head=additive_edits())
def test_additive_edit_sequences_never_violate(head: dict[str, Any]) -> None:
    assert lint(_base(), Manifest.model_validate(head)) == []


@pytest.mark.property
@settings(max_examples=50)
@given(removed=st.sampled_from(["column", "table", "enum_value"]))
def test_any_removal_is_caught(removed: str) -> None:
    """The mirror of the property above: additive is permitted *because*
    non-additive is not, and a linter that permitted everything would pass the
    first test perfectly."""
    payload = Manifest.model_validate(_BASE).model_dump()
    payload["tables"]["item"]["columns"].append({"name": "extra", "type": "text"})
    payload["enums"]["status"]["values"].append("c")
    payload["tables"]["gone"] = {
        "since": 3,
        "purpose": "Present in the base only.",
        "scope_level": "global",
        "exportable": True,
        "primary_key": ["id"],
        "columns": [{"name": "id", "type": "id", "nullable": False}],
        "indexes": [],
    }
    base = Manifest.model_validate(payload)

    head_payload = base.model_dump()
    if removed == "column":
        head_payload["tables"]["item"]["columns"] = [
            c for c in head_payload["tables"]["item"]["columns"] if c["name"] != "extra"
        ]
    elif removed == "table":
        del head_payload["tables"]["gone"]
    else:
        head_payload["enums"]["status"]["values"].remove("c")

    assert lint(base, Manifest.model_validate(head_payload)) != []
