"""Each CI gate rejects what it exists to reject.

A gate is only worth having if it is known to fail. These four tests plant the
violation each gate was built for and assert the gate catches it -- the gates
themselves are the instruments for the invariants, and this file is the
instrument for the gates.

Shared defect sentence: *fails when* a gate stops detecting its violation.
*Matters because* every one of these gates guards an invariant that has no other
enforcement -- a silently broken gate reads exactly like a clean build. *No
other instrument catches it because* a passing gate and an absent gate produce
identical CI output.
"""

import os
from datetime import date
from pathlib import Path

import pytest
from scripts import ci_ratchet, constants_sync, error_registry_sync, licence_gate

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SPEC_STUB = """
### 2.1 -- schema, format, identity

| Name | Value | Consumer |
|---|---|---|
| `ALPHA_LIMIT` | `4_096` | thing |
| `BETA_BASE_MS` / `_MAX_MS` | `250` / `30_000` | retry |

### 2.4 `plane_const`

| Name | Value | Consumer |
|---|---|---|
| `GAMMA_FLOOR` | `0.85` | classifier |
"""


@pytest.mark.unit
class TestConstantsSync:
    def test_shorthand_names_expand_against_the_previous_name_in_the_same_cell(self) -> None:
        """`FOO_BASE_MS / _MAX_MS` means `FOO_BASE_MS` and `FOO_MAX_MS`.

        Expanding against the previous *row* instead silently invents a
        constant name that exists in neither the document nor the module, and
        the gate then reports two violations that are both artefacts of its own
        parser.
        """
        declared = {d.name: d.value for d in constants_sync.parse_spec(SPEC_STUB, ("2.1",))}

        assert declared == {
            "ALPHA_LIMIT": 4096,
            "BETA_BASE_MS": 250,
            "BETA_MAX_MS": 30000,
        }

    @pytest.mark.parametrize(
        ("module", "expected_fragment", "why"),
        [
            ({"ALPHA_LIMIT": 4096, "BETA_BASE_MS": 250, "BETA_MAX_MS": 1}, "is 1", "value drift"),
            ({"ALPHA_LIMIT": 4096, "BETA_BASE_MS": 250}, "does not", "missing from module"),
            (
                {
                    "ALPHA_LIMIT": 4096,
                    "BETA_BASE_MS": 250,
                    "BETA_MAX_MS": 30000,
                    "STOWAWAY": 7,
                },
                "appears in no",
                "undocumented extra constant",
            ),
        ],
        ids=["drift", "missing", "undocumented"],
    )
    def test_module_and_table_must_agree(
        self, module: dict[str, object], expected_fragment: str, why: str
    ) -> None:
        declarations = constants_sync.parse_spec(SPEC_STUB, ("2.1",))

        violations = constants_sync.check_module_matches_spec(declarations, module, "test_const")  # type: ignore[arg-type]

        assert violations, why
        assert any(expected_fragment in v for v in violations), why

    def test_a_name_in_both_modules_is_rejected(self) -> None:
        core = constants_sync.parse_spec(SPEC_STUB, ("2.1",))
        clash = constants_sync.parse_spec(
            SPEC_STUB.replace("`GAMMA_FLOOR`", "`ALPHA_LIMIT`"), ("2.4",)
        )

        violations = constants_sync.check_uniqueness(core, clash)

        assert any("both adopt_const and plane_const" in v for v in violations)

    def test_an_inlined_tunable_is_rejected_and_a_waiver_is_reported(self, tmp_path: Path) -> None:
        source = tmp_path / "pkg" / "leaky.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            "TIMEOUT = 4096\nOTHER = 4096  # const-sync: ok -- unrelated buffer size\n",
            encoding="utf-8",
        )

        violations, waivers = constants_sync.check_inlined_literals({4096}, [tmp_path])

        assert len(violations) == 1, "the unwaived literal must be reported"
        assert len(waivers) == 1, "the waived literal must still be reported, not hidden"

    def test_structurally_unavoidable_values_are_not_flagged(self, tmp_path: Path) -> None:
        """0, 1, 2 and -1 appear in indexing and arity in every codebase.

        Flagging them produces noise that ends with the gate switched off,
        which is strictly worse than not having it.
        """
        source = tmp_path / "pkg" / "normal.py"
        source.parent.mkdir(parents=True)
        source.write_text("x = items[0]\ny = n + 1\nz = n // 2\n", encoding="utf-8")

        violations, _ = constants_sync.check_inlined_literals({0, 1, 2}, [tmp_path])

        assert violations == []


@pytest.mark.unit
class TestErrorRegistrySync:
    REGISTRY_STUB = """
## 13. Error envelope and code registry

| Code | Category | Raised when |
|---|---|---|
| `ALPHA_FAILED` | policy | something |
| `BETA_ONE` / `BETA_TWO` | usage / integrity | two codes, two categories |
| `GAMMA_A` / `GAMMA_B` | usage | two codes, one broadcast category |

## 14. Next section
"""

    def test_rows_declaring_several_codes_pair_and_broadcast_correctly(self) -> None:
        parsed = error_registry_sync.parse_registry(self.REGISTRY_STUB)

        assert parsed == {
            "ALPHA_FAILED": "policy",
            "BETA_ONE": "usage",
            "BETA_TWO": "integrity",
            "GAMMA_A": "usage",
            "GAMMA_B": "usage",
        }

    def test_parsing_stops_at_the_next_section(self) -> None:
        """A registry that bleeds into §14 would pick up the CLI table."""
        parsed = error_registry_sync.parse_registry(
            self.REGISTRY_STUB + "\n| `NOT_A_CODE` | usage | in section 14 |\n"
        )

        assert "NOT_A_CODE" not in parsed

    def test_the_live_registry_matches_the_live_contracts_document(self) -> None:
        """The real bidirectional check, when the pack is reachable.

        **This is a convenience, not the instrument.** The authoritative check is
        the `error-registry-sync` CI job, which checks the handoff pack out
        deliberately and runs the same comparison. This test exists only so a
        developer with the pack as a sibling directory gets the answer from
        `pytest` instead of from CI ten minutes later.

        It therefore **skips** rather than fails when the pack is absent. The
        `unit` job runs without it on purpose: a unit suite that needs a second
        private repository checked out is not a unit suite, and wiring that
        dependency into the most frequently run job means a token expiry
        presents itself as a unit-test failure.

        The path is resolved exactly as `main()` resolves it, so the two cannot
        drift into looking in different places.
        """
        contracts = Path(
            os.environ.get("ADOPT_CONTRACTS_PATH", error_registry_sync.DEFAULT_CONTRACTS_PATH)
        )
        if not contracts.exists():
            pytest.skip(
                f"handoff pack not reachable at {contracts}; the `error-registry-sync` "
                "CI job is the authoritative instrument for this comparison"
            )

        report = error_registry_sync.run(contracts)

        assert report.ok, report.violations


@pytest.mark.unit
class TestLicenceGate:
    def test_self_test_detects_all_three_planted_violations(self) -> None:
        """`--self-test` is what the sprint's Final Output Validation runs."""
        assert licence_gate.self_test() == 0

    @pytest.mark.parametrize(
        ("dependency", "usage_mode", "rejected", "why"),
        [
            (("planted-agpl", "1.0", "AGPL-3.0-only"), "in-binary", True, "copyleft, linked"),
            (("planted-agpl", "1.0", "AGPL-3.0-only"), "dev-only", False, "copyleft, never ships"),
            (("planted-mit", "1.0", "MIT"), "in-binary", False, "permissive, linked"),
            (("planted-weird", "1.0", "Proprietary"), "in-binary", True, "not on the allowlist"),
            (("codeql", "1.0", "MIT"), "dev-only", True, "denied by name, any usage mode"),
        ],
        ids=["agpl-linked", "agpl-dev-only", "mit-linked", "unknown-licence", "denied-by-name"],
    )
    def test_licence_rule_depends_on_usage_mode(
        self, dependency: tuple[str, str, str], usage_mode: str, rejected: bool, why: str
    ) -> None:
        dep = licence_gate.Dependency(*dependency)
        row = {
            dep.name: {
                "repository": "https://example.invalid/x",
                "version": dep.version,
                "licence_hash": "abc",
                "security_status": "clear",
                "usage_mode": usage_mode,
                "owner": "eng-lead",
                "reverification_date": "2099-01-01",
            }
        }

        report = licence_gate.check([dep], row, {}, strict_verify=False)

        assert bool(report.violations) is rejected, f"{why}: {report.violations}"

    @pytest.mark.parametrize("missing_field", licence_gate.REQUIRED_VERIFICATION_FIELDS)
    def test_a_row_missing_any_one_of_the_seven_fields_blocks(self, missing_field: str) -> None:
        complete = {
            "repository": "https://example.invalid/x",
            "version": "1.0",
            "licence_hash": "abc",
            "security_status": "clear",
            "usage_mode": "in-binary",
            "owner": "eng-lead",
            "reverification_date": "2099-01-01",
        }
        complete[missing_field] = ""

        report = licence_gate.check([], {"planted": complete}, {}, strict_verify=False)

        assert any(missing_field in v for v in report.violations)

    def test_release_strictness_blocks_a_lapsed_reverification_date(self) -> None:
        row = {
            "planted": {
                "repository": "https://example.invalid/x",
                "version": "1.0",
                "licence_hash": "abc",
                "security_status": "clear",
                "usage_mode": "in-binary",
                "owner": "eng-lead",
                "reverification_date": "2020-01-01",
            }
        }

        report = licence_gate.check([], row, {}, strict_verify=True, today=date(2026, 7, 30))

        assert any("due for re-verification" in v for v in report.violations)

    def test_the_live_tree_passes_the_normal_gate(self) -> None:
        verifications = licence_gate.parse_verifications(
            licence_gate.VERIFICATIONS_PATH.read_text(encoding="utf-8")
        )
        report = licence_gate.check(
            licence_gate.installed_dependencies(),
            verifications,
            licence_gate.load_subprocess_deps(licence_gate.SUBPROCESS_DEPS_PATH),
            strict_verify=False,
        )

        assert report.ok, report.violations


@pytest.mark.unit
class TestCiRatchet:
    @pytest.mark.parametrize(
        ("budget", "elapsed", "command_exit", "expected_exit", "why"),
        [
            ("unit", 10.0, 0, 0, "well inside budget"),
            ("unit", 119.9, 0, 0, "just inside budget"),
            ("unit", 120.1, 0, 1, "a green suite over budget still fails"),
            ("pr", 601.0, 0, 1, "the per-PR budget is enforced too"),
            ("unit", 5.0, 2, 2, "a failing command keeps its own exit code"),
            ("unit", 999.0, 2, 2, "command failure outranks the budget verdict"),
        ],
    )
    def test_budget_table(
        self, budget: str, elapsed: float, command_exit: int, expected_exit: int, why: str
    ) -> None:
        result = ci_ratchet.evaluate(budget, elapsed, command_exit)

        assert result.exit_code == expected_exit, why

    def test_an_unknown_budget_name_is_rejected_rather_than_defaulted(self) -> None:
        """Defaulting would silently apply the wrong budget to a whole suite."""
        with pytest.raises(KeyError):
            ci_ratchet.evaluate("nightly", 1.0, 0)
