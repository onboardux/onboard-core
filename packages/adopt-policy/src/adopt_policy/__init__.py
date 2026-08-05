"""Capability-manifest validation, envelope validation, the signature interface.

Implementation spec §4.12, PRD F11 and F12, contracts §7 and §8, sprint S6.

**Both validators raise; neither returns a soft warning.** A validator that
returned a warning would leave the decision at the call site, and there are as
many call sites as there are callers. `02` §8's `validate_envelope` is the egress
gate for a client environment; it either refuses or it does not run.

**The observability boundary is the authority for permitted outbound policies --
never the caller's declaration.** That is contracts §8 rule 3, and it is why
`validate_envelope` takes a `BoundaryView` rather than reading a config key: the
boundary is the artifact the client agreed to, and widening it is a contract
amendment recorded on the row.

**Build 0 does not judge approval expiry.** `validate_capability_manifest`
returns `approval_expires_at` and stops. Enforcement is item 8 (PRD F11.5), and
implementing it early would put a control in the security story that does not
exist.
"""

from adopt_policy.capability_manifest import (
    SAFE_PATHS,
    SIDE_EFFECT_POLICIES,
    ManifestVerdict,
    validate_capability_manifest,
)
from adopt_policy.content_fields import content_fields, find_content_fields
from adopt_policy.envelope import REQUIRED_ENVELOPE_KEYS, validate_envelope
from adopt_policy.signature import (
    NullSignatureVerifier,
    SignatureVerifier,
    signature_conformance,
    verify_signature,
)

__all__ = [
    "REQUIRED_ENVELOPE_KEYS",
    "SAFE_PATHS",
    "SIDE_EFFECT_POLICIES",
    "ManifestVerdict",
    "NullSignatureVerifier",
    "SignatureVerifier",
    "content_fields",
    "find_content_fields",
    "signature_conformance",
    "validate_capability_manifest",
    "validate_envelope",
    "verify_signature",
]
