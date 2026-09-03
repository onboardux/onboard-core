"""`adopt probe add`'s three answers, against a real store.

*Fails when* adding a probe stops being idempotent, stops appending a revision
for a changed one, or stops going through the append-only family machinery.
*Matters because* `probe_definition` has no UNIQUE constraint on
(system, environment, name) -- idempotence is the verb's job, and if it lapses a
re-run silently creates a second probe of the same name whose runs interleave
with the first's in every later report. *No other instrument catches it because*
the schema cannot: both rows are perfectly legal.

Against a real SQLite store rather than a fake, because the thing under test is
the interaction between the verb's key check and the revision chain -- a fake of
either would be a fake of the half that matters.
"""

import textwrap

import pytest
from adopt_probe import parse_probe

from adopt_cli.commands._probe_support import (
    AddOutcome,
    active_revision,
    add_probe,
    probes_in_scope,
)
from adopt_model import ProbeDefinitionRevision
from adopt_obs import AdoptError
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

pytestmark = pytest.mark.unit


def _text(*, host: str = "127.0.0.1:8123", path: str = "/v1/checkout") -> str:
    return textwrap.dedent(f"""\
        probe_id: checkout-happy-path
        safe_path: sandbox
        network: {{ deny_by_default: true, allow: ["{host}"] }}
        side_effect_policy: prohibited
        runtime: {{ max_seconds: 20, max_memory_mb: 64, max_requests: 5 }}
        cost: {{ max_model_calls: 0, max_tokens: 100 }}
        output: {{ retain_raw: false }}
        cleanup: {{ required: true }}
        steps:
          - kind: http
            method: GET
            url: "http://{host}{path}"
        """)


def test_first_add_creates_definition_and_revision(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    outcome, probe_id, revision_id = add_probe(s4_store, s4_scope, parse_probe(_text()))

    assert outcome == AddOutcome.CREATED
    assert probe_id.startswith("pd_")
    assert revision_id.startswith("pdrev_")

    probes = probes_in_scope(s4_store, s4_scope)
    assert [p.name for p in probes] == ["checkout-happy-path"]
    # The head pointer is advanced by the family machinery, not by the verb.
    assert probes[0].current_revision_id == revision_id


def test_identical_re_add_is_a_no_op(s4_store: SqliteStoreHandle, s4_scope: Scope) -> None:
    """The idempotence the schema cannot give us."""
    first = add_probe(s4_store, s4_scope, parse_probe(_text()))
    second = add_probe(s4_store, s4_scope, parse_probe(_text()))

    assert second == (AddOutcome.UNCHANGED, first[1], first[2])
    assert len(probes_in_scope(s4_store, s4_scope)) == 1
    revisions = list(
        s4_store.export_records().table_rows("probe_definition_revision", ProbeDefinitionRevision)
    )
    assert len(revisions) == 1


def test_changed_probe_appends_a_revision(s4_store: SqliteStoreHandle, s4_scope: Scope) -> None:
    """A supersede chain, which is what lets `diff` say *the probe* changed."""
    _, probe_id, first_revision = add_probe(s4_store, s4_scope, parse_probe(_text()))
    outcome, same_probe, second_revision = add_probe(
        s4_store, s4_scope, parse_probe(_text(path="/v2/checkout"))
    )

    assert outcome == AddOutcome.REVISED
    assert same_probe == probe_id
    assert second_revision != first_revision
    assert len(probes_in_scope(s4_store, s4_scope)) == 1

    revisions = {
        row.id: row
        for row in s4_store.export_records().table_rows(
            "probe_definition_revision", ProbeDefinitionRevision
        )
    }
    assert len(revisions) == 2
    assert revisions[second_revision].supersedes_revision_id == first_revision
    assert active_revision(s4_store, probes_in_scope(s4_store, s4_scope)[0]).id == second_revision


def test_a_probe_needs_a_system_and_an_environment(s4_store: SqliteStoreHandle) -> None:
    """A probe that did not say which environment could be pointed at production."""
    firm_only = s4_store.scope()
    firm = firm_only.create_firm(slug="solo", name="Solo")
    engagement = firm_only.create_engagement(firm_id=firm.id, slug="eng", name="Eng")
    scope = firm_only.resolve(f"{firm.slug}/{engagement.slug}")

    with pytest.raises(AdoptError):
        add_probe(s4_store, scope, parse_probe(_text()))
