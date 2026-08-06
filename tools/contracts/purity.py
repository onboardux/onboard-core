"""`workflow-body-purity`: a `@workflow` body is deterministic.

**The rules are not here.** They live in `adopt_workflow.purity`, which is also
what `@workflow` calls at decoration time. Implementation spec §4.14 requires
purity checked at import *and* by this contract, and two enforcement points
reading two copies of a banned-call list is one copy drifting -- silently, in the
permissive direction, the first time someone adds an alias to one of them. This
module is the import-linter adapter and nothing else.

Both halves are load-bearing and neither subsumes the other: the import-time
check catches a body whose module the lint paths do not cover, and this contract
catches a body in a file the suite never imports.
"""

from grimp import ImportGraph
from importlinter import Contract, ContractCheck, fields, output

from adopt_workflow.purity import find_impure_workflow_bodies
from tools.contracts._scan import Finding, as_str_list, iter_source_files

__all__ = ["WorkflowBodyPurityContract", "find_impure_workflow_bodies"]


class WorkflowBodyPurityContract(Contract):
    """No clock, randomness, network, model call or I/O in a `@workflow` body."""

    paths = fields.ListField(subfield=fields.StringField())

    def check(self, graph: ImportGraph, verbose: bool) -> ContractCheck:
        findings: list[Finding] = []
        for path in iter_source_files(as_str_list(self.paths)):
            if path.suffix != ".py":
                continue
            source = path.read_text(encoding="utf-8")
            if "workflow" not in source:
                continue
            try:
                impurities = find_impure_workflow_bodies(source, str(path))
            except SyntaxError:  # pragma: no cover -- ruff catches these first
                continue
            findings += [Finding(path, line, reason) for line, reason in impurities]
        output.verbose_print(verbose, f"checked workflow bodies; {len(findings)} finding(s)")
        return ContractCheck(
            kept=not findings, metadata={"findings": [f.render() for f in findings]}
        )

    def render_broken_contract(self, check: ContractCheck) -> None:
        output.print_error("WORKFLOW_BODY_IMPURE: a @workflow body is not deterministic.")
        output.new_line()
        for rendered in check.metadata.get("findings", []):
            output.print_error(f"  {rendered}", bold=False)
        output.new_line()
        output.print_error(
            "Move the non-deterministic work into a @step. A workflow body is replayed "
            "on resume; anything that reads a clock, draws randomness or performs I/O "
            "will take a different branch the second time, and the divergence shows up "
            "in production rather than in tests.",
            bold=False,
        )
