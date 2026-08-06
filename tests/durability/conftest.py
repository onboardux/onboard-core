"""`--backend` for the durability suite.

The default is `inproc` so `uv run pytest` in this repository runs the drill
without asking for anything external, which is what makes `03` §6's "every PR"
cadence affordable.
"""

import importlib
from collections.abc import Iterator

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
