"""The probe capability-manifest validator.

Contracts §7 and PRD F11. **Build 0 ships the schema, the validator and the
signature interface. The enforcing runner is item 8**, and that boundary is the
most important thing about this module: nothing here sandboxes, denies egress,
enforces a budget or verifies cleanup. It reads a declaration and refuses the
ones that are unsafe on their face.

**Six named rejections**, each its own registered code so the operator is told
what to fix rather than that something is wrong:

* a request target naming an **undeclared host**
* `network.deny_by_default: false`
* a missing or unknown `side_effect_policy`
* absent `runtime` limits
* absent `cost` limits
* `cleanup.required: false`

**`safe_path` is not validated here, and that is a design property.**
`probe_definition_revision.safe_path` is `NOT NULL` with a `CHECK` over
`('mock','sandbox','shadow')` (contracts §3), so a probe revision without a safe
path is *unrepresentable* -- not permitted-then-rejected. PRD F11.3 states it
exactly that way. `MANIFEST_MISSING_SAFE_PATH` therefore exists for the one case
the column cannot cover: a manifest submitted on its own, before any revision
exists to carry it, which is what `adopt probe manifest validate FILE` does.

**`approval_expires_at` is returned, never judged** (PRD F11.5, CUJ-8's failure
branch). Expiry enforcement is item 8. Returning the expiry and letting the
caller decide is the honest shape; treating an expired approval as invalid here
would put a control in the security story that Build 0 has not built.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "SAFE_PATHS",
    "SIDE_EFFECT_POLICIES",
    "ManifestVerdict",
    "validate_capability_manifest",
]

#: Contracts §3's `side_effect_policy`. Declared here rather than in the manifest
#: enum vocabulary because it is a *capability manifest* field, not a column: the
#: manifest document is stored whole in `probe_definition_revision.capability_
#: manifest` as TEXT, so no generated enum covers it.
SIDE_EFFECT_POLICIES: Final[frozenset[str]] = frozenset({"prohibited", "compensating", "declared"})

#: The `safe_path` vocabulary, from the manifest's own enum -- the same three
#: values the column's CHECK constraint carries.
SAFE_PATHS: Final[frozenset[str]] = frozenset({"mock", "sandbox", "shadow"})

_REQUIRED_RUNTIME: Final[tuple[str, ...]] = ("max_seconds", "max_memory_mb", "max_requests")
_REQUIRED_COST: Final[tuple[str, ...]] = ("max_model_calls", "max_tokens")


@dataclass(frozen=True, slots=True)
class ManifestVerdict:
    """What a valid manifest tells the caller.

    `approval_expires_at` is carried out verbatim and **not interpreted**. It is
    a string rather than a parsed datetime for the same reason: parsing implies a
    comparison, and the comparison is item 8's.
    """

    probe_id: str
    declared_hosts: tuple[str, ...]
    side_effect_policy: str
    approval_expires_at: str | None


def _reject(code: ErrorCode, message: str, hint: str) -> AdoptError:
    return AdoptError(code, message=message, hint=hint)


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _reject(
            ErrorCode.MANIFEST_INVALID,
            f"`{name}` must be a mapping, found {type(value).__name__}",
            f"Contracts §7 declares `{name}` as a block. See the worked example there.",
        )
    return value


def _declared_hosts(network: Mapping[str, Any]) -> tuple[str, ...]:
    allow = network.get("allow", [])
    if not isinstance(allow, Sequence) or isinstance(allow, str):
        raise _reject(
            ErrorCode.MANIFEST_INVALID,
            "`network.allow` must be a list of hostnames",
            "A single string reads as one host to a human and as a list of characters "
            "to a parser, which is how an allow-list silently becomes empty.",
        )
    return tuple(str(host) for host in allow)


def _check_targets(doc: Mapping[str, Any], declared: Sequence[str]) -> None:
    """Every request target must name a declared host.

    Contracts §7's first validator obligation. `request_targets` is the list a
    probe declares it will call; `network.allow` is what it is permitted to call.
    A target outside the allow-list is the exact case the sandbox exists to stop,
    and catching it at validation is cheaper than catching it at run time in a
    client environment.
    """
    targets = doc.get("request_targets", [])
    if not isinstance(targets, Sequence) or isinstance(targets, str):
        raise _reject(
            ErrorCode.MANIFEST_INVALID,
            "`request_targets` must be a list of hostnames",
            "Omit the key entirely if the probe declares no targets.",
        )
    permitted = set(declared)
    undeclared = sorted({str(target) for target in targets} - permitted)
    if undeclared:
        raise _reject(
            ErrorCode.MANIFEST_UNDECLARED_HOST,
            f"request target(s) {undeclared} are not in `network.allow`",
            f"Declared hosts are {sorted(permitted)}. Add the host to `network.allow` "
            "if the probe is meant to reach it -- the allow-list is the statement the "
            "sandbox enforces, and a target outside it is an undeclared egress.",
        )


def validate_capability_manifest(doc: Mapping[str, Any]) -> ManifestVerdict:
    """Validate a probe capability manifest. Raises on violation.

    Args:
        doc: The parsed manifest document (contracts §7).

    Returns:
        A `ManifestVerdict` carrying the declared hosts, the side-effect policy
        and `approval_expires_at` **unjudged**.

    Raises:
        AdoptError: ``MANIFEST_UNDECLARED_HOST`` when a request target is not in
            `network.allow`. ``MANIFEST_MISSING_SAFE_PATH`` when `safe_path` is
            absent or outside the declared vocabulary. ``MANIFEST_INVALID`` for
            every structural violation: `deny_by_default: false`, a missing or
            unknown `side_effect_policy`, absent `runtime` or `cost` limits, and
            `cleanup.required: false`.

    Note:
        The signature is `validate_capability_manifest(doc) -> None` in contracts
        §7. It returns a verdict instead, because §7's own next sentence requires
        `approval_expires_at` be **returned to the caller** -- and a function
        returning `None` has no way to do that. `02` §7 is corrected to match
        (CR-38's document set); the raising behaviour it specifies is unchanged.
    """
    if not isinstance(doc, Mapping):
        raise _reject(
            ErrorCode.MANIFEST_INVALID,
            f"the manifest must be a mapping, found {type(doc).__name__}",
            "Contracts §7 gives the shape.",
        )

    safe_path = doc.get("safe_path")
    if safe_path is None or safe_path not in SAFE_PATHS:
        raise _reject(
            ErrorCode.MANIFEST_MISSING_SAFE_PATH,
            f"`safe_path` is {safe_path!r}, not one of {sorted(SAFE_PATHS)}",
            "A probe without a safe execution path runs against the real system. "
            "On a stored revision the NOT NULL column makes this unrepresentable; a "
            "standalone manifest is the one place it has to be checked.",
        )

    network = _require_mapping(doc.get("network"), "network")
    if network.get("deny_by_default") is not True:
        raise _reject(
            ErrorCode.MANIFEST_INVALID,
            f"`network.deny_by_default` is {network.get('deny_by_default')!r}, not true",
            "Deny-by-default is not a setting. An allow-list only means anything when "
            "everything not on it is refused.",
        )
    declared = _declared_hosts(network)
    _check_targets(doc, declared)

    policy = doc.get("side_effect_policy")
    if policy not in SIDE_EFFECT_POLICIES:
        raise _reject(
            ErrorCode.MANIFEST_INVALID,
            f"`side_effect_policy` is {policy!r}, not one of {sorted(SIDE_EFFECT_POLICIES)}",
            "A probe that does not say whether it may cause side effects is a probe "
            "nobody can approve.",
        )

    for block, required in (("runtime", _REQUIRED_RUNTIME), ("cost", _REQUIRED_COST)):
        limits = _require_mapping(doc.get(block), block)
        missing = [key for key in required if limits.get(key) is None]
        if missing:
            raise _reject(
                ErrorCode.MANIFEST_INVALID,
                f"`{block}` is missing limit(s) {missing}",
                f"Every {block} limit is required. An absent limit is an unbounded one, "
                "and an unbounded probe in a client environment is the failure the "
                "manifest exists to prevent.",
            )

    cleanup = _require_mapping(doc.get("cleanup"), "cleanup")
    if cleanup.get("required") is not True:
        raise _reject(
            ErrorCode.MANIFEST_INVALID,
            f"`cleanup.required` is {cleanup.get('required')!r}, not true",
            "A probe that need not clean up leaves state in a client system that "
            "nobody owns. Item 8 verifies the cleanup; Build 0 refuses to accept a "
            "manifest that never promised one.",
        )

    expires = doc.get("approval_expires_at")
    return ManifestVerdict(
        probe_id=str(doc.get("probe_id", "")),
        declared_hosts=declared,
        side_effect_policy=str(policy),
        approval_expires_at=None if expires is None else str(expires),
    )
