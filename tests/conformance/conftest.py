"""`--adapters` for the conformance suite. AI spec §7.1.

The default is `fake_recorded` so `uv run pytest` in this repository runs the
thirteen cases without asking for a credential or a container -- the same reason
`tests/durability/conftest.py` defaults `--backend` to `inproc`.

**A named adapter that cannot be reached FAILS; it never skips.** S2's reading,
restated once per sprint because it keeps mattering: a skipped isolation test is
not evidence of isolation, and a skipped conformance case is not evidence that an
adapter satisfies the contract. `conformance-matrix` counts green adapters, and a
suite that skipped its way to green would report the two-adapter rule satisfied by
a pipeline that exercised one.

**`fake_recorded` alone can never satisfy the gate**, by construction rather than
by convention: `AdapterKind` types it `test`, and the gate requires **two
non-test** adapters green *(CR-48 deferred the "at least one local" half; the
arity and the typing are what survive, and they are the half doing the structural
work)*. That typing is the reason a recorded fake is safe to make the default
here.

**Model selection is not this file's business.** `--adapters` here takes bare
ids; `scripts/conformance_matrix.py` resolves an `id=model` form and sets
`ADOPT_MODEL` per subprocess, because the harness reads it from the environment
and two hosted vendors cannot share one model id.
"""

import pytest

#: Comma-separated, because that is the form `05` S7's validation lines use:
#: `--adapters=local_openai,fake_recorded`.
_DEFAULT: str = "fake_recorded"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--adapters",
        action="store",
        default=_DEFAULT,
        help=(
            "Comma-separated adapter ids the conformance suite runs against, "
            "e.g. --adapters=local_openai,fake_recorded. A named adapter that "
            "cannot be reached fails; it never skips."
        ),
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parameterize every case over every requested adapter.

    Done here rather than with a fixture so each adapter appears as its own test
    id -- `test_case_04_single_tool_call[local_openai]` -- which is what makes a
    per-adapter failure readable in a CI log and what lets the matrix job report
    which adapter was green.
    """
    if "adapter_id" not in metafunc.fixturenames:
        return
    requested = [
        name.strip()
        for name in str(metafunc.config.getoption("--adapters")).split(",")
        if name.strip()
    ]
    if not requested:
        raise pytest.UsageError("--adapters was given with no adapter ids")
    metafunc.parametrize("adapter_id", requested, ids=requested)
