"""`adopt map`'s exit codes -- contracts §8, §10 C14; PRD N17; B1-CR-35 / OD-3.

**One contract test per code, and the table itself is the subject.** `02` §8's
exit codes are a published surface: an integrator scripts `if [ $? -eq 4 ]`
against them, and a code that changes meaning breaks a script silently. So the
assertions are against the *documented meaning* of each code, not against
whichever value the implementation happens to hold.

The CLI wiring itself gets **zero dedicated tests** under `03` §7's budget --
it is T4 glue, swept by the six journeys in S1.8. What is tested here is the
table, which is T3: a contract with exactly one home.
"""

import pytest

from adopt_obs import ERROR_CATEGORIES, ErrorCode, MapExitCode, exit_code_for, map_exit_code_for

pytestmark = pytest.mark.unit

#: `02` §8, verbatim. Restated here because a test that derived the expectation
#: from the implementation would assert only that the implementation equals
#: itself -- the one thing a contract test must not do.
_CONTRACT = {
    ErrorCode.MAP_USAGE: 2,
    ErrorCode.MAP_SCOPE_UNRESOLVED: 4,
    ErrorCode.MAP_ENVIRONMENT_AMBIGUOUS: 4,
    ErrorCode.MAP_BOUNDARY_MISSING: 4,
    ErrorCode.MAP_TIER_DECLINED: 4,
    ErrorCode.MAP_NO_ARCHETYPE: 4,
    ErrorCode.MAP_EXPORT_BUNDLE_MISSING: 4,
    ErrorCode.MAP_STORE_LOCKED: 5,
    ErrorCode.MAP_STORE_INCOMPATIBLE: 5,
    ErrorCode.MAP_COVERAGE_CACHE_DRIFT: 5,
    ErrorCode.MAP_URI_CONSTRUCTION_BYPASS: 5,
    ErrorCode.MAP_BUDGET_EXHAUSTED: 3,
    ErrorCode.MAP_AGENT_BUDGET_EXHAUSTED: 6,
    ErrorCode.MAP_EXTRACTOR_FAILED: 0,
}


@pytest.mark.parametrize(("code", "expected"), sorted(_CONTRACT.items()))
def test_every_map_code_exits_with_the_code_contracts_section_8_gives_it(
    code: ErrorCode, expected: int
) -> None:
    """*Fails when* a `MAP_*` code's exit value drifts from `02` §8.

    *Matters because* exit codes are the one part of a CLI that scripts branch
    on, and `3`, `4` and `6` are **successful runs with less output** while `5`
    is a failure -- a caller that treats them alike either discards a usable
    partial map or acts on a store error as if it were data. *No other instrument
    catches it because* every value is a plausible small integer.
    """
    assert map_exit_code_for(code) == expected


def test_the_map_table_covers_every_map_code_and_nothing_else() -> None:
    """*Fails when* a code is registered without an exit value, or vice versa.

    *Matters because* `map_exit_code_for` raises `KeyError` on a miss -- which is
    correct, and would surface as a crash in front of an operator rather than as
    a wrong exit code. The table and the registry move together. *No other
    instrument catches it because* the new code would work perfectly until the
    first time it was raised.
    """
    registered = {code for code in ERROR_CATEGORIES if str(code).startswith("MAP_")}
    assert set(_CONTRACT) == registered


def test_the_map_table_disagrees_with_the_category_default_and_that_is_the_point() -> None:
    """*Fails when* someone "simplifies" `adopt map` back onto the category table.

    *Matters because* B1-CR-24 introduced categories on the Build 1 codes saying
    *"the category decides the exit code"*, and for `adopt map` that is false:
    six of the fourteen codes disagree (OD-3). Deleting the `MAP_*` table because
    "the category already handles it" would silently return `MAP_SCOPE_UNRESOLVED`
    to `3` and `MAP_STORE_INCOMPATIBLE` to `1`. *No other instrument catches it
    because* both tables are individually coherent -- this test is the record
    that they are **deliberately** different.
    """
    disagreeing = {code for code in _CONTRACT if map_exit_code_for(code) != exit_code_for(code)}
    assert disagreeing == {
        ErrorCode.MAP_SCOPE_UNRESOLVED,
        ErrorCode.MAP_ENVIRONMENT_AMBIGUOUS,
        ErrorCode.MAP_BOUNDARY_MISSING,
        ErrorCode.MAP_TIER_DECLINED,
        ErrorCode.MAP_NO_ARCHETYPE,
        ErrorCode.MAP_EXPORT_BUNDLE_MISSING,
        ErrorCode.MAP_STORE_LOCKED,
        ErrorCode.MAP_STORE_INCOMPATIBLE,
        ErrorCode.MAP_COVERAGE_CACHE_DRIFT,
        ErrorCode.MAP_URI_CONSTRUCTION_BYPASS,
        ErrorCode.MAP_AGENT_BUDGET_EXHAUSTED,
        ErrorCode.MAP_EXTRACTOR_FAILED,
    }


def test_a_build_0_code_is_refused_by_the_map_table() -> None:
    """*Fails when* the table starts answering for codes it does not own.

    *Matters because* a plausible default -- returning `1`, say -- would hide a
    caller that reached for the wrong exit table entirely. Loud is correct here.
    """
    with pytest.raises(KeyError):
        map_exit_code_for(ErrorCode.URI_MALFORMED)


def test_the_three_partial_success_codes_are_distinct_from_failure() -> None:
    """`0`, `3` and `6` are usable runs; `2`, `4` and `5` are not (`02` §8)."""
    usable = {
        MapExitCode.COMPLETE,
        MapExitCode.PARTIAL_BUDGET_EXHAUSTED,
        MapExitCode.AGENT_BUDGET_EXHAUSTED,
    }
    refusing = {MapExitCode.USAGE, MapExitCode.DECLINED, MapExitCode.STORE_ERROR}
    assert usable.isdisjoint(refusing)
    assert usable == {0, 3, 6}
    assert refusing == {2, 4, 5}
