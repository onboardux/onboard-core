"""The in-process backend: no external dependency, for CI and local dev.

Implementation spec §4.14 requires it and PRD F14.6 says why -- **no Build 0
OSS-side command uses durable workflows, so the OSS CLI never requires
Postgres.** A backend that needed a database to run the suite would make that
sentence false in the one place it is checked.

**Steps persist before and after execution**, so a crash between them replays the
step rather than the workflow. The step's identity is `(step name, ordinal within
the run)` -- and the ordinal is only stable because the body is pure. That is the
mechanical reason `workflow-body-purity` exists: an `if random.random() > 0.5`
in a body changes which step is third, and a resumed run then replays one step's
result into another step's slot. The purity check is not hygiene; it is what
makes replay addressable.
"""

import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from adopt_obs import AdoptError, ErrorCategory, ErrorCode, get_logger, new_id
from adopt_workflow.api import (
    TERMINAL_STATUSES,
    RetryPolicy,
    WorkflowHandle,
    WorkflowStatus,
    backoff_delays_ms,
    validate_idempotency_key,
)
from adopt_workflow.decorators import WORKFLOW_ATTR, StepDefinition, WorkflowDefinition
from adopt_workflow.inproc.journal import Journal

__all__ = ["InProcessStepContext", "InProcessWorkflowClient"]

_LOG = get_logger(__name__)

_MS_PER_SECOND: Final[int] = 1000


class _Cancelled(Exception):
    """Raised inside a body when `cancel` was recorded for its run."""


class InProcessStepContext:
    """Contracts §10.2's `StepContext`, backed by the journal."""

    def __init__(self, *, run_id: str, attempt: int, journal: Journal) -> None:
        self.run_id = run_id
        self.attempt = attempt
        self._journal = journal

    def dedupe(self, key: str) -> bool:
        """`True` on the first commit for `key`, `False` on every replay.

        The `effect` record **is** the commit -- see `journal.py` for why the
        record and the effect cannot be two writes.
        """
        for record in self._journal.records():
            if record.get("type") == "effect" and record.get("key") == key:
                return False
        self._journal.append({"type": "effect", "key": key, "run_id": self.run_id})
        return True


class _Context:
    """What a workflow body is handed. `step` is the only door out."""

    def __init__(self, *, run_id: str, client: "InProcessWorkflowClient") -> None:
        self.run_id = run_id
        self._client = client
        self._ordinals: dict[str, int] = {}

    def step(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        definition: StepDefinition | None = getattr(fn, "__adopt_step__", None)
        if definition is None:
            # The same defect `workflow-body-purity` exists for, caught at a
            # different moment: an unpersisted call is not replay-safe, and the
            # callee's identity is only knowable when it is called, so no AST
            # check can see this one.
            raise AdoptError(
                ErrorCode.WORKFLOW_BODY_IMPURE,
                message=f"{getattr(fn, '__name__', fn)!r} is not a @step",
                hint=(
                    "Only a @step is persisted and replayed. Calling a plain function "
                    "from a body runs it again on every resume, which makes the replay "
                    "diverge exactly as a clock reading would."
                ),
            )
        ordinal = self._ordinals.get(definition.name, 0)
        self._ordinals[definition.name] = ordinal + 1
        return self._client._run_step(
            run_id=self.run_id,
            definition=definition,
            ordinal=ordinal,
            args=args,
            kwargs=kwargs,
        )


class InProcessWorkflowClient:
    """Contracts §10.2's `WorkflowClient`, over a file journal.

    `sleeper` is injected because implementation spec §5 bans sleeps in tests:
    the retry schedule is asserted by recording the delays that *would* have been
    waited, which also makes the assertion exact rather than timing-dependent.
    """

    def __init__(
        self,
        journal_dir: Path,
        *,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._journal = Journal(Path(journal_dir) / "workflow.ndjson")
        self._sleep = sleeper if sleeper is not None else time.sleep

    # -- reading the journal ------------------------------------------------

    def _runs(self) -> dict[str, dict[str, Any]]:
        """Fold the journal into one entry per run. Later records win."""
        runs: dict[str, dict[str, Any]] = {}
        for record in self._journal.records():
            kind = record.get("type")
            run_id = str(record.get("run_id", ""))
            if kind == "run_started":
                runs[run_id] = {
                    "run_id": run_id,
                    "name": record["name"],
                    "version": record["version"],
                    "idempotency_key": record["idempotency_key"],
                    "status": "running",
                    "result": None,
                }
            elif kind == "run_finished" and run_id in runs:
                runs[run_id]["status"] = record["status"]
                runs[run_id]["result"] = record.get("result")
        return runs

    def _completed_steps(self, run_id: str) -> dict[tuple[str, int], Any]:
        done: dict[tuple[str, int], Any] = {}
        for record in self._journal.records():
            if record.get("type") == "step_finished" and record.get("run_id") == run_id:
                done[(str(record["step"]), int(record["ordinal"]))] = record.get("result")
        return done

    def _cancelled(self, run_id: str) -> bool:
        return any(
            record.get("type") == "cancel_requested" and record.get("run_id") == run_id
            for record in self._journal.records()
        )

    # -- the WorkflowClient surface ----------------------------------------

    def start(
        self,
        fn: Callable[..., Any],
        args: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> WorkflowHandle:
        validate_idempotency_key(idempotency_key)
        definition: WorkflowDefinition | None = getattr(fn, WORKFLOW_ATTR, None)
        if definition is None:
            raise AdoptError(
                ErrorCode.WORKFLOW_DUPLICATE_START,
                message=f"{getattr(fn, '__name__', fn)!r} is not a @workflow",
                hint="Decorate the body with @workflow(name=..., version=...) first.",
            )

        for run in self._runs().values():
            if run["idempotency_key"] != idempotency_key:
                continue
            if run["name"] != definition.name or run["version"] != definition.version:
                raise AdoptError(
                    ErrorCode.WORKFLOW_DUPLICATE_START,
                    message=f"idempotency key {idempotency_key!r} already started "
                    f"{run['name']}@{run['version']}, not "
                    f"{definition.name}@{definition.version}",
                    hint=(
                        "The same key naming two different workflows is a caller bug, "
                        "not a replay: returning the existing handle would silently "
                        "run something the caller did not ask for."
                    ),
                )
            # A genuine replay. Contracts §10.2: return the existing handle.
            return self._handle(run)

        run_id = new_id("run")
        self._journal.append(
            {
                "type": "run_started",
                "run_id": run_id,
                "name": definition.name,
                "version": definition.version,
                "idempotency_key": idempotency_key,
            }
        )
        self._drive(run_id, definition, dict(args))
        return self._handle(self._runs()[run_id])

    def signal(self, run_id: str, name: str, payload: Mapping[str, Any]) -> None:
        self._journal.append(
            {"type": "signal", "run_id": run_id, "name": name, "payload": dict(payload)}
        )

    def status(self, run_id: str) -> WorkflowStatus:
        run = self._runs().get(run_id)
        if run is None:
            raise AdoptError(
                ErrorCode.WORKFLOW_STEP_EXHAUSTED,
                message=f"no run {run_id!r} in this journal",
                hint="A run id is only meaningful against the journal that minted it.",
            )
        status: WorkflowStatus = run["status"]
        return status

    def result(self, run_id: str, *, timeout_s: int) -> Any:
        """The recorded result.

        Execution here is synchronous, so `timeout_s` never elapses -- it is
        accepted because §10.2 declares it and a backend may not narrow the seam.
        A run that is still `running` when asked is a resumable crash, not a slow
        run, and says so.
        """
        run = self._runs().get(run_id)
        if run is None or run["status"] not in TERMINAL_STATUSES:
            raise AdoptError(
                ErrorCode.WORKFLOW_STEP_EXHAUSTED,
                message=f"run {run_id!r} has no result: it is "
                f"{'unknown' if run is None else run['status']}",
                hint="Call recover() first; an unfinished run in this backend means a crash.",
            )
        return run["result"]

    def cancel(self, run_id: str) -> None:
        self._journal.append({"type": "cancel_requested", "run_id": run_id})
        if self.status(run_id) not in TERMINAL_STATUSES:
            self._finish(run_id, "cancelled", None)

    def close(self) -> None:
        """Nothing to stop, and that is a property of this backend *(CR-43)*.

        This client executes a body **inline on the caller's thread** and appends
        to the journal with one `write` + `fsync` per record, holding no pooled
        connection and no worker between calls. So there is no window in which
        closing could lose a record, and no thread to join.

        It is still declared and still called by the drill: a no-op that is
        exercised is what proves the *contract* is honoured by both backends
        rather than by the one that happened to need it.
        """

    # -- execution ----------------------------------------------------------

    @staticmethod
    def _handle(run: Mapping[str, Any]) -> WorkflowHandle:
        return WorkflowHandle(
            run_id=run["run_id"],
            name=run["name"],
            version=run["version"],
            idempotency_key=run["idempotency_key"],
            status=run["status"],
        )

    def recover(self) -> list[WorkflowHandle]:
        """Re-drive every non-terminal run. Returns what was resumed.

        DBOS does this at startup; here it is explicit, because the drill needs
        to say *when* the resume happened to assert anything about it.
        """
        resumed: list[WorkflowHandle] = []
        for run in list(self._runs().values()):
            if run["status"] in TERMINAL_STATUSES:
                continue
            definition = self._resolve(run)
            args = self._recorded_args(run["run_id"])
            self._drive(run["run_id"], definition, args)
            resumed.append(self._handle(self._runs()[run["run_id"]]))
        return resumed

    def _resolve(self, run: Mapping[str, Any]) -> WorkflowDefinition:
        from adopt_workflow.decorators import resolve

        definition: WorkflowDefinition = resolve("workflow", run["name"], int(run["version"]))
        return definition

    def _recorded_args(self, run_id: str) -> dict[str, Any]:
        for record in self._journal.records():
            if record.get("type") == "run_args" and record.get("run_id") == run_id:
                args: dict[str, Any] = dict(record.get("args", {}))
                return args
        return {}

    def _drive(self, run_id: str, definition: WorkflowDefinition, args: Mapping[str, Any]) -> None:
        if not any(
            record.get("type") == "run_args" and record.get("run_id") == run_id
            for record in self._journal.records()
        ):
            self._journal.append({"type": "run_args", "run_id": run_id, "args": dict(args)})
        context = _Context(run_id=run_id, client=self)
        try:
            result = definition.fn(context, dict(args))
        except _Cancelled:
            self._finish(run_id, "cancelled", None)
            return
        except AdoptError as error:
            # **A run fails for work that failed, not for work that was written
            # wrongly.** `internal` and `transient` are the step's own failure and
            # belong in the run record; `usage` and `policy` are statements about
            # the request or the body, and swallowing one into a `failed` run
            # would hide a programming error behind a status a retry looks at.
            if error.category in (ErrorCategory.USAGE, ErrorCategory.POLICY):
                raise
            # `run_id` is a reserved log key, bound rather than passed as a field.
            _LOG.bind_run(run_id).error("workflow_run_failed", code=error.code.value)
            self._finish(run_id, "failed", None)
            return
        self._finish(run_id, "completed", result)

    def _finish(self, run_id: str, status: WorkflowStatus, result: Any) -> None:
        self._journal.append(
            {"type": "run_finished", "run_id": run_id, "status": status, "result": result}
        )

    def _run_step(
        self,
        *,
        run_id: str,
        definition: StepDefinition,
        ordinal: int,
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any],
    ) -> Any:
        if self._cancelled(run_id):
            raise _Cancelled(run_id)

        recorded = self._completed_steps(run_id)
        key = (definition.name, ordinal)
        if key in recorded:
            return recorded[key]

        policy: RetryPolicy = definition.retries
        delays = backoff_delays_ms(policy)
        last: Exception | None = None
        for attempt in range(1, policy.max_attempts + 1):
            self._journal.append(
                {
                    "type": "step_started",
                    "run_id": run_id,
                    "step": definition.name,
                    "ordinal": ordinal,
                    "attempt": attempt,
                }
            )
            context = InProcessStepContext(run_id=run_id, attempt=attempt, journal=self._journal)
            try:
                result = definition.fn(context, *args, **dict(kwargs))
            except Exception as exc:
                last = exc
                if attempt < policy.max_attempts:
                    self._sleep(delays[attempt - 1] / _MS_PER_SECOND)
                continue
            self._journal.append(
                {
                    "type": "step_finished",
                    "run_id": run_id,
                    "step": definition.name,
                    "ordinal": ordinal,
                    "result": result,
                }
            )
            return result

        raise AdoptError(
            ErrorCode.WORKFLOW_STEP_EXHAUSTED,
            message=f"step {definition.name!r} failed {policy.max_attempts} times: {last}",
            hint=(
                "History is retained: the journal holds one step_started record per "
                "attempt, so the failures are readable without re-running anything."
            ),
        ) from last

    # `list` is declared last on purpose: contracts §10.2 names the method, and
    # the name shadows the builtin for every annotation after it in the class
    # body. Putting it at the end keeps `list[WorkflowHandle]` meaning the
    # builtin everywhere above, without renaming a method the contract fixes.
    def list(self, *, status: WorkflowStatus | None = None) -> list[WorkflowHandle]:
        """Every run, oldest first. Filtered by status when asked.

        Implementation spec §7.4's drain reads this: flipping the backend off
        requires in-flight runs to finish first, and "in-flight" is
        `list(status="running")` being empty *(CR-42)*.
        """
        return [
            self._handle(run)
            for run in self._runs().values()
            if status is None or run["status"] == status
        ]
