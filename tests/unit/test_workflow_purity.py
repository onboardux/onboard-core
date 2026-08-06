"""`workflow-body-purity`, at import time and across the tree.

*Fails when* a non-deterministic call reaches a `@workflow` body. *Matters
because* a body is replayed on resume, so a clock read or a random draw makes the
second run take a different branch -- and that failure appears in production, as
a workflow that silently did something else after a restart. *No other instrument
catches it because* the type system cannot see determinism and no test crashes a
workflow midway by accident.

One row per rule class rather than one per banned symbol: the symbol table is
data, and testing each entry would assert the table against itself. What is worth
asserting is that each *kind* of impurity is caught, and that the two enforcement
points implementation spec §4.14 requires read the same table.
"""

import textwrap

import pytest

from adopt_obs import AdoptError, ErrorCode
from adopt_workflow import RetryPolicy, assert_pure, clear_registry, scheduled, step, workflow
from adopt_workflow.purity import impurities_in_source

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    clear_registry()


# Each row is `(label, body)`. The body is source text rather than a real
# function so a single table can carry cases that must never be imported.
IMPURE_BODIES: list[tuple[str, str]] = [
    ("clock_direct", "def f(ctx):\n    return datetime.now()\n"),
    ("clock_aliased", "def f(ctx):\n    return dt.datetime.now()\n"),
    ("clock_utcnow", "def f(ctx):\n    return datetime.utcnow()\n"),
    ("sleep", "def f(ctx):\n    time.sleep(1)\n"),
    ("random_draw", "def f(ctx):\n    return random.random()\n"),
    ("uuid_draw", "def f(ctx):\n    return uuid.uuid4()\n"),
    ("id_mint", "def f(ctx):\n    return new_id('run')\n"),
    ("env_read_call", "def f(ctx):\n    return os.getenv('HOME')\n"),
    ("env_read_attr", "def f(ctx):\n    return os.environ['HOME']\n"),
    ("network_httpx", "def f(ctx):\n    return httpx.get('http://x')\n"),
    ("network_requests", "def f(ctx):\n    return requests.post('http://x')\n"),
    ("network_socket", "def f(ctx):\n    return socket.create_connection(('x', 1))\n"),
    ("subprocess", "def f(ctx):\n    return subprocess.run(['ls'])\n"),
    ("model_call", "def f(ctx):\n    return runner.AgentRunner.run(req)\n"),
    ("file_io", "def f(ctx):\n    return open('/tmp/x')\n"),
    ("console_io", "def f(ctx):\n    return input()\n"),
]


@pytest.mark.parametrize(("label", "body"), IMPURE_BODIES, ids=[r[0] for r in IMPURE_BODIES])
def test_each_kind_of_impurity_is_caught(label: str, body: str) -> None:
    found = impurities_in_source(body)
    assert found, f"{label}: no impurity reported for a body that has one"


def test_a_pure_body_is_accepted() -> None:
    """The converse. Without it a checker that rejected everything would pass."""
    pure = "def f(ctx, args):\n    total = args['a'] + args['b']\n    return ctx.step(add, total)\n"
    assert impurities_in_source(pure) == []


def test_workflow_decorator_refuses_an_impure_body_at_import_time() -> None:
    """Sprint S8 Final Output Validation item 6, asserted at the seam it names."""
    with pytest.raises(AdoptError) as caught:

        @workflow(name="impure-flow")
        def _flow(ctx: object, args: dict[str, int]) -> str:
            import datetime

            return datetime.datetime.now().isoformat()

    assert caught.value.code is ErrorCode.WORKFLOW_BODY_IMPURE
    assert "clock reading" in caught.value.message


def test_a_pure_workflow_registers() -> None:
    @workflow(name="pure-flow")
    def _flow(ctx: object, args: dict[str, int]) -> int:
        return args["a"]

    assert _flow.__adopt_workflow__.name == "pure-flow"


def test_a_body_whose_source_cannot_be_read_is_refused_not_skipped() -> None:
    """A body nobody can read is a body nobody can prove pure.

    Silently accepting it would make `exec`-built workflows the one way around
    both enforcement points at once.
    """
    namespace: dict[str, object] = {}
    # `exec` is the subject, not a shortcut: it is the one way to build a body
    # whose source no checker can read, which is exactly the case under test.
    exec(  # noqa: S102
        compile("def built(ctx):\n    return 1\n", "<generated>", "exec"), namespace
    )
    built = namespace["built"]
    assert callable(built)
    with pytest.raises(AdoptError) as caught:
        assert_pure(built)
    assert caught.value.code is ErrorCode.WORKFLOW_BODY_IMPURE
    assert "could not be read" in caught.value.message


def test_the_lint_contract_and_the_decorator_share_one_table() -> None:
    """Implementation spec §4.14 requires both enforcement points.

    Two copies of the banned-call table would drift, and the copy that drifts is
    the one nobody is reading when a new alias is added -- silently, in the
    permissive direction. Asserted by identity, not by comparing contents.
    """
    from tools.contracts import purity as contract_purity

    from adopt_workflow import purity as package_purity

    assert contract_purity.find_impure_workflow_bodies is package_purity.find_impure_workflow_bodies


def test_the_contract_finds_an_impure_body_in_module_source() -> None:
    """The tree-walking half: a decorated body in a file nobody imports."""
    from tools.contracts.purity import find_impure_workflow_bodies

    module = textwrap.dedent(
        """
        @workflow(name="x")
        def flow(ctx):
            return datetime.now()
        """
    )
    findings = find_impure_workflow_bodies(module, "<memory>")
    assert len(findings) == 1
    assert "clock reading" in findings[0][1]


def test_an_undecorated_function_is_not_scanned() -> None:
    """Only `@workflow` bodies are constrained. A step may read a clock -- that
    is the entire point of steps, and a checker that flagged them would push
    people to stop declaring steps."""
    from tools.contracts.purity import find_impure_workflow_bodies

    module = "def helper():\n    return datetime.now()\n"
    assert find_impure_workflow_bodies(module, "<memory>") == []


def test_scheduled_refuses_to_decorate_a_workflow() -> None:
    """PRD F14.5, made mechanical rather than left to a review line."""

    @workflow(name="not-a-cron-job")
    def _flow(ctx: object, args: dict[str, int]) -> int:
        return 1

    with pytest.raises(AdoptError) as caught:
        scheduled(name="not-a-cron-job", cron="0 * * * *")(_flow)
    assert caught.value.code is ErrorCode.WORKFLOW_BODY_IMPURE
    assert "cron" in (caught.value.hint or "")


def test_scheduled_accepts_a_plain_periodic_job() -> None:
    @step(name="prune", retries=RetryPolicy(max_attempts=1))
    def _prune(ctx: object) -> None:
        return None

    decorated = scheduled(name="prune-runs", cron="0 3 * * *")(_prune)
    assert decorated.__adopt_scheduled__.cron == "0 3 * * *"
