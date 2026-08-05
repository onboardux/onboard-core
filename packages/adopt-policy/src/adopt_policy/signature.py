"""The signature-verification interface, with a null implementation.

PRD F11.4 and contracts §7: **a signature-verification interface ships; Build 0
does not implement key management.**

**Why a null implementation rather than no module.** The seam is what item 8
plugs a real verifier into, and a seam that does not exist until someone needs it
gets invented at the call site three times. This module fixes the signature, the
return type and -- most importantly -- what an unverified artifact means today.

**`verify_signature` returns `False`, never `True`.** That is the whole content
of the null implementation and it is the only defensible default: a stub that
returned `True` would make every probe revision appear signed, and the first real
verifier to land would then *reduce* apparent trust, which reads to an operator
as a regression. Returning `False` means "this build cannot verify signatures",
which is exactly true.

**The conformance test is what stops this drifting.** Any implementation --
including this one -- must satisfy `signature_conformance`, so item 8's verifier
is checked against the same statements before it replaces this.
"""

from typing import Final, Protocol

__all__ = [
    "NullSignatureVerifier",
    "SignatureVerifier",
    "signature_conformance",
    "verify_signature",
]

#: What the null implementation always returns. Named so that the conformance
#: test asserts against the constant rather than restating the literal.
NULL_VERIFICATION_RESULT: Final[bool] = False


class SignatureVerifier(Protocol):
    """The seam item 8 implements."""

    def verify_signature(self, revision_id: str, signature: str) -> bool:
        """Whether `signature` is a valid signature over `revision_id`.

        Returns `False` -- never raises -- for an unverifiable signature, an
        unknown key or an unsupported algorithm. A verifier that raised would
        make "not signed" and "verifier broken" the same event at the call site,
        and the caller's correct action differs between them.
        """
        ...


class NullSignatureVerifier:
    """Build 0's implementation: verifies nothing and says so."""

    def verify_signature(self, revision_id: str, signature: str) -> bool:
        return NULL_VERIFICATION_RESULT


def verify_signature(revision_id: str, signature: str) -> bool:
    """Contracts §7's module-level entry point. Delegates to the null verifier."""
    return NullSignatureVerifier().verify_signature(revision_id, signature)


def signature_conformance(verifier: SignatureVerifier) -> None:
    """Assert the statements every verifier must satisfy. Raises `AssertionError`.

    Three, and each is a real failure mode rather than a type check:

    1. **Total.** It returns a `bool` for any pair of strings, including empty
       ones and obvious garbage. A verifier that raised on a malformed signature
       would turn a forged artifact into a crash instead of a rejection.
    2. **Pure.** Two calls with the same arguments agree. A verifier whose answer
       drifted between calls could not be used in an audit.
    3. **Never true for an empty signature.** The absence of a signature is not a
       valid signature, whatever the algorithm.
    """
    cases = [("pdrev_01J8", "sig"), ("", ""), ("pdrev_01J8", "!!! not base64 !!!")]
    for revision_id, signature in cases:
        first = verifier.verify_signature(revision_id, signature)
        assert isinstance(first, bool), (  # noqa: S101 -- conformance assertion is the product
            f"verify_signature({revision_id!r}, {signature!r}) returned "
            f"{type(first).__name__}, not bool"
        )
        second = verifier.verify_signature(revision_id, signature)
        assert first == second, (  # noqa: S101
            f"verify_signature({revision_id!r}, {signature!r}) is not deterministic"
        )
    assert verifier.verify_signature("pdrev_01J8", "") is False, (  # noqa: S101
        "an empty signature verified; the absence of a signature is not a valid one"
    )
