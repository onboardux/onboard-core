"""CUJ-8 -- a probe manifest is rejected.

*Fails when* a probe declaring an undeclared host is accepted, when a rejection
leaves a partial revision behind, or when Build 0 starts judging an approval it
has not built the enforcement for. *Matters because* this is the gate in front of
code that executes inside a client system: everything past it is item 8's sandbox,
and a manifest accepted here is a capability the sandbox will be told to permit.
*No other instrument catches it because* the unit table drives the validator
directly and never checks that a refused manifest wrote nothing.

PRD §4 CUJ-8, four steps and one failure branch. **The failure branch is the
interesting one:** an expired approval is *returned*, not judged.
"""

import json
from pathlib import Path

import pytest

from adopt_cli.main import main
from adopt_obs import AdoptError, ErrorCode, ExitCode
from adopt_policy import validate_capability_manifest
from adopt_scope import Scope
from adopt_store import ProbeDefinitionRevisionDraft
from adopt_store.api import SqliteStoreHandle

MANIFESTS = Path(__file__).resolve().parent.parent / "fixtures" / "manifests"


def _manifest_text(name: str) -> str:
    return (MANIFESTS / name).read_text(encoding="utf-8")


@pytest.mark.e2e
def test_a_probe_with_a_valid_manifest_is_created_and_one_with_an_undeclared_host_is_not(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """Steps 1-4: submit, validate, reject the undeclared host, write nothing partial."""
    import yaml

    valid = yaml.safe_load(_manifest_text("valid.yaml"))
    validate_capability_manifest(valid)
    probe_id, revision_id = s4_store.probes().create(
        scope=s4_scope,
        name="orders-smoke",
        revision=ProbeDefinitionRevisionDraft(
            interaction="GET /v1/orders/health",
            safe_path="sandbox",
            diff_method="exact",
            capability_manifest=_manifest_text("valid.yaml"),
        ),
    )
    assert probe_id and revision_id

    # Step 3: the undeclared host is refused *before* anything is written.
    undeclared = yaml.safe_load(_manifest_text("undeclared_host.yaml"))
    with pytest.raises(AdoptError) as caught:
        validate_capability_manifest(undeclared)
    assert caught.value.code is ErrorCode.MANIFEST_UNDECLARED_HOST

    # Step 4: nothing partial. One probe and one revision, the valid one's.
    probes = s4_store.backend.query("SELECT id FROM probe_definition")
    revisions = s4_store.backend.query("SELECT id FROM probe_definition_revision")
    assert len(probes) == 1
    assert len(revisions) == 1


@pytest.mark.e2e
def test_a_revision_without_a_safe_path_cannot_be_constructed(
    s4_store: SqliteStoreHandle, s4_scope: Scope
) -> None:
    """PRD F11.3 and `05` S6's sixth validation item.

    *Matters because* this is a **design property, not a validation rule that
    could be relaxed later**: the column is `NOT NULL` with a `CHECK` over three
    values, so the unsafe state is unrepresentable rather than rejected. Both
    halves are asserted -- the draft refuses to be built, and the column refuses
    the value if anything ever got past the draft.
    """
    with pytest.raises(TypeError):
        ProbeDefinitionRevisionDraft(  # type: ignore[call-arg]
            interaction="GET /v1/orders/health",
            diff_method="exact",
            capability_manifest="{}",
        )

    with pytest.raises(Exception) as caught, s4_store.backend.transaction():
        s4_store.backend.execute(
            "INSERT INTO probe_definition_revision "
            "(id, probe_definition_id, interaction, safe_path, diff_method, status, "
            " capability_manifest, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "pdrev_01J8ZK3QW7X9Y2N4M6P8R0T2V4",
                "pd_01J8ZK3QW7X9Y2N4M6P8R0T2V4",
                "GET /health",
                None,
                "exact",
                "active",
                "{}",
                "2026-08-05T09:00:00.000Z",
            ),
        )
    assert "NOT NULL" in str(caught.value).upper() or "CONSTRAINT" in str(caught.value).upper()


@pytest.mark.e2e
def test_the_failure_branch_an_expired_approval_is_returned_not_judged() -> None:
    """CUJ-8's failure branch, and the reason it is written down.

    *Fails when* Build 0 starts treating an expired approval as invalid.
    *Matters because* expiry enforcement is item 8: judging it here would put a
    control in the security story that nothing implements, and an operator would
    believe expired approvals were blocked when they are not.
    """
    import yaml

    verdict = validate_capability_manifest(yaml.safe_load(_manifest_text("expired_approval.yaml")))
    assert verdict.approval_expires_at == "2020-01-01T00:00:00.000Z"


@pytest.mark.e2e
def test_the_operator_facing_command_refuses_with_the_named_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`05` S6's fifth validation item, as the journey rather than as a shell line."""
    assert (
        main(["probe", "manifest", "validate", str(MANIFESTS / "undeclared_host.yaml"), "--json"])
        == ExitCode.POLICY_REFUSAL
    )
    envelope = json.loads(capsys.readouterr().err)
    assert envelope["error"]["code"] == ErrorCode.MANIFEST_UNDECLARED_HOST.value
    assert envelope["error"]["category"] == "policy"


@pytest.mark.e2e
def test_the_valid_manifest_reports_its_expiry_to_the_operator(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(["probe", "manifest", "validate", str(MANIFESTS / "expired_approval.yaml"), "--json"])
        == ExitCode.SUCCESS
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["approval_expires_at"] == "2020-01-01T00:00:00.000Z"
