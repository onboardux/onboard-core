"""Each CI gate rejects what it exists to reject.

A gate is only worth having if it is known to fail. These tests plant the
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
from scripts import (
    ci_ratchet,
    constants_sync,
    error_registry_sync,
    licence_gate,
    no_destructive_sql,
    plant_violation,
)
from tools.contracts import source_rules

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


@pytest.mark.unit
class TestNoRevisionUpdate:
    """The append-only gate rejects the mutation it exists for.

    PRD F6's acceptance signal is *"the grep gate rejects a planted `UPDATE
    knowledge_revision`"*. The CI job proves it end to end by planting into the
    real tree; this proves the contract itself, without touching the working
    tree, so a developer finds out in `pytest` rather than in a red pipeline.
    """

    def test_an_update_against_a_revision_table_is_reported(self, tmp_path: Path) -> None:
        source = tmp_path / "pkg" / "writer.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            'SQL = "UPDATE knowledge_revision SET body_md = ? WHERE id = ?"\n',
            encoding="utf-8",
        )

        findings = source_rules.scan_paths_for_tests(
            source_rules.NoRevisionUpdateContract, paths=[str(tmp_path)], allowed_paths=[]
        )

        assert len(findings) == 1
        assert "knowledge_revision" in findings[0]

    @pytest.mark.parametrize(
        "table",
        [
            "identity_revision",
            "knowledge_revision",
            "binding_revision",
            "probe_definition_revision",
        ],
    )
    def test_every_family_is_covered_by_the_suffix_rule(self, tmp_path: Path, table: str) -> None:
        """The rule is the `_revision` suffix, not a list -- so a family added
        later is covered on the day it is added, by nobody."""
        source = tmp_path / "pkg" / f"{table}.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        # S608: this writes a *fixture file* for the gate to read. Nothing here
        # reaches a database -- the string being built is the violation the gate
        # must catch, which is the one thing this test cannot avoid containing.
        source.write_text(f'SQL = "UPDATE {table} SET x = 1"\n', encoding="utf-8")  # noqa: S608

        findings = source_rules.scan_paths_for_tests(
            source_rules.NoRevisionUpdateContract, paths=[str(tmp_path)], allowed_paths=[]
        )

        assert findings, f"{table} was not caught"

    def test_an_update_against_a_parent_row_is_not_a_violation(self, tmp_path: Path) -> None:
        """Parents are mutable in their head pointer, denormalized status and
        `last_seen` (contracts §5 obligation 6). A gate that flagged those would
        be switched off within a week, and the revision rule with it."""
        source = tmp_path / "pkg" / "parents.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            'HEAD = "UPDATE knowledge_item SET current_revision_id = ? WHERE id = ?"\n'
            'SEEN = "UPDATE identity SET last_seen = ? WHERE id = ?"\n',
            encoding="utf-8",
        )

        findings = source_rules.scan_paths_for_tests(
            source_rules.NoRevisionUpdateContract, paths=[str(tmp_path)], allowed_paths=[]
        )

        assert findings == []

    def test_prose_describing_the_rule_is_not_a_violation(self, tmp_path: Path) -> None:
        """A gate that fires on its own documentation is a gate people work
        around instead of with (the CR-24 lesson, applied here)."""
        source = tmp_path / "pkg" / "documented.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            '"""Never write UPDATE knowledge_revision -- append instead."""\n',
            encoding="utf-8",
        )

        findings = source_rules.scan_paths_for_tests(
            source_rules.NoRevisionUpdateContract, paths=[str(tmp_path)], allowed_paths=[]
        )

        assert findings == []

    def test_the_planted_fixture_still_carries_a_statement_the_gate_catches(self) -> None:
        """The CI job's proof depends on this fixture; if it were emptied or
        reworded, the job would go green while proving nothing."""
        statement = plant_violation._planted_statement("revision_update.sql")

        assert statement.upper().startswith("UPDATE ")
        assert "_revision" in statement

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


@pytest.mark.unit
class TestNoDestructiveSql:
    def test_self_test_detects_the_planted_statements(self) -> None:
        """`--self-test` is what the sprint's Final Output Validation runs."""
        assert no_destructive_sql.main(["--self-test"]) == 0

    @pytest.mark.parametrize(
        ("statement", "rule", "why"),
        [
            ("DROP TABLE knowledge_item", "drop-object", "the plain case"),
            ("ALTER TABLE binding DROP COLUMN is_load_bearing", "alter-drop", "column removal"),
            ("DELETE FROM identity", "unpredicated-delete", "no predicate, every row"),
            ("TRUNCATE TABLE audit_event", "truncate", "empties without an audit trail"),
            ("drop table firm", "drop-object", "SQL keywords are case-insensitive"),
            (
                "DROP\n  TABLE\n  firm",
                "drop-object",
                "whitespace is collapsed, so a wrapped statement is caught too",
            ),
        ],
    )
    def test_destructive_statements_are_detected(
        self, tmp_path: Path, statement: str, rule: str, why: str
    ) -> None:
        (tmp_path / "planted.py").write_text(
            f'SQL = """{statement}"""\n', encoding="utf-8", newline="\n"
        )

        findings, _ = no_destructive_sql.scan([tmp_path])

        assert [finding.rule for finding in findings] == [rule], why

    @pytest.mark.parametrize(
        ("source", "why"),
        [
            ('"""A docstring saying DROP TABLE is forbidden."""\nX = 1\n', "prose is not code"),
            ('SQL = "DELETE FROM review_item WHERE id = ?"', "a predicated delete is legitimate"),
            ('SQL = "UPDATE system SET lifecycle_state = ?"', "an update is not destructive"),
            (
                'SQL = "DROP TABLE t"  # no-destructive-sql: ok -- planted fixture\n',
                "an inline waiver is honoured and reported",
            ),
        ],
    )
    def test_legitimate_source_is_not_flagged(self, tmp_path: Path, source: str, why: str) -> None:
        (tmp_path / "clean.py").write_text(source, encoding="utf-8", newline="\n")

        findings, _ = no_destructive_sql.scan([tmp_path])

        assert findings == [], why

    def test_the_store_package_is_clean(self) -> None:
        """The invariant itself, not just the instrument: implementation spec §4.7."""
        findings, _ = no_destructive_sql.scan([REPO_ROOT / "packages"])

        assert findings == [], [finding.render(REPO_ROOT) for finding in findings]


@pytest.mark.unit
class TestWorkflowsAreRunnable:
    """The gates above are only enforced if the workflow declaring them starts.

    *Fails when* a workflow file stops being valid YAML, or a required job
    disappears from it. *Matters because* GitHub does not run an unparseable
    workflow at all -- it records a `startup_failure`, produces **no job output**,
    and offers **no re-run button**, so a required gate silently stops running and
    the run looks like an infrastructure hiccup. *No other instrument catches it
    because* every check in this repository runs inside a workflow, and none of
    them run when the workflow itself will not start.

    Added after exactly that reached `main`: a shell edit injected a control
    character into a workflow, and it was noticed only because a human went
    looking for a button that was not there.
    """

    WORKFLOWS = REPO_ROOT / ".github" / "workflows"

    #: Gates the pack requires to exist (implementation spec §7). A gate deleted
    #: from CI is a gate that stops running, and nothing else would notice.
    REQUIRED_JOBS = frozenset(
        {
            "lint",
            "constants-sync",
            "error-registry-sync",
            "licence-gate",
            "no-destructive-sql",
            "unit",
            "property",
            "schema-check",
            "schema-lint",
            "schema-realize",
        }
    )

    def _files(self) -> list[Path]:
        return sorted(self.WORKFLOWS.glob("*.yml")) + sorted(self.WORKFLOWS.glob("*.yaml"))

    def test_there_is_at_least_one_workflow(self) -> None:
        """Without this, every assertion below passes over an empty list."""
        assert self._files(), f"no workflow files found under {self.WORKFLOWS}"

    def test_every_workflow_parses_and_declares_jobs(self) -> None:
        import yaml

        for path in self._files():
            text = path.read_text(encoding="utf-8")
            control = {c for c in text if ord(c) < 32 and c not in "\n\r\t"}
            assert not control, (
                f"{path.name} contains control characters {[hex(ord(c)) for c in control]}. "
                "GitHub will not start this workflow, and reports no job output at all."
            )
            document = yaml.safe_load(text)
            assert isinstance(document, dict), f"{path.name} did not parse to a mapping"
            assert document.get("jobs"), f"{path.name} declares no jobs"

    def test_every_required_gate_is_still_declared(self) -> None:
        import yaml

        declared: set[str] = set()
        for path in self._files():
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            declared.update(document.get("jobs", {}))

        missing = self.REQUIRED_JOBS - declared
        assert not missing, f"these gates are no longer declared in any workflow: {sorted(missing)}"
