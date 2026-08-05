"""The outbound-envelope validator. Fails closed.

Contracts §8 and PRD F12. This is the gate: it is what stands between a client's
content and anything that could carry it out of their environment, and it runs
**before any transport exists to send it** (item 9 owns transport).

**The boundary is the authority, never the caller's declaration** (§8 rule 3,
F12.5). An envelope declaring `content_policy: full_content` is rejected unless
the *stored boundary* lists it, because the boundary is the artifact the client
agreed to and the envelope is a claim by whatever code constructed it.

**Fails closed at every branch.** An unparseable envelope, an unknown policy, a
missing scope id, a payload that is not an object -- all rejections. §8 rule 5
says so, and the reason is asymmetric cost: a wrongly rejected envelope is a
support ticket, and a wrongly permitted one is client content that has left.

**What counts as content is derived, not listed** -- see `content_fields`. The
deny-list widens by itself when a `md`/`text` column is added to an exportable
table, which is the only direction a gate like this may move without a human.
"""

from collections.abc import Mapping
from typing import Any, Final

from adopt_detect import METADATA_ONLY, BoundaryView
from adopt_obs import AdoptError, ErrorCode
from adopt_policy.content_fields import find_content_fields
from adopt_schema.manifest import Manifest

__all__ = ["REQUIRED_ENVELOPE_KEYS", "validate_envelope"]

#: Contracts §8's envelope shape. `payload` is required and may be empty;
#: `content_policy` is the one key with a default (§8 rule 1).
REQUIRED_ENVELOPE_KEYS: Final[tuple[str, ...]] = (
    "schema_version",
    "firm_id",
    "engagement_id",
    "system_id",
    "environment_id",
    "event_type",
    "occurred_at",
    "payload",
)


def _reject(code: ErrorCode, message: str, hint: str) -> AdoptError:
    return AdoptError(code, message=message, hint=hint)


def validate_envelope(
    env: Mapping[str, Any],
    boundary: BoundaryView,
    *,
    manifest: Manifest | None = None,
) -> None:
    """Validate one outbound envelope against a boundary. Raises on violation.

    Args:
        env: The parsed envelope (contracts §8).
        boundary: The **authority** for what may leave. Not a hint.
        manifest: Injected manifest, for tests asserting the derived deny-list.

    Raises:
        AdoptError: ``ENVELOPE_CONTENT_UNDER_METADATA_ONLY`` when the payload
            carries a content field under `metadata_only`.
            ``ENVELOPE_POLICY_NOT_PERMITTED`` when the declared policy is absent
            from the boundary's permitted list. ``MANIFEST_INVALID`` when the
            envelope is structurally unusable -- an unparseable envelope is
            rejected rather than partially validated (§8 rule 5).

    Note:
        **A non-`metadata_only` policy is checked against the boundary first, and
        the content check is then skipped.** That is not a hole: a policy the
        boundary permits is by definition one the client contractually agreed may
        carry what it carries, and `contractual_approval_ref` on the boundary row
        is the reference to that agreement. Re-applying the metadata-only rule to
        a policy that exists precisely to carry content would make every wider
        policy unusable and the boundary's permitted list decorative.
    """
    if not isinstance(env, Mapping):
        raise _reject(
            ErrorCode.MANIFEST_INVALID,
            f"the envelope must be a mapping, found {type(env).__name__}",
            "Contracts §8 gives the shape. An envelope that cannot be parsed is "
            "rejected rather than partially validated.",
        )

    missing = [key for key in REQUIRED_ENVELOPE_KEYS if key not in env]
    if missing:
        raise _reject(
            ErrorCode.MANIFEST_INVALID,
            f"the envelope is missing {missing}",
            "Every scope id is required: an envelope that does not say which firm, "
            "engagement, system and environment it came from cannot be checked "
            "against any boundary at all.",
        )

    payload = env["payload"]
    if not isinstance(payload, Mapping):
        raise _reject(
            ErrorCode.MANIFEST_INVALID,
            f"`payload` must be a mapping, found {type(payload).__name__}",
            "An empty payload is `{}`. A payload that is a bare string cannot be "
            "inspected field by field, so it cannot be cleared to leave.",
        )

    policy = env.get("content_policy", METADATA_ONLY)
    if not isinstance(policy, str):
        raise _reject(
            ErrorCode.MANIFEST_INVALID,
            f"`content_policy` must be a string, found {type(policy).__name__}",
            f"Omit it to take the default, {METADATA_ONLY}.",
        )

    if not boundary.permits(policy):
        raise _reject(
            ErrorCode.ENVELOPE_POLICY_NOT_PERMITTED,
            f"content policy {policy!r} is not in the boundary's permitted list "
            f"{list(boundary.permitted_outbound_categories)}",
            "The boundary is the authority, not the envelope's declaration. Widening "
            "what may leave is a contract amendment recorded on the boundary row "
            "with `contractual_approval_ref`, not a field a sender can set.",
        )

    if policy != METADATA_ONLY:
        return

    violations = find_content_fields(payload, manifest=manifest)
    if violations:
        raise _reject(
            ErrorCode.ENVELOPE_CONTENT_UNDER_METADATA_ONLY,
            f"payload carries content field(s) {list(violations)} under {METADATA_ONLY}",
            "Under metadata-only, an envelope may carry counts, ids, timestamps and "
            "states -- never the text itself. Send a reference and let the reader "
            "fetch it inside the boundary, or declare a policy the boundary permits.",
        )
