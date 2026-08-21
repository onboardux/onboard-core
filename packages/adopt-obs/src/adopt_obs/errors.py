"""Typed errors, the code registry, categories, and the exit-code mapping.

Every error the programme raises across a package boundary is an
:class:`AdoptError` carrying a code from :class:`ErrorCode`. The registry below
is the executable half of ``02-contracts-build0.md`` §13;
``scripts/error_registry_sync.py`` fails the build when the two disagree in
either direction, so a code cannot be added to the code without being
documented, nor documented without being implemented.

**Category is derived from the code, not supplied by the caller.** The
implementation spec's signature ``AdoptError(code, category, message, hint)``
still works, but passing a category that contradicts the registry raises rather
than being accepted -- one code meaning two categories on two call sites is
exactly the drift the registry exists to prevent.

The exit-code mapping lives here rather than in the CLI because it is a
function of the category, and the category is owned here. A second copy in the
CLI would be a second place to get it wrong.
"""

from enum import StrEnum
from typing import Final

__all__ = [
    "CATEGORY_EXIT_CODES",
    "ERROR_CATEGORIES",
    "AdoptError",
    "ErrorCategory",
    "ErrorCode",
    "ExitCode",
    "exit_code_for",
]


class ErrorCategory(StrEnum):
    """The five categories in contracts §13."""

    USAGE = "usage"
    POLICY = "policy"
    INTEGRITY = "integrity"
    TRANSIENT = "transient"
    INTERNAL = "internal"


class ExitCode:
    """Stable process exit codes (contracts §13, PRD F16.7).

    Not an enum: these are returned to a shell, compared by integrators in
    scripts, and must stay plain integers at every boundary.
    """

    SUCCESS: Final[int] = 0
    OPERATIONAL_FAILURE: Final[int] = 1
    USAGE_ERROR: Final[int] = 2
    # const-sync: ok -- a contracts §13 exit code, fixed by contract, not a tunable.
    POLICY_REFUSAL: Final[int] = 3
    DEGRADED_WITH_FINDINGS: Final[int] = 4


CATEGORY_EXIT_CODES: Final[dict[ErrorCategory, int]] = {
    ErrorCategory.USAGE: ExitCode.USAGE_ERROR,
    ErrorCategory.POLICY: ExitCode.POLICY_REFUSAL,
    ErrorCategory.INTEGRITY: ExitCode.OPERATIONAL_FAILURE,
    ErrorCategory.INTERNAL: ExitCode.OPERATIONAL_FAILURE,
    # A transient failure is an operational failure to the caller. It is
    # retriable, but the process still did not do what it was asked.
    ErrorCategory.TRANSIENT: ExitCode.OPERATIONAL_FAILURE,
}


class ErrorCode(StrEnum):
    """Every error code in contracts §13. Adding one here without adding it
    there (or the reverse) fails `error-registry-sync`."""

    ADOPT_OFFLINE_DENIED = "ADOPT_OFFLINE_DENIED"
    ADOPT_CONFIG_UNRESOLVED = "ADOPT_CONFIG_UNRESOLVED"

    SCHEMA_NON_ADDITIVE = "SCHEMA_NON_ADDITIVE"
    SCHEMA_GENERATED_DRIFT = "SCHEMA_GENERATED_DRIFT"
    SCHEMA_VERSION_TOO_NEW = "SCHEMA_VERSION_TOO_NEW"
    SCHEMA_MIGRATION_PENDING = "SCHEMA_MIGRATION_PENDING"
    SCHEMA_MIGRATION_FAILED = "SCHEMA_MIGRATION_FAILED"
    SCHEMA_ASSETS_MISSING = "SCHEMA_ASSETS_MISSING"

    STORE_READ_ONLY = "STORE_READ_ONLY"

    SCOPE_SLUG_INVALID = "SCOPE_SLUG_INVALID"
    SCOPE_SLUG_IMMUTABLE = "SCOPE_SLUG_IMMUTABLE"
    SCOPE_SLUG_REUSED = "SCOPE_SLUG_REUSED"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"

    URI_MALFORMED = "URI_MALFORMED"
    URI_TOO_LONG = "URI_TOO_LONG"
    URI_SCHEME_UNKNOWN = "URI_SCHEME_UNKNOWN"
    URI_DOUBLE_ENCODED = "URI_DOUBLE_ENCODED"

    REVISION_CHAIN_FORK = "REVISION_CHAIN_FORK"
    REVISION_IMMUTABLE = "REVISION_IMMUTABLE"
    REVISION_HEAD_DANGLING = "REVISION_HEAD_DANGLING"

    COVERAGE_CACHE_DISAGREEMENT = "COVERAGE_CACHE_DISAGREEMENT"
    FRESHNESS_SENSOR_DEGRADED = "FRESHNESS_SENSOR_DEGRADED"

    EXPORT_VERSION_UNSUPPORTED = "EXPORT_VERSION_UNSUPPORTED"
    EXPORT_DIGEST_MISMATCH = "EXPORT_DIGEST_MISMATCH"
    EXPORT_ROUNDTRIP_UNSTABLE = "EXPORT_ROUNDTRIP_UNSTABLE"
    EXPORT_SCOPE_AMBIGUOUS = "EXPORT_SCOPE_AMBIGUOUS"
    EXPORT_BUNDLE_MALFORMED = "EXPORT_BUNDLE_MALFORMED"
    EXPORT_TARGET_NOT_EMPTY = "EXPORT_TARGET_NOT_EMPTY"

    DETECT_AMBIGUOUS = "DETECT_AMBIGUOUS"
    TIER_INSUFFICIENT = "TIER_INSUFFICIENT"
    TIER_DECLINE_RECOMMENDED = "TIER_DECLINE_RECOMMENDED"
    TIER_ANSWERS_INVALID = "TIER_ANSWERS_INVALID"

    MANIFEST_UNDECLARED_HOST = "MANIFEST_UNDECLARED_HOST"
    MANIFEST_MISSING_SAFE_PATH = "MANIFEST_MISSING_SAFE_PATH"
    MANIFEST_INVALID = "MANIFEST_INVALID"

    ENVELOPE_CONTENT_UNDER_METADATA_ONLY = "ENVELOPE_CONTENT_UNDER_METADATA_ONLY"
    ENVELOPE_POLICY_NOT_PERMITTED = "ENVELOPE_POLICY_NOT_PERMITTED"

    AGENT_ADAPTER_UNKNOWN = "AGENT_ADAPTER_UNKNOWN"
    AGENT_OFFLINE_ADAPTER_DENIED = "AGENT_OFFLINE_ADAPTER_DENIED"
    AGENT_BUDGET_EXHAUSTED = "AGENT_BUDGET_EXHAUSTED"
    AGENT_OUTPUT_SCHEMA = "AGENT_OUTPUT_SCHEMA"
    AGENT_PROVIDER_ERROR = "AGENT_PROVIDER_ERROR"

    WORKFLOW_STEP_EXHAUSTED = "WORKFLOW_STEP_EXHAUSTED"
    WORKFLOW_BODY_IMPURE = "WORKFLOW_BODY_IMPURE"
    WORKFLOW_DUPLICATE_START = "WORKFLOW_DUPLICATE_START"

    LICENCE_POLICY_VIOLATION = "LICENCE_POLICY_VIOLATION"

    MAP_NO_PACK_FOR_ARCHETYPE = "MAP_NO_PACK_FOR_ARCHETYPE"
    MAP_TREE_TOO_LARGE = "MAP_TREE_TOO_LARGE"
    MAP_EXPECTED_IDENTITY_MISSING = "MAP_EXPECTED_IDENTITY_MISSING"
    MAP_EXPECTED_LIST_UNREADABLE = "MAP_EXPECTED_LIST_UNREADABLE"

    KNOWLEDGE_SOURCE_UNREADABLE = "KNOWLEDGE_SOURCE_UNREADABLE"
    BIND_TARGET_NOT_FOUND = "BIND_TARGET_NOT_FOUND"
    REVIEW_ITEM_NOT_FOUND = "REVIEW_ITEM_NOT_FOUND"
    REVIEW_ITEM_RESOLVED = "REVIEW_ITEM_RESOLVED"


#: Code -> category, verbatim from the contracts §13 table.
ERROR_CATEGORIES: Final[dict[ErrorCode, ErrorCategory]] = {
    ErrorCode.ADOPT_OFFLINE_DENIED: ErrorCategory.POLICY,
    ErrorCode.ADOPT_CONFIG_UNRESOLVED: ErrorCategory.USAGE,
    ErrorCode.SCHEMA_NON_ADDITIVE: ErrorCategory.POLICY,
    ErrorCode.SCHEMA_GENERATED_DRIFT: ErrorCategory.INTEGRITY,
    ErrorCode.SCHEMA_VERSION_TOO_NEW: ErrorCategory.POLICY,
    ErrorCode.SCHEMA_MIGRATION_PENDING: ErrorCategory.USAGE,
    ErrorCode.SCHEMA_MIGRATION_FAILED: ErrorCategory.INTEGRITY,
    # The installed artefact does not carry the schema assets it needs. Integrity
    # rather than usage: the operator did nothing wrong and no flag fixes it --
    # what they hold was built incompletely.
    ErrorCode.SCHEMA_ASSETS_MISSING: ErrorCategory.INTEGRITY,
    ErrorCode.STORE_READ_ONLY: ErrorCategory.POLICY,
    ErrorCode.SCOPE_SLUG_INVALID: ErrorCategory.USAGE,
    ErrorCode.SCOPE_SLUG_IMMUTABLE: ErrorCategory.POLICY,
    ErrorCode.SCOPE_SLUG_REUSED: ErrorCategory.POLICY,
    ErrorCode.SCOPE_VIOLATION: ErrorCategory.POLICY,
    ErrorCode.URI_MALFORMED: ErrorCategory.USAGE,
    ErrorCode.URI_TOO_LONG: ErrorCategory.USAGE,
    ErrorCode.URI_SCHEME_UNKNOWN: ErrorCategory.USAGE,
    ErrorCode.URI_DOUBLE_ENCODED: ErrorCategory.USAGE,
    ErrorCode.REVISION_CHAIN_FORK: ErrorCategory.INTEGRITY,
    ErrorCode.REVISION_IMMUTABLE: ErrorCategory.POLICY,
    ErrorCode.REVISION_HEAD_DANGLING: ErrorCategory.INTEGRITY,
    ErrorCode.COVERAGE_CACHE_DISAGREEMENT: ErrorCategory.INTEGRITY,
    ErrorCode.FRESHNESS_SENSOR_DEGRADED: ErrorCategory.POLICY,
    ErrorCode.EXPORT_VERSION_UNSUPPORTED: ErrorCategory.POLICY,
    ErrorCode.EXPORT_DIGEST_MISMATCH: ErrorCategory.INTEGRITY,
    ErrorCode.EXPORT_ROUNDTRIP_UNSTABLE: ErrorCategory.INTEGRITY,
    ErrorCode.EXPORT_SCOPE_AMBIGUOUS: ErrorCategory.POLICY,
    ErrorCode.EXPORT_BUNDLE_MALFORMED: ErrorCategory.INTEGRITY,
    ErrorCode.EXPORT_TARGET_NOT_EMPTY: ErrorCategory.POLICY,
    ErrorCode.DETECT_AMBIGUOUS: ErrorCategory.USAGE,
    ErrorCode.TIER_INSUFFICIENT: ErrorCategory.POLICY,
    ErrorCode.TIER_DECLINE_RECOMMENDED: ErrorCategory.POLICY,
    ErrorCode.TIER_ANSWERS_INVALID: ErrorCategory.USAGE,
    ErrorCode.MANIFEST_UNDECLARED_HOST: ErrorCategory.POLICY,
    ErrorCode.MANIFEST_MISSING_SAFE_PATH: ErrorCategory.POLICY,
    ErrorCode.MANIFEST_INVALID: ErrorCategory.USAGE,
    ErrorCode.ENVELOPE_CONTENT_UNDER_METADATA_ONLY: ErrorCategory.POLICY,
    ErrorCode.ENVELOPE_POLICY_NOT_PERMITTED: ErrorCategory.POLICY,
    ErrorCode.AGENT_ADAPTER_UNKNOWN: ErrorCategory.USAGE,
    ErrorCode.AGENT_OFFLINE_ADAPTER_DENIED: ErrorCategory.POLICY,
    ErrorCode.AGENT_BUDGET_EXHAUSTED: ErrorCategory.POLICY,
    ErrorCode.AGENT_OUTPUT_SCHEMA: ErrorCategory.INTERNAL,
    ErrorCode.AGENT_PROVIDER_ERROR: ErrorCategory.TRANSIENT,
    ErrorCode.WORKFLOW_STEP_EXHAUSTED: ErrorCategory.INTERNAL,
    ErrorCode.WORKFLOW_BODY_IMPURE: ErrorCategory.POLICY,
    ErrorCode.WORKFLOW_DUPLICATE_START: ErrorCategory.USAGE,
    ErrorCode.LICENCE_POLICY_VIOLATION: ErrorCategory.POLICY,
    ErrorCode.MAP_NO_PACK_FOR_ARCHETYPE: ErrorCategory.USAGE,
    # A bound refusing to proceed, not a mistake the operator made: a client
    # monorepo is allowed to be enormous, it is just not allowed to make the
    # walk unbounded. The fix is to narrow what was asked for, so this reads as
    # a refusal (exit 3) rather than a malformed invocation.
    ErrorCode.MAP_TREE_TOO_LARGE: ErrorCategory.POLICY,
    # Never raised -- see `_NEVER_RAISED`. The category is recorded because the
    # registry requires one for every code, and integrity is what a missing
    # expected identity is a statement about: the map does not contain something
    # asserted to be in it.
    ErrorCode.MAP_EXPECTED_IDENTITY_MISSING: ErrorCategory.INTEGRITY,
    # Its own code rather than a reused one, on CR-38's precedent: an
    # unreadable expected-list, an invalid answers document and an unresolved
    # config key are three inputs needing three different fixes, and one code
    # covering all of them says only "something you supplied is wrong". Usage,
    # so it exits 2 -- and never 4, which would report a missing *file* as a
    # perfect recall floor over nothing.
    ErrorCode.MAP_EXPECTED_LIST_UNREADABLE: ErrorCategory.USAGE,
    # Build 2. A document named for ingest that cannot be read is refused rather
    # than skipped, on `MAP_EXPECTED_LIST_UNREADABLE`'s precedent: silently
    # ingesting nothing from an unreadable path reports a successful run over a
    # corpus that is not there.
    ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE: ErrorCategory.USAGE,
    ErrorCode.BIND_TARGET_NOT_FOUND: ErrorCategory.USAGE,
    ErrorCode.REVIEW_ITEM_NOT_FOUND: ErrorCategory.USAGE,
    # Policy rather than usage: the id was right and the operator did nothing
    # malformed -- the queue is refusing to record a second disposition over a
    # decision already made. Confirming twice would bind twice, and rejecting
    # something already confirmed would leave a binding whose review says it was
    # rejected.
    ErrorCode.REVIEW_ITEM_RESOLVED: ErrorCategory.POLICY,
}

#: Codes that are **never raised** (contracts §13). Constructing one as an
#: exception is a programming error, not a runtime condition, so it is refused at
#: construction.
#:
#: * `AGENT_BUDGET_EXHAUSTED` is returned as `AgentResult.status`.
#: * `MAP_EXPECTED_IDENTITY_MISSING` is a **finding**, carried in the
#:   `--check-expected` payload once per miss. `adopt map --check-expected` exits
#:   `4` -- degraded success with findings, the same contract `doctor` and
#:   `coverage recompute` already use -- and **no category maps to `4`**, by
#:   design: exit `4` means the command *worked* and found something a human must
#:   see. Raising this code would therefore silently downgrade the miss to exit
#:   `1`, turning "your map is incomplete" into "the command failed", which is a
#:   different sentence and sends the reader somewhere else entirely.
_NEVER_RAISED: Final[frozenset[ErrorCode]] = frozenset(
    {ErrorCode.AGENT_BUDGET_EXHAUSTED, ErrorCode.MAP_EXPECTED_IDENTITY_MISSING}
)


def exit_code_for(code: ErrorCode) -> int:
    """The process exit code a given error code should terminate with."""
    return CATEGORY_EXIT_CODES[ERROR_CATEGORIES[code]]


class AdoptError(Exception):
    """The one error type that crosses a package boundary.

    Never raise a bare exception across a package boundary and never swallow
    one. A message is for a human; the ``code`` is what callers, tests and the
    CLI's exit status branch on.
    """

    def __init__(
        self,
        code: ErrorCode,
        category: ErrorCategory | None = None,
        message: str = "",
        hint: str | None = None,
        *,
        run_id: str | None = None,
    ) -> None:
        registered = ERROR_CATEGORIES[code]
        if category is not None and category is not registered:
            raise ValueError(
                f"{code} is registered as {registered}, not {category}. "
                "Change contracts §13 and ERROR_CATEGORIES together, or use the "
                "registered category."
            )
        if code in _NEVER_RAISED:
            raise ValueError(f"{code} is a returned status, never a raised error (contracts §13).")
        self.code: Final[ErrorCode] = code
        self.category: Final[ErrorCategory] = registered
        self.message: Final[str] = message
        self.hint: Final[str | None] = hint
        self.run_id: Final[str | None] = run_id
        super().__init__(message or str(code))

    @property
    def exit_code(self) -> int:
        """The process exit code this error should terminate with."""
        return exit_code_for(self.code)

    def to_envelope(self) -> dict[str, dict[str, str | None]]:
        """Render the one documented error envelope from contracts §13.

        Only the fields the contract declares. In particular the traceback and
        the exception chain are deliberately absent: an envelope is emitted to
        a client-facing surface, and a traceback carries file paths and, via
        local variables in some renderings, content.
        """
        return {
            "error": {
                "code": str(self.code),
                "category": str(self.category),
                "message": self.message,
                "hint": self.hint,
                "run_id": self.run_id,
            }
        }
