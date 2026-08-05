"""The capability-manifest validator: one row per named rejection.

*Fails when* a manifest that declares an unsafe capability starts validating, or
when a rejection stops carrying the code that says which one. *Matters because*
this is what an operator submits before a probe is ever run in a client
environment, and every rejection here is a control that item 8's runner will
enforce -- accepting a manifest that never promised cleanup means state left in a
client system nobody owns. *No other instrument catches it because* CUJ-8 walks
one rejection and the schema's `NOT NULL` covers only the stored-revision case.

**Each fixture file is the test case.** A manifest is a document an operator
writes, so the instrument is a document rather than a dict built in Python --
which also makes every row runnable through `adopt probe manifest validate`.
"""

from pathlib import Path

import pytest
import yaml

from adopt_obs import AdoptError, ErrorCode
from adopt_policy import (
    NullSignatureVerifier,
    signature_conformance,
    validate_capability_manifest,
    verify_signature,
)

MANIFESTS = Path(__file__).resolve().parent.parent / "fixtures" / "manifests"

#: (fixture, expected code, the failure the rejection prevents).
REJECTIONS: list[tuple[str, ErrorCode, str]] = [
    (
        "undeclared_host.yaml",
        ErrorCode.MANIFEST_UNDECLARED_HOST,
        "a probe reaching a host the allow-list never named is undeclared egress",
    ),
    (
        "missing_safe_path.yaml",
        ErrorCode.MANIFEST_MISSING_SAFE_PATH,
        "a probe with no safe path runs against the real system",
    ),
    (
        "unknown_safe_path.yaml",
        ErrorCode.MANIFEST_MISSING_SAFE_PATH,
        "'production' is not a safe path however confidently it is declared",
    ),
    (
        "deny_by_default_false.yaml",
        ErrorCode.MANIFEST_INVALID,
        "an allow-list only means anything when everything else is refused",
    ),
    (
        "unknown_side_effect_policy.yaml",
        ErrorCode.MANIFEST_INVALID,
        "an invented policy value would be approved by a human reading a word they know",
    ),
    (
        "missing_side_effect_policy.yaml",
        ErrorCode.MANIFEST_INVALID,
        "a probe that does not say whether it causes side effects cannot be approved",
    ),
    (
        "missing_runtime_limits.yaml",
        ErrorCode.MANIFEST_INVALID,
        "an absent limit is an unbounded one",
    ),
    (
        "missing_cost_block.yaml",
        ErrorCode.MANIFEST_INVALID,
        "no cost ceiling means an unbounded spend in a client environment",
    ),
    (
        "cleanup_not_required.yaml",
        ErrorCode.MANIFEST_INVALID,
        "state left in a client system that nobody owns",
    ),
    (
        "not_a_mapping.yaml",
        ErrorCode.MANIFEST_INVALID,
        "an unparseable declaration is not a permissive one",
    ),
]


def _load(name: str) -> object:
    return yaml.safe_load((MANIFESTS / name).read_text(encoding="utf-8"))


@pytest.mark.unit
@pytest.mark.parametrize(("fixture", "code", "prevents"), REJECTIONS)
def test_manifest_rejection(fixture: str, code: ErrorCode, prevents: str) -> None:
    with pytest.raises(AdoptError) as caught:
        validate_capability_manifest(_load(fixture))  # type: ignore[arg-type]
    assert caught.value.code is code, prevents


@pytest.mark.unit
def test_the_worked_example_from_contracts_validates() -> None:
    """*Fails when* the validator rejects the document the contract shows.

    A gate that refuses its own specification's example is a gate whose users
    conclude the specification is wrong.
    """
    verdict = validate_capability_manifest(_load("valid.yaml"))  # type: ignore[arg-type]
    assert verdict.declared_hosts == ("api.sandbox.customer.example",)
    assert verdict.side_effect_policy == "prohibited"


@pytest.mark.unit
def test_an_expired_approval_is_returned_and_not_judged() -> None:
    """PRD F11.5 and CUJ-8's failure branch, which is the whole point of the row.

    *Fails when* Build 0 starts enforcing expiry. *Matters because* expiry
    enforcement is item 8: judging it here would put a control in the security
    story that does not exist, and an operator would believe expired approvals
    were being blocked when nothing blocks them.
    """
    verdict = validate_capability_manifest(_load("expired_approval.yaml"))  # type: ignore[arg-type]
    assert verdict.approval_expires_at == "2020-01-01T00:00:00.000Z"


@pytest.mark.unit
def test_a_manifest_with_no_request_targets_is_fine() -> None:
    """Declaring no targets is not the same as declaring an undeclared one."""
    document = _load("valid.yaml")
    assert isinstance(document, dict)
    document.pop("request_targets")
    assert validate_capability_manifest(document).declared_hosts


@pytest.mark.unit
def test_a_string_allow_list_is_refused() -> None:
    """*Fails when* `network.allow: api.example` is read as a list of characters.

    A single string reads as one host to a human and as a sequence of one-letter
    hosts to a parser, which is how an allow-list silently matches nothing.
    """
    document = _load("valid.yaml")
    assert isinstance(document, dict)
    document["network"]["allow"] = "api.sandbox.customer.example"
    with pytest.raises(AdoptError) as caught:
        validate_capability_manifest(document)
    assert caught.value.code is ErrorCode.MANIFEST_INVALID


@pytest.mark.unit
def test_the_null_verifier_satisfies_the_signature_conformance() -> None:
    """The conformance test contracts §7 requires, run against Build 0's verifier.

    *Matters because* item 8's real verifier replaces this one, and the suite is
    what checks the replacement makes the same three statements before it lands.
    """
    signature_conformance(NullSignatureVerifier())


@pytest.mark.unit
def test_the_null_verifier_never_reports_a_valid_signature() -> None:
    """*Fails when* a stub starts returning `True`.

    *Matters because* a stub that verified everything would make every probe
    revision appear signed, and the first real verifier would then *reduce*
    apparent trust -- which reads to an operator as a regression rather than as
    the moment verification began.
    """
    assert verify_signature("pdrev_01J8", "any signature at all") is False
