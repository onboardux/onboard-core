"""`--backend` for the durability suite.

The default is `inproc` so `uv run pytest` in this repository runs the drill
without asking for anything external, which is what makes `03` §6's "every PR"
cadence affordable.
"""

import importlib
from collections.abc import Callable, Iterator
from uuid import uuid4

import drill_backends
import pytest

from adopt_workflow import REGISTRY


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--backend",
        action="store",
        default="inproc",
        help="Workflow backend the durability suite runs against: inproc | dbos.",
    )


@pytest.fixture
def backend(request: pytest.FixtureRequest) -> str:
    name: str = request.config.getoption("--backend")
    return name


@pytest.fixture(scope="session")
def suite_run_id() -> str:
    """A nonce that makes this run of the suite distinguishable from the last.

    **The DBOS backend's state outlives the suite, and fixed keys made the drill
    a one-shot instrument.** `tmp_path` gives each test a fresh effects file, but
    the dedupe marker lives in Postgres and the run lives in DBOS's own tables --
    so a second run against the same database found `charge:drill-1` already
    claimed, committed nothing, and reported "the effect did not commit before
    the kill". Green the first time, red every time after, for no defect.

    CI creates a fresh database per job and would never have shown this. That is
    the argument for fixing it rather than relying on the container: a drill a
    developer cannot run twice is one they stop running.
    """
    return uuid4().hex[:12]


@pytest.fixture
def drill_key(suite_run_id: str) -> Callable[[str], str]:
    """`label -> idempotency key`, unique to this run and stable within it.

    Stable within the run matters as much as unique across runs: the parent
    hands the key to the child on `argv` and later finds the resumed handle by
    it, so a key regenerated between those two points would look like a
    different run.
    """

    def _key(label: str) -> str:
        return f"drill-{label}-{suite_run_id}"

    return _key


@pytest.fixture(autouse=True)
def _drill_workflow_is_registered() -> Iterator[None]:
    """Guarantee the drill's workflow is in the registry, re-registering if not.

    A resumed run resolves its body **by name**, so the registry must hold it --
    and the registry is process-global, which means another test file clearing it
    is enough to break a resume here. `import` alone does not fix that: the module
    is already in `sys.modules`, so the decorators never run a second time and the
    registration stays missing. The reload is what makes this suite independent of
    what ran before it, which a full-suite run otherwise decides by file order.
    """
    import drill_workflows as declarations

    if ("workflow", "paying-flow", 1) not in REGISTRY:
        importlib.reload(declarations)

    yield


@pytest.fixture(autouse=True)
def _no_worker_outlives_its_test() -> Iterator[None]:
    """Close every client the test built, before the next test spawns a child.

    **A durable client is a worker.** DBOS polls the shared queue for the life
    of the process, so a client left open by one test keeps dequeuing during the
    next -- and the next test's whole premise is that its *child* is the only
    executor. Left open, the parent won the race often enough to run the child's
    workflow itself, in a process where the drill's run directory is unset. The
    symptom was "child never committed its effect", which reads like a
    durability defect and was a test-lifecycle one.

    Teardown rather than a `with` block because the tests need the client after
    the helper that builds it returns, and this states the invariant once for
    the file instead of three times.
    """
    yield
    drill_backends.close_built_clients()
