"""CUJ-9 -- an outbound envelope carrying content is rejected.

*Fails when* client content passes the gate, or when a caller's own declaration
starts deciding what may leave. *Matters because* everything downstream of this
check is transport, and transport cannot un-send: the boundary is the client's
signed statement of what may cross it, and this is the code that enforces it.
*No other instrument catches it because* the unit table drives the validator with
a synthetic boundary, and this journey reads the boundary the store actually
holds -- which is the arrangement a real caller is in.

PRD §4 CUJ-9, four steps and one failure branch.
"""

import json
from pathlib import Path

import pytest

from adopt_cli.main import main
from adopt_detect import METADATA_ONLY, Answers, BoundaryView, declare_boundary, negotiate
from adopt_obs import AdoptError, ErrorCode, ExitCode
from adopt_policy import validate_envelope
from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

ENVELOPES = Path(__file__).resolve().parent.parent / "fixtures" / "envelopes"
ANSWERS = Path(__file__).resolve().parent.parent / "fixtures" / "answers"
REPOS = Path(__file__).resolve().parent.parent / "fixtures" / "repos"

T4 = Answers(artifact_access=True, deploy_signal=True, safe_interaction=True)


def _envelope(name: str) -> dict[str, object]:
    document = json.loads((ENVELOPES / f"{name}.json").read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


@pytest.mark.e2e
def test_content_under_metadata_only_is_rejected_against_the_stored_boundary(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """Steps 1-3, against the boundary the store holds rather than a synthetic one."""
    view = declare_boundary(
        s4_store.boundary(), scope=s4_scope, decision=negotiate(T4), archetype="ai"
    )
    assert view.permitted_outbound_categories == (METADATA_ONLY,)

    with pytest.raises(AdoptError) as caught:
        validate_envelope(_envelope("content_under_metadata_only"), view)
    assert caught.value.code is ErrorCode.ENVELOPE_CONTENT_UNDER_METADATA_ONLY

    # Step 4's other half: the same envelope validates once the boundary permits
    # a policy that carries content, and only because the boundary says so.
    assert s4_scope.system is not None and s4_scope.environment is not None
    widened = s4_store.boundary().declare(
        scope=s4_scope,
        tier="T4",
        knowledge_plane_location="customer",
        control_plane_location="vendor",
        permitted_outbound_categories=[METADATA_ONLY, "full_content"],
        contractual_approval_ref="MSA-2026-014 schedule 3",
        contractual=True,
    )
    permissive = BoundaryView.of(widened, archetype="ai")
    carrying = _envelope("content_under_metadata_only")
    carrying["content_policy"] = "full_content"
    validate_envelope(carrying, permissive)
    assert permissive.contractual_approval_ref == "MSA-2026-014 schedule 3"


@pytest.mark.e2e
def test_the_failure_branch_a_declared_policy_absent_from_the_boundary_is_rejected(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """CUJ-9's failure branch: **the boundary is the authority, not the caller.**

    *Fails when* an envelope can widen what may leave by declaring that it does.
    """
    view = declare_boundary(
        s4_store.boundary(), scope=s4_scope, decision=negotiate(T4), archetype="web"
    )
    with pytest.raises(AdoptError) as caught:
        validate_envelope(_envelope("policy_not_permitted"), view)
    assert caught.value.code is ErrorCode.ENVELOPE_POLICY_NOT_PERMITTED


@pytest.mark.e2e
def test_the_operator_facing_command_refuses_before_any_transport_exists(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`05` S6's seventh validation item, as the journey.

    No store, no scope, no transport: the check runs against the strictest
    default boundary, which is the posture a CI job validating an envelope is in.
    """
    assert (
        main(
            ["envelope", "validate", str(ENVELOPES / "content_under_metadata_only.json"), "--json"]
        )
        == ExitCode.POLICY_REFUSAL
    )
    envelope = json.loads(capsys.readouterr().err)
    assert envelope["error"]["code"] == ErrorCode.ENVELOPE_CONTENT_UNDER_METADATA_ONLY.value


@pytest.mark.e2e
def test_a_metadata_only_envelope_passes_the_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gate lets through what metadata-only is for.

    Without this row the suite would pass with a validator that rejected
    everything, which is safe and useless -- and the product would route around
    it within a sprint.
    """
    assert (
        main(["envelope", "validate", str(ENVELOPES / "valid_metadata_only.json"), "--json"])
        == ExitCode.SUCCESS
    )
    assert json.loads(capsys.readouterr().out)["valid"] is True


@pytest.mark.e2e
def test_validating_against_a_named_scope_reads_the_declared_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole journey through the CLI: `init` declares, `envelope` enforces.

    *Matters because* the two halves of this feature are written in different
    packages and wired in a third, and nothing else runs them in the order a real
    engagement does.
    """
    monkeypatch.setenv("ADOPT_STORE_PATH", str(tmp_path / ".adopt" / "store.db"))
    scope = "northwind/acme-erp/support-agent/prod"
    assert (
        main(
            [
                "init",
                str(REPOS / "ai" / "langgraph_support"),
                "--scope",
                scope,
                "--answers",
                str(ANSWERS / "t4.json"),
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )
    capsys.readouterr()

    assert (
        main(
            [
                "envelope",
                "validate",
                str(ENVELOPES / "content_under_metadata_only.json"),
                "--scope",
                scope,
                "--json",
            ]
        )
        == ExitCode.POLICY_REFUSAL
    )
    envelope = json.loads(capsys.readouterr().err)
    assert envelope["error"]["code"] == ErrorCode.ENVELOPE_CONTENT_UNDER_METADATA_ONLY.value
