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
    assert_release_complete,
    ci_ratchet,
    conformance_matrix,
    constants_sync,
    error_registry_sync,
    licence_gate,
    no_destructive_sql,
    plant_violation,
    release_context,
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
class TestNoCoveredCacheWrite:
    """The coverage-cache gate rejects the write it exists for.

    **Declared at S0 and passing vacuously until S4**, because nothing wrote the
    cache. A gate that has only ever been green on an empty tree is not evidence,
    so its first real code and its first watched failure land together.

    PRD F7.4: *"No facade exposes a setter for `covered_cache`. It is written
    only by the recompute path."*
    """

    def test_a_cache_write_outside_adopt_coverage_is_reported(self, tmp_path: Path) -> None:
        source = tmp_path / "pkg" / "records.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            'SQL = "UPDATE identity SET covered_cache = ? WHERE id = ?"\n',
            encoding="utf-8",
        )

        findings = source_rules.scan_paths_for_tests(
            source_rules.NoCoveredCacheWriteContract, paths=[str(tmp_path)], allowed_paths=[]
        )

        assert len(findings) == 1
        assert "covered_cache" in findings[0]

    def test_the_timestamp_column_is_covered_too(self, tmp_path: Path) -> None:
        """`covered_cache_at` is half the cache. Writing it alone would leave the
        store claiming a confirmation time for a value nobody confirmed."""
        source = tmp_path / "pkg" / "stamp.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            'SQL = "UPDATE identity SET covered_cache_at = ? WHERE id = ?"\n',
            encoding="utf-8",
        )

        findings = source_rules.scan_paths_for_tests(
            source_rules.NoCoveredCacheWriteContract, paths=[str(tmp_path)], allowed_paths=[]
        )

        assert findings

    def test_the_same_write_inside_adopt_coverage_is_allowed(self, tmp_path: Path) -> None:
        """The gate is about *where* the write lives, not whether one exists --
        `adopt_coverage` has to be able to do its job."""
        source = tmp_path / "packages" / "adopt-coverage" / "cache.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            'SQL = "UPDATE identity SET covered_cache = ? WHERE id = ?"\n',
            encoding="utf-8",
        )

        findings = source_rules.scan_paths_for_tests(
            source_rules.NoCoveredCacheWriteContract,
            paths=[str(tmp_path)],
            # `as_posix()`, because `is_under` compares posix strings. A native
            # Windows path here silently never matches, and the test would then
            # pass for the wrong reason on one platform and fail on the other.
            allowed_paths=[(tmp_path / "packages" / "adopt-coverage").as_posix()],
        )

        assert findings == []

    def test_reading_the_cache_is_not_a_violation(self, tmp_path: Path) -> None:
        """`store doctor` and the recompute both read it. A gate that flagged a
        `SELECT` would make the disagreement check unwriteable."""
        source = tmp_path / "pkg" / "reader.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            'SQL = "SELECT id, covered_cache, covered_cache_at FROM identity"\n',
            encoding="utf-8",
        )

        findings = source_rules.scan_paths_for_tests(
            source_rules.NoCoveredCacheWriteContract, paths=[str(tmp_path)], allowed_paths=[]
        )

        assert findings == []

    def test_prose_describing_the_rule_is_not_a_violation(self, tmp_path: Path) -> None:
        """The CR-24 lesson again: a gate that fires on its own documentation
        gets switched off, and takes the rule with it."""
        source = tmp_path / "pkg" / "documented.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            '"""Never UPDATE identity SET covered_cache -- recompute owns it."""\n',
            encoding="utf-8",
        )

        findings = source_rules.scan_paths_for_tests(
            source_rules.NoCoveredCacheWriteContract, paths=[str(tmp_path)], allowed_paths=[]
        )

        assert findings == []

    def test_the_planted_fixture_still_carries_a_statement_the_gate_catches(self) -> None:
        """The CI job's proof depends on this fixture; if it were emptied or
        reworded, the job would go green while proving nothing."""
        statement = plant_violation._planted_statement("covered_cache_write.sql")

        assert statement.upper().startswith("UPDATE ")
        assert "covered_cache" in statement


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
    #: (See `TestConformanceMatrixTargets` below for the `--adapters` parser.)
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
            # `append-only` landed at S4 and `golden-g0` at S5; both are named as
            # gates in implementation spec §7 and neither was on this list, so
            # deleting either would have gone unnoticed by the one instrument
            # that exists to notice exactly that.
            "append-only",
            "golden-g0",
            # `durability` landed at S8 and is named in implementation spec §7.
            # Same reason as the two above: a gate absent from this list is a
            # gate whose deletion nothing notices.
            "durability",
            # `pricing-freshness` landed at S7 and is named in implementation
            # spec §2.2 rather than in §7's job table. Listed anyway, and for a
            # sharper version of the same reason: it exits 0 by design, so its
            # deletion would change nothing visible on any run.
            "pricing-freshness",
            # `vuln-audit` landed at S9 prep. A dependency acquires a
            # vulnerability after we pin it, so an audit that runs once is a
            # statement about a day rather than about the tree -- and the
            # `clean-<date>` statuses in `licence-verifications.md` are only
            # true for as long as this keeps running.
            "vuln-audit",
            # `conformance-matrix` landed at S7 and is named in implementation
            # spec §7. It is **switched off until two vendor credentials land**
            # (CR-48), which is the honest state for an unfinished capability --
            # and exactly why it must be on this list: the cheapest way to make a
            # red gate green is to delete it, and Build DoD condition 4 is this
            # job.
            "conformance-matrix",
            # `perf` and `supply-chain` are implementation spec §7's last two
            # rows, and **neither was on this list until S9** -- so deleting
            # `bench.yml` outright, or dropping the release's SBOM and signature
            # steps, would have gone unnoticed by the one instrument that exists
            # to notice exactly that. `perf` was additionally declared under a
            # stale job id (`schema-bench`) long after it grew from one harness
            # to seven; §7 names it `perf` and it is now called that (CR-51).
            #
            # §7 calls the release row `release`; the workflow is `release.yml`
            # and the job that enforces it is `supply-chain`, because `release`
            # names the file and three jobs live in it. §7 records the mapping.
            "perf",
            "supply-chain",
            "publish",
            "github-release",
            # `coverage-floor` landed at S9 and is implementation spec §6's floor
            # alarm. It is on this list for the sharpest version of the reason:
            # it is the only gate here that is *supposed* to do nothing on a
            # healthy tree, so its deletion changes no output at all.
            "coverage-floor",
            # `metrics` landed at S9. PRD §6 calls D1-D6 "ratios over CI events
            # with no human input", and this job is where the events come from.
            "metrics",
            # `packaged-artifact` landed at S9 after the release dry run found
            # that **nothing this repository builds could create a store**
            # (CR-53). It is the only gate here whose subject is a built
            # artefact rather than the source tree, which is precisely the gap
            # it exists to close: every other job -- and all 749 tests -- runs
            # against an editable install, where the `parents[N]` walk to
            # `schema/` still lands in the checkout and the defect is invisible.
            "packaged-artifact",
            # `artifact-licence` landed after the CR-58 audit found that all
            # fifteen distributions were built with no `License-Expression`, no
            # `License-File` and neither LICENSE nor NOTICE inside them. It is on
            # this list because its subject is irreversible: a wheel published
            # without its licence cannot be recalled from PyPI, and the gate that
            # would have caught it is the cheapest thing to drop when a build
            # slows down.
            "artifact-licence",
            # `map-journey` landed at Build 1 S1.2 and is that build's
            # **Definition of Done**: v6.1 §6's demo, run line by line on two
            # real repositories at pinned commits. It is on this list for the
            # reason the whole build exists to answer -- eight sprints of the
            # withdrawn v4 line were internally consistent, fully gated and 1604
            # tests green, and still the wrong build, because nothing in the
            # gates ran the verb on real code. This job is the one that does,
            # and deleting it would restore exactly that blindness.
            "map-journey",
            # `knowledge-journey` landed at Build 2 S2.2 and is that build's
            # **Definition of Done**: ingest, harvest, the one review queue,
            # gaps and the G2 move check, run on the same two pinned
            # repositories. It is on this list for a reason `map-journey` cannot
            # cover -- Build 1's journey maps a tree and binds nothing, so it
            # passes unchanged whether or not a move orphans every binding in
            # the store. Only a journey that has both a move and bindings can
            # see invariant #3 fail.
            "knowledge-journey",
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

    def test_every_external_action_uses_an_immutable_commit(self) -> None:
        """Mutable action tags must not change release or CI code without a commit.

        *Fails when* any job- or step-level ``uses`` value names a tag or branch.
        *Matters because* an upstream retag could change privileged release code
        after this repository's review. *No other instrument catches it because*
        quoted YAML values defeat line-oriented scans unless the parsed value is
        checked, and GitHub accepts both quoted and unquoted action references.
        """
        import re

        import yaml

        references: list[tuple[Path, str]] = []
        for path in self._files():
            jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
            for job in jobs.values():
                if "uses" in job:
                    references.append((path, str(job["uses"])))
                references.extend(
                    (path, str(step["uses"])) for step in job.get("steps", []) if "uses" in step
                )

        external = [
            (path, reference)
            for path, reference in references
            if not reference.startswith(("./", "docker://"))
        ]
        assert external, "no external action references were found"
        for path, reference in external:
            _, separator, revision = reference.rpartition("@")
            assert separator and re.fullmatch(r"[0-9a-f]{40}", revision), (
                f"{path.name} uses mutable action reference {reference!r}; pin the exact commit"
            )

    def test_every_required_gate_is_still_declared(self) -> None:
        import yaml

        declared: set[str] = set()
        for path in self._files():
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            declared.update(document.get("jobs", {}))

        missing = self.REQUIRED_JOBS - declared
        assert not missing, f"these gates are no longer declared in any workflow: {sorted(missing)}"

    def test_golden_g0_has_no_soft_fail(self) -> None:
        """`golden-g0` must never acquire `continue-on-error`.

        *Fails when* someone makes the portability gate advisory. *Matters
        because* PRD D1 measures its pass rate at `1.0` over the trailing 30 CI
        days, and a soft-failing job satisfies that ratio while proving nothing
        -- it reports success whatever the round trip did. *No other instrument
        catches it because* every other check here asserts that a gate *runs*,
        and a soft-failed job does run. It just stops meaning anything.
        """
        import yaml

        for path in self._files():
            job = yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs", {}).get("golden-g0")
            if job is None:
                continue
            assert "continue-on-error" not in job, "golden-g0 has no soft-fail mode, ever"
            for step in job.get("steps", []):
                assert "continue-on-error" not in step, (
                    f"a golden-g0 step is soft-failing: {step.get('name', step)}"
                )
            return
        raise AssertionError("golden-g0 is not declared in any workflow")

    def test_secret_bearing_ci_jobs_never_run_fork_code(self) -> None:
        """Public fork PRs receive no secrets and must not turn CI red.

        *Fails when* a job that consumes a private-pack or provider credential
        loses its trusted-event guard. *Matters because* an unguarded job either
        fails every external contribution or tempts a switch to
        ``pull_request_target``, which would expose secrets to fork-controlled
        code. *No other instrument catches it because* repository-local CI has
        secrets and therefore cannot simulate a fork's event boundary.
        """
        import yaml

        text = (self.WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        assert "pull_request_target" not in text
        jobs = yaml.safe_load(text)["jobs"]

        for job_name in ("constants-sync", "error-registry-sync", "conformance-matrix"):
            condition = str(jobs[job_name].get("if", ""))
            assert "github.event.pull_request.head.repo.full_name == github.repository" in condition
            assert "dependabot[bot]" in condition
            assert "github.event_name == 'push'" in condition

        for job_name in ("constants-sync", "error-registry-sync"):
            condition = str(jobs[job_name]["if"])
            assert "vars.ADOPT_PACK_REPOSITORY != ''" in condition
            pack_checkout = next(
                step
                for step in jobs[job_name]["steps"]
                if "ADOPT_PACK_REPOSITORY" in str(step.get("with", {}).get("repository", ""))
            )
            assert pack_checkout["with"]["persist-credentials"] is False

        assert "vars.ADOPT_CONFORMANCE_ADAPTERS != ''" in str(jobs["conformance-matrix"]["if"])

    def test_binaries_are_packed_from_the_published_wheel(self) -> None:
        """The packer compiles the artefact we ship, not the source tree.

        *Fails when* the `binaries` job goes back to `uv sync --all-packages`
        and points the packer at `packages/adopt-cli/src/...`. *Matters because*
        that is an **editable** install: its `RECORD` lists a `.pth` shim and no
        package files, so Nuitka cannot map the distribution to a package and
        `--include-distribution-metadata` fails outright (CR-54) -- and, worse,
        `--include-package-data=adopt_schema` would find no `_assets/schema/`,
        because that directory exists only inside a built wheel. The binary
        would build clean and ship without the schema assets CR-53 exists to
        put there. *No other instrument catches it because* the smoke test would
        catch the symptom one CI round later, on a job that needs a C toolchain
        on three platforms and is the slowest feedback loop in the repository.
        """
        import yaml

        for path in self._files():
            job = yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs", {}).get("binaries")
            if job is None:
                continue
            steps = job.get("steps", [])
            runs = "\n".join(str(step.get("run", "")) for step in steps)
            uses = [str(step.get("uses", "")) for step in steps]
            install = next(
                step
                for step in steps
                if step.get("name") == "Install the published wheel into a clean environment"
            )

            assert any("download-artifact" in u for u in uses), (
                "the binaries job must take the wheels from the build job, "
                "not rebuild or install from the checkout"
            )
            assert "--requirement runtime-constraints.txt" in runs
            assert install["env"]["FIRST_PARTY_DISTRIBUTIONS"] == (
                "${{ needs.build.outputs.distributions }}"
            )
            assert "for distribution in $FIRST_PARTY_DISTRIBUTIONS" in runs
            assert '"${distribution}==${EXPECTED_VERSION}"' in runs
            assert all(
                flag in runs for flag in ("--no-index", "--find-links dist/", "--no-deps")
            ), "the packer's first-party environment must come only from the published wheels"
            assert "adopt-cli nuitka" not in runs, (
                "Nuitka is a build tool and must be installed separately from first-party wheels"
            )
            assert "packages/adopt-cli/src" not in runs, (
                "packing from the source tree is an editable install -- the defect CR-54 "
                "records. Resolve the entry point from the installed distribution instead."
            )
            return
        raise AssertionError("the binaries job is not declared in any workflow")

    def test_each_binary_is_measured_against_the_governed_size_ceiling(self) -> None:
        """Every platform build must fail before upload when its binary is too large.

        *Fails when* the matrix relies only on the later merged-bundle check,
        uses a rounded shell utility instead of exact bytes, or hard-codes the
        limit. *Matters because* one oversized platform artifact otherwise spends
        the supply-chain job signing an unreleasable bundle. *No other instrument
        produces per-platform evidence because* only this job still has the
        installed build environment and its matrix-specific step summary.
        """
        import yaml

        release = yaml.safe_load((self.WORKFLOWS / "release.yml").read_text(encoding="utf-8"))
        steps = release["jobs"]["binaries"]["steps"]
        names = [step.get("name") for step in steps]
        gate = next(
            step for step in steps if step.get("name") == "Enforce the governed binary size ceiling"
        )
        upload_index = next(
            index
            for index, step in enumerate(steps)
            if "upload-artifact" in str(step.get("uses", ""))
        )

        assert names.index("Pack") < steps.index(gate) < upload_index
        run = str(gate["run"])
        assert '"$PY" - "$BIN" "$GITHUB_STEP_SUMMARY"' in run
        assert "from adopt_const import BINARY_MAX_MB" in run
        assert "binary.stat().st_size" in run
        assert "limit_bytes = BINARY_MAX_MB * bytes_per_mib" in run
        assert "if size_bytes > limit_bytes" in run
        assert "Exact bytes" in run and "GITHUB_STEP_SUMMARY" in run
        assert "continue-on-error" not in gate

    def test_release_never_installs_the_cosign_version_affected_by_ghsa_whqx_f9j3_ch6m(
        self,
    ) -> None:
        """Both irreversible boundaries must select a patched Cosign release.

        *Fails when* the installer falls back to its v2.5.2 default or the two
        verification jobs drift apart. *Matters because* Cosign versions through
        2.6.1 are affected by GHSA-whqx-f9j3-ch6m. *No other instrument catches
        it because* the action itself is immutably pinned while the tool version
        it downloads is a separate input with its own default.
        """
        import yaml

        release = yaml.safe_load((self.WORKFLOWS / "release.yml").read_text(encoding="utf-8"))
        for job_name in ("supply-chain", "github-release"):
            installers = [
                step
                for step in release["jobs"][job_name]["steps"]
                if str(step.get("uses", "")).startswith("sigstore/cosign-installer@")
            ]
            assert len(installers) == 1
            assert installers[0].get("with", {}).get("cosign-release") == "v2.6.3"

    def test_release_provenance_has_one_fail_closed_private_dry_run_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The unsupported private dry run is the only provenance exception.

        *Fails when* release-mode routing lets any path except a private manual
        non-publishing run omit provenance, fails to carry a successful action
        bundle into ``dist/``, or treats that bundle as an unsigned payload.
        *Matters because* the first failure permanently blocks the supported dry
        run, while either latter failure blocks or weakens a real release. *No
        other instrument catches it because* no existing test joins the action
        condition, action output, and release checker's CLI contract.
        """
        import yaml

        release = yaml.safe_load((self.WORKFLOWS / "release.yml").read_text(encoding="utf-8"))
        supply_chain = release["jobs"]["supply-chain"]
        private_dry_run = supply_chain.get("env", {}).get("ALLOW_MISSING_PROVENANCE")
        assert private_dry_run == (
            "${{ github.event_name == 'workflow_dispatch' && "
            "github.event.repository.private == true && inputs.publish == false }}"
        )

        steps = supply_chain["steps"]
        provenance = next(step for step in steps if step.get("name") == "SLSA provenance")
        assert provenance.get("id") == "provenance"
        assert provenance.get("if") == "${{ env.ALLOW_MISSING_PROVENANCE != 'true' }}"
        assert str(provenance.get("uses", "")).startswith("actions/attest-build-provenance@")
        assert not str(provenance["uses"]).endswith("@v4"), (
            "release actions are pinned to immutable commits, not mutable major tags"
        )
        assert provenance.get("with", {}).get("subject-path") == "dist/*"
        assert "continue-on-error" not in provenance

        staged = next(
            step
            for step in steps
            if step.get("name") == "Add SLSA provenance to the release bundle"
        )
        assert staged.get("if") == "${{ steps.provenance.outcome == 'success' }}"
        assert "${{ steps.provenance.outputs.bundle-path }}" in staged["run"]
        assert "dist/provenance.intoto.jsonl" in staged["run"]

        verified = next(
            step
            for step in steps
            if step.get("name") == "Verify provenance covers every exact payload"
        )
        assert verified.get("if") == "${{ env.ALLOW_MISSING_PROVENANCE != 'true' }}"
        verification_run = str(verified["run"])
        assert "for artefact in dist/*" in verification_run
        assert 'gh attestation verify "$artefact"' in verification_run
        assert "--bundle dist/provenance.intoto.jsonl" in verification_run
        assert "--limit 100" in verification_run
        assert '--repo "$GITHUB_REPOSITORY"' in verification_run
        assert '--cert-identity "$CERTIFICATE_IDENTITY"' in verification_run
        assert '--cert-oidc-issuer "$CERTIFICATE_OIDC_ISSUER"' in verification_run
        assert "--predicate-type https://slsa.dev/provenance/v1" in verification_run
        assert "continue-on-error" not in verified

        complete = next(
            step
            for step in steps
            if step.get("name") == "Refuse to proceed unless the release is complete"
        )
        assert (
            "${{ env.ALLOW_MISSING_PROVENANCE == 'true' && '--allow-missing-provenance' || '' }}"
        ) in complete["run"]

        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "adopt_cli-0.3.0-py3-none-any.whl").write_bytes(b"wheel")
        (dist / "adopt_cli-0.3.0-py3-none-any.whl.sig").write_text("signature", encoding="utf-8")
        (dist / "adopt_cli-0.3.0-py3-none-any.whl.pem").write_text("certificate", encoding="utf-8")
        (dist / "sbom.cdx.json").write_text(
            '{"components": [{"name": "adopt-core"}]}', encoding="utf-8"
        )
        (dist / "sbom.cdx.json.sig").write_text("signature", encoding="utf-8")
        (dist / "sbom.cdx.json.pem").write_text("certificate", encoding="utf-8")

        missing = assert_release_complete.check(
            dist,
            expected_version="0.3.0",
            expected_python_distributions=15,
        )
        assert not missing.ok
        provenance_violation = "exactly one SLSA provenance attestation is required"
        assert any(provenance_violation in violation for violation in missing.violations)

        relaxed = assert_release_complete.check(
            dist,
            require_provenance=False,
            expected_version="0.3.0",
            expected_python_distributions=15,
        )
        assert not any(provenance_violation in violation for violation in relaxed.violations)

        (dist / "provenance.intoto.jsonl").write_text(
            '{"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"}', encoding="utf-8"
        )
        report = assert_release_complete.check(
            dist,
            expected_version="0.3.0",
            expected_python_distributions=15,
        )
        assert not any(provenance_violation in violation for violation in report.violations)

        captured: dict[str, object] = {}

        def capture_check(
            directory: Path,
            *,
            require_provenance: bool = True,
            expected_version: str | None = None,
            expected_python_distributions: int | None = None,
        ) -> assert_release_complete.Report:
            captured.update(
                directory=directory,
                require_provenance=require_provenance,
                expected_version=expected_version,
                expected_python_distributions=expected_python_distributions,
            )
            return assert_release_complete.Report()

        monkeypatch.setattr(assert_release_complete, "check", capture_check)
        assert (
            assert_release_complete.main(
                [
                    "--dir",
                    str(dist),
                    "--version",
                    "0.3.0",
                    "--python-distributions",
                    "15",
                    "--allow-missing-provenance",
                ]
            )
            == 0
        )
        assert captured == {
            "directory": dist,
            "require_provenance": False,
            "expected_version": "0.3.0",
            "expected_python_distributions": 15,
        }

    def test_release_publication_is_tag_bound_and_artifacts_are_separated(self) -> None:
        """A green supply-chain job must not make an unsafe publish possible.

        *Fails when* build facts are stamped after packaging, PyPI receives the
        evidence bundle, signature verification trusts wildcard identities, or
        publication is no longer bound to the exact version tag. *Matters
        because* each defect is discovered only after an irreversible upload.
        *No other instrument catches it because* source tests neither evaluate
        GitHub routing nor inspect the uploader's artifact boundary.
        """
        import re

        import yaml

        path = self.WORKFLOWS / "release.yml"
        text = path.read_text(encoding="utf-8")
        release = yaml.safe_load(text)
        jobs = release["jobs"]
        build_steps = jobs["build"]["steps"]
        build_names = [step.get("name") for step in build_steps]

        assert "FIRST_PARTY_DISTRIBUTIONS" not in release.get("env", {})
        assert jobs["build"]["outputs"]["distributions"] == (
            "${{ steps.context.outputs.distributions }}"
        )
        named_distributions = set(re.findall(r"\badopt-[a-z][a-z-]*\b", text))
        assert not release_context.CANONICAL_DISTRIBUTIONS.issubset(named_distributions), (
            "release.yml must consume release_context's distribution output, not repeat its list"
        )

        assert (
            build_names.index("CycloneDX SBOM")
            < build_names.index("Embed immutable build facts")
            < build_names.index("Build all 15 Python distributions")
        )

        pypi_upload = next(
            step for step in build_steps if step.get("with", {}).get("name") == "pypi-dist"
        )
        pypi_paths = str(pypi_upload["with"]["path"]).splitlines()
        assert pypi_paths == ["dist/*.whl", "dist/*.tar.gz"]

        installed_smoke = next(
            step
            for step in build_steps
            if step.get("name") == "The installed wheel is self-contained and stamped"
        )
        installed_run = str(installed_smoke["run"])
        assert installed_smoke["env"]["FIRST_PARTY_DISTRIBUTIONS"] == (
            "${{ steps.context.outputs.distributions }}"
        )
        assert installed_run.index("--requirement runtime-constraints.txt") < installed_run.index(
            "--no-index"
        )
        assert "for distribution in $FIRST_PARTY_DISTRIBUTIONS" in installed_run
        assert '"${distribution}==${EXPECTED_VERSION}"' in installed_run
        assert all(
            flag in installed_run for flag in ("--no-index", "--find-links dist/", "--no-deps")
        )
        assert "adopt-cli\n" not in installed_run

        publish = jobs["publish"]
        publish_condition = (
            "${{ github.event_name == 'workflow_dispatch' && inputs.publish == true && "
            "github.ref == format('refs/tags/{0}', needs.build.outputs.tag) }}"
        )
        assert publish["if"] == publish_condition
        publish_downloads = [
            step for step in publish["steps"] if "download-artifact" in step.get("uses", "")
        ]
        assert len(publish_downloads) == 1
        assert publish_downloads[0]["with"] == {"name": "pypi-dist", "path": "dist/"}
        assert "release-bundle" not in str(publish)

        supply_runs = "\n".join(str(step.get("run", "")) for step in jobs["supply-chain"]["steps"])
        assert '--certificate-identity "$CERTIFICATE_IDENTITY"' in supply_runs
        assert '--certificate-oidc-issuer "$CERTIFICATE_OIDC_ISSUER"' in supply_runs
        assert "identity-regexp" not in supply_runs and "issuer-regexp" not in supply_runs

        github_release = jobs["github-release"]
        assert github_release["if"] == publish_condition
        assert github_release["env"]["GH_REPO"] == "${{ github.repository }}"
        release_verification = next(
            step
            for step in github_release["steps"]
            if step.get("name") == "Verify downloaded release assets"
        )
        release_verification_run = str(release_verification["run"])
        assert 'gh attestation verify "$artefact"' in release_verification_run
        assert "--bundle dist/provenance.intoto.jsonl" in release_verification_run
        assert "--limit 100" in release_verification_run
        assert '--repo "$GITHUB_REPOSITORY"' in release_verification_run
        assert '--cert-identity "$CERTIFICATE_IDENTITY"' in release_verification_run
        assert '--cert-oidc-issuer "$CERTIFICATE_OIDC_ISSUER"' in release_verification_run
        release_run = "\n".join(str(step.get("run", "")) for step in github_release["steps"])
        assert "gh release create" in release_run and "--verify-tag" in release_run


@pytest.mark.unit
class TestReleaseContext:
    """Release routing fails before any publication job receives credentials."""

    def test_every_workspace_package_must_share_one_version(self, tmp_path: Path) -> None:
        packages = tmp_path / "packages"
        for name in sorted(release_context.CANONICAL_DISTRIBUTIONS):
            version = "0.3.1" if name == "adopt-cli" else "0.3.0"
            project = packages / name / "pyproject.toml"
            project.parent.mkdir(parents=True)
            project.write_text(
                f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8"
            )

        with pytest.raises(ValueError, match="not lockstep"):
            release_context.resolve_context(tmp_path)

    @pytest.mark.parametrize(
        ("event_name", "git_ref", "publish", "expected"),
        [
            (
                "push",
                "refs/tags/v0.3.1",
                False,
                ["tag v0.3.1 does not match workspace version 0.3.0; expected v0.3.0"],
            ),
            (
                "push",
                "refs/tags/v0.3.0",
                True,
                ["publish=true is permitted only on a manual workflow dispatch"],
            ),
            (
                "workflow_dispatch",
                "refs/heads/main",
                True,
                ["publish=true requires ref refs/tags/v0.3.0; received refs/heads/main"],
            ),
            (
                "workflow_dispatch",
                "refs/tags/v0.3.1",
                True,
                [
                    "tag v0.3.1 does not match workspace version 0.3.0; expected v0.3.0",
                    "publish=true requires ref refs/tags/v0.3.0; received refs/tags/v0.3.1",
                ],
            ),
        ],
    )
    def test_only_a_manual_dispatch_on_the_exact_tag_can_publish(
        self, event_name: str, git_ref: str, publish: bool, expected: list[str]
    ) -> None:
        context = release_context.Context(version="0.3.0", tag="v0.3.0", distribution_count=15)

        violations = release_context.validate_route(
            context, event_name=event_name, git_ref=git_ref, publish=publish
        )

        assert violations == expected

    @pytest.mark.parametrize(
        ("event_name", "git_ref", "publish"),
        [
            ("push", "refs/tags/v0.3.0", False),
            ("workflow_dispatch", "refs/heads/main", False),
            ("workflow_dispatch", "refs/tags/v0.3.0", False),
            ("workflow_dispatch", "refs/tags/v0.3.0", True),
        ],
    )
    def test_expected_diagnostic_and_publication_routes_are_allowed(
        self, event_name: str, git_ref: str, publish: bool
    ) -> None:
        """The fail-closed route check must not make the intended release impossible."""
        context = release_context.Context(version="0.3.0", tag="v0.3.0", distribution_count=15)

        assert (
            release_context.validate_route(
                context, event_name=event_name, git_ref=git_ref, publish=publish
            )
            == []
        )


@pytest.mark.unit
class TestConformanceMatrixTargets:
    """`--adapters` resolves a model per adapter.

    *Fails when* the `id=model` parser breaks. *Matters because* no adapter
    carries a default model (AI spec §2) and `ADOPT_MODEL` is one process-wide
    value, so a mis-parse either sends one vendor's model id to the other vendor
    or drops it entirely -- and both surface in CI as an adapter that refused to
    construct, which reads exactly like a bad credential. *No other instrument
    catches it because* the matrix only executes where credentials exist, and
    there a wiring defect and a vendor outage are indistinguishable.
    """

    def test_a_bare_id_inherits_the_process_model(self) -> None:
        """The pre-CR-48 form still means what it meant.

        `fake_recorded` is typed `test` and needs no model at all; requiring one
        would break the default invocation this repository runs on every PR.
        """
        assert conformance_matrix.parse_targets("fake_recorded") == [
            conformance_matrix.Target(adapter="fake_recorded", model=None)
        ]

    def test_each_adapter_carries_its_own_model(self) -> None:
        """The defect CR-51 closed: two hosted vendors, two model ids."""
        assert conformance_matrix.parse_targets("openai=gpt-5,anthropic=claude-sonnet-4-5") == [
            conformance_matrix.Target(adapter="openai", model="gpt-5"),
            conformance_matrix.Target(adapter="anthropic", model="claude-sonnet-4-5"),
        ]

    def test_the_two_forms_mix_and_whitespace_is_not_significant(self) -> None:
        assert conformance_matrix.parse_targets(" openai=gpt-5 , fake_recorded ") == [
            conformance_matrix.Target(adapter="openai", model="gpt-5"),
            conformance_matrix.Target(adapter="fake_recorded", model=None),
        ]

    @pytest.mark.parametrize(
        ("spec", "fragment", "why"),
        [
            ("openai=", "names no model", "an empty model reads as an intention to name one"),
            ("nope=gpt-5", "not a registered adapter", "a typo must cost a message, not a run"),
            ("nope", "not a registered adapter", "the bare form validates too"),
        ],
    )
    def test_a_malformed_target_is_refused_before_any_provider_is_called(
        self, spec: str, fragment: str, why: str
    ) -> None:
        with pytest.raises(SystemExit) as raised:
            conformance_matrix.parse_targets(spec)
        assert fragment in str(raised.value), why

    def test_the_model_does_not_leak_from_one_invocation_to_the_next(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """*Fails when* `_run_one` mutates the parent environment.

        That is the original defect's shape one level down: a per-adapter model
        written into `os.environ` would be inherited by every later adapter, so
        the second vendor would silently run against the first vendor's model
        and the matrix would report a vendor failure for a wiring bug.
        """
        seen: list[dict[str, str]] = []

        class _Completed:
            returncode = 0

        def _fake_run(argv: list[str], **kwargs: object) -> _Completed:
            env = kwargs.get("env")
            assert isinstance(env, dict)
            seen.append(env)
            return _Completed()

        monkeypatch.setattr(conformance_matrix.subprocess, "run", _fake_run)
        monkeypatch.delenv("ADOPT_MODEL", raising=False)

        for target in conformance_matrix.parse_targets("openai=gpt-5,fake_recorded"):
            conformance_matrix.run_one(target)

        assert seen[0]["ADOPT_MODEL"] == "gpt-5"
        assert "ADOPT_MODEL" not in seen[1], "the first adapter's model reached the second"
        assert "ADOPT_MODEL" not in os.environ, "the parent environment was mutated"
