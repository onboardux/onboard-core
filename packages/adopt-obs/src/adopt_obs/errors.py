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
    # const-sync: ok -- a contracts §13 exit code, fixed by contract, not a tunable.
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
    HARVEST_NOT_A_GIT_REPO = "HARVEST_NOT_A_GIT_REPO"
    HARVEST_RANGE_UNKNOWN = "HARVEST_RANGE_UNKNOWN"

    ASK_OUTSIDE_BOUNDARY = "ASK_OUTSIDE_BOUNDARY"
    ESCALATION_NOT_FOUND = "ESCALATION_NOT_FOUND"
    ESCALATION_ALREADY_ANSWERED = "ESCALATION_ALREADY_ANSWERED"

    GAP_NOT_FOUND = "GAP_NOT_FOUND"
    GAP_WAIVER_NEEDS_UNTIL = "GAP_WAIVER_NEEDS_UNTIL"
    PACK_RENDERER_MISSING = "PACK_RENDERER_MISSING"

    PROBE_HOST_UNDECLARED = "PROBE_HOST_UNDECLARED"
    PROBE_BUDGET_EXCEEDED = "PROBE_BUDGET_EXCEEDED"
    PROBE_BASELINE_MISSING = "PROBE_BASELINE_MISSING"

    PLANE_AUTH_INVALID = "PLANE_AUTH_INVALID"
    PLANE_ACTIVATION_UNOWNED = "PLANE_ACTIVATION_UNOWNED"
    PLANE_REMOTE_NOT_CONFIGURED = "PLANE_REMOTE_NOT_CONFIGURED"
    PULL_TARGET_NOT_REPLICA = "PULL_TARGET_NOT_REPLICA"

    PLANE_CONNECTOR_REVOKED = "PLANE_CONNECTOR_REVOKED"
    PLANE_SENSE_PAYLOAD_INVALID = "PLANE_SENSE_PAYLOAD_INVALID"
    REFRESH_TARGET_IS_REPLICA = "REFRESH_TARGET_IS_REPLICA"

    HANDOVER_ALREADY_OPEN = "HANDOVER_ALREADY_OPEN"
    HANDOVER_NOT_OPEN = "HANDOVER_NOT_OPEN"
    HANDOVER_STEP_OUT_OF_ORDER = "HANDOVER_STEP_OUT_OF_ORDER"
    HANDOVER_CHECKLIST_INVALID = "HANDOVER_CHECKLIST_INVALID"
    HANDOVER_UNOWNED = "HANDOVER_UNOWNED"
    HANDOVER_TARGET_IS_REPLICA = "HANDOVER_TARGET_IS_REPLICA"


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
    # Harvest mines what is locally present (v6.1 §6 F7), so the absence of a
    # repository -- or of the `git` binary that reads one -- is the operator
    # pointing the verb at something it cannot mine. Usage, so it exits 2.
    ErrorCode.HARVEST_NOT_A_GIT_REPO: ErrorCategory.USAGE,
    # A **second** code rather than a reuse of the one above, on CR-38's
    # precedent and S2.1's: "there is no history here" and "the history is here
    # but the point you named is not in it" send an operator to two different
    # places -- install git or run in a checkout, versus `git tag -l`. The first
    # code covering both would say only "harvest cannot read this".
    ErrorCode.HARVEST_RANGE_UNKNOWN: ErrorCategory.USAGE,
    # Build 3. Policy rather than usage or integrity: the question was
    # well-formed and the store's data is intact -- the negotiated observability
    # boundary does not permit the content this answer would quote, and the
    # assistant refuses rather than trimming the answer to fit. Exits 3 with
    # every other policy refusal, which is what lets a caller distinguish "the
    # boundary said no" from UNKNOWN, an ordinary answer that exits 0.
    ErrorCode.ASK_OUTSIDE_BOUNDARY: ErrorCategory.POLICY,
    ErrorCode.ESCALATION_NOT_FOUND: ErrorCategory.USAGE,
    # Policy rather than usage, on `REVIEW_ITEM_RESOLVED`'s precedent and for
    # the same reason: the id was right and nothing is malformed -- the store
    # is refusing to answer a question that already has an answer. Answering
    # twice would leave the escalation pointing at one of two revisions with
    # no record of which the asker was actually given, and the second capture
    # would land as knowledge nothing links back to.
    ErrorCode.ESCALATION_ALREADY_ANSWERED: ErrorCategory.POLICY,
    # Usage: the caller named a gap the current recompute does not derive. A
    # disposition is only meaningful against a gap that exists, and accepting
    # one for a `gap_key` nothing produces would let the table accumulate rows
    # about identities that were never uncovered -- invisible, because the
    # report joins onto derived existence and would simply never show them.
    ErrorCode.GAP_NOT_FOUND: ErrorCategory.USAGE,
    # Usage: a waiver without an expiry is the one disposition that would
    # silently outlive the decision behind it. v6.1 §6 Build 4 makes
    # `waived_until` mandatory on a waiver for exactly that reason, and the
    # rule is a statement about one status value rather than a column
    # constraint either dialect can express -- so it is refused here.
    ErrorCode.GAP_WAIVER_NEEDS_UNTIL: ErrorCategory.USAGE,
    # Usage: `--format docx` or `--format pdf` was asked for and the pinned
    # converter is not on this machine. Usage rather than transient because
    # retrying changes nothing and the operator can fix it in one command -- and
    # **refusing is the only honest answer**: the canonical Markdown was written
    # either way, so a derived format that silently did not appear would leave
    # somebody looking for a file nobody said was missing.
    ErrorCode.PACK_RENDERER_MISSING: ErrorCategory.USAGE,
    # Policy, both of them, and for the same reason `ENVELOPE_*` are: the request
    # was well-formed and the store is intact -- a declaration the operator wrote
    # is what refused it. `PROBE_HOST_UNDECLARED` is the allow-list saying a
    # target was never declared; `PROBE_BUDGET_EXCEEDED` is the manifest's own
    # runtime or cost limit being spent. Exiting `3` with the other policy
    # refusals keeps "the probe was stopped by its own declaration"
    # distinguishable from "the probe ran and the system disagreed", which is an
    # ordinary outcome exiting `0`.
    ErrorCode.PROBE_HOST_UNDECLARED: ErrorCategory.POLICY,
    ErrorCode.PROBE_BUDGET_EXCEEDED: ErrorCategory.POLICY,
    # Usage, not policy and not integrity: nothing refused anything and
    # nothing is broken -- the operator asked for a comparison before there
    # was anything to compare against, and one command fixes it. Exiting `2`
    # keeps it distinguishable from exit `4`, which is what `adopt probe diff`
    # returns when it *did* compare and found drift: 'I could not answer' and
    # 'the answer is that it changed' send a reader to different places.
    ErrorCode.PROBE_BASELINE_MISSING: ErrorCategory.USAGE,
    # Build 7's two, and both are policy for the same reason the `ENVELOPE_*`
    # codes are: the request was well-formed and nothing is broken -- a rule the
    # operator declared is what refused it.
    #
    # `PLANE_AUTH_INVALID` is deliberately **one** code for every way a bearer
    # token can fail to resolve: absent, malformed, unknown, revoked, expired.
    # Distinguishing them in the response would let an unauthenticated caller
    # enumerate which tokens exist, and the plane's own logs carry the reason
    # for the one reader entitled to it.
    ErrorCode.PLANE_AUTH_INVALID: ErrorCategory.POLICY,
    # `PLANE_ACTIVATION_UNOWNED` is v6.1 §6's "an unowned live system is a
    # refused activation, not a warning" in one code. Policy rather than usage
    # although the fix is to supply an owner: the refusal exists because an
    # operated system with nobody to route an escalation to is a service that
    # cannot do its job, which is a decision about what we will operate rather
    # than a malformed request.
    ErrorCode.PLANE_ACTIVATION_UNOWNED: ErrorCategory.POLICY,
    # `PLANE_REMOTE_NOT_CONFIGURED` is **usage**, unlike the two above it, and
    # the difference is the fix: the operator asked a local verb to reach a
    # control plane and never said which one. Nothing refused anything and
    # nothing is broken -- three configuration keys are absent, which the hint
    # names. Exiting `2` rather than `3` keeps it distinguishable from a plane
    # that answered and said no.
    ErrorCode.PLANE_REMOTE_NOT_CONFIGURED: ErrorCategory.USAGE,
    # `PULL_TARGET_NOT_REPLICA` is **policy**, back with the first two, and the
    # test is the same one: nothing is malformed and nothing is broken. `adopt
    # pull` replaces a store file wholesale, and a store that is not already a
    # replica may hold canon nobody has exported -- so the refusal is a rule
    # about what this command is allowed to destroy, not a complaint about the
    # request. `--init-replica` is the operator saying they know, which is why
    # the fix is a flag rather than configuration and why this is not usage.
    ErrorCode.PULL_TARGET_NOT_REPLICA: ErrorCategory.POLICY,
    # `PLANE_CONNECTOR_REVOKED` is **policy** for `PULL_TARGET_NOT_REPLICA`'s
    # reason: the payload is well-formed and the token authenticated, and the
    # plane is refusing on a rule an operator set. A revoked relay that kept
    # posting would otherwise read as a transport fault to whoever is watching
    # the CI log, and the fix -- ask the operator why the relay was revoked --
    # is nothing like the fix for a malformed body.
    ErrorCode.PLANE_CONNECTOR_REVOKED: ErrorCategory.POLICY,
    # `PLANE_SENSE_PAYLOAD_INVALID` is **usage**: the caller sent something this
    # endpoint cannot read -- an unknown payload version, or a body missing a
    # field the cascade needs. Distinct from the code above precisely because
    # the two have opposite fixes, and a CI step that cannot tell them apart
    # retries the one that will never succeed.
    ErrorCode.PLANE_SENSE_PAYLOAD_INVALID: ErrorCategory.USAGE,
    # `REFRESH_TARGET_IS_REPLICA` is **policy**, and it is `PULL_TARGET_NOT_
    # REPLICA`'s mirror: that one refuses to overwrite canon with a replica,
    # this one refuses to write canon *into* a replica. Both are rules about
    # what a command may destroy rather than complaints about the request --
    # a refresh against a replica would write change events and staled bindings
    # that the next `adopt pull` silently discards, so the work is not merely
    # misplaced, it is lost without a trace. R9: the plane is the sole writer,
    # and `adopt ci-sense` is how an operated system gets sensed.
    ErrorCode.REFRESH_TARGET_IS_REPLICA: ErrorCategory.POLICY,
    # The four **usage** handover codes below are all "you asked for the wrong
    # thing next", and none of them is a rule about what may be destroyed --
    # which is the test that separates them from the two policy codes after.
    #
    # `HANDOVER_ALREADY_OPEN`: this system already has an un-closed handover.
    # Usage rather than policy because the operator almost certainly meant to
    # continue the one that is open, and `adopt handover status` shows it.
    ErrorCode.HANDOVER_ALREADY_OPEN: ErrorCategory.USAGE,
    # `HANDOVER_NOT_OPEN`: a step verb ran with no open handover for the
    # resolved system -- including the case where `--scope` named a different
    # system than the one that was frozen. `adopt handover start` is the fix.
    ErrorCode.HANDOVER_NOT_OPEN: ErrorCategory.USAGE,
    # `HANDOVER_STEP_OUT_OF_ORDER`: the step's prerequisite has not been
    # recorded. The message names the verb to run first, because a checklist
    # that refuses without saying what comes next is a checklist people work
    # around.
    ErrorCode.HANDOVER_STEP_OUT_OF_ORDER: ErrorCategory.USAGE,
    # `HANDOVER_CHECKLIST_INVALID`: the verification file is unreadable, is not
    # YAML, or fails validation. Usage for `TIER_ANSWERS_INVALID`'s reason --
    # the operator supplied a file and the file is wrong, which editing fixes.
    ErrorCode.HANDOVER_CHECKLIST_INVALID: ErrorCategory.USAGE,
    # `HANDOVER_UNOWNED` is **policy**, and it is `PLANE_ACTIVATION_UNOWNED`'s
    # local mirror: v6.1 §6 Build 9 makes "the event cannot close with the
    # system unowned" an honesty rule, so the refusal is a decision about what
    # we will record rather than a complaint about the request. Supplying an
    # owner fixes it and it is still not usage, for the reason the plane's
    # code is not: a handover that closed leaving nobody responsible is a
    # handover that transferred nothing, and the record would say otherwise.
    ErrorCode.HANDOVER_UNOWNED: ErrorCategory.POLICY,
    # `HANDOVER_TARGET_IS_REPLICA` is **policy**, beside `REFRESH_TARGET_IS_
    # REPLICA` and for the same rule: R9 makes the plane the sole writer after
    # activation, and every writing handover verb lands canon -- ownership
    # assignments, escalations, gap dispositions, audit rows -- into a file the
    # next `adopt pull` replaces wholesale. Its own code rather than a reuse of
    # refresh's, on the `PULL_`/`REFRESH_` precedent: the recovery differs, and
    # the hint has to be able to name it.
    ErrorCode.HANDOVER_TARGET_IS_REPLICA: ErrorCategory.POLICY,
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
