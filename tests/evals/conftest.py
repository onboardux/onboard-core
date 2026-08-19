"""`--eval-adapters` for the E1-E9 suites. `04` §8.

**There is no default and there is no fake.** `tests/conformance/conftest.py`
defaults to `fake_recorded` because a recorded fake can demonstrate *protocol*
conformance; it can demonstrate nothing about the quality thresholds `04` §8
gates on, because a recorded fake replays its script whatever it is sent (Build 0
CR-51). So an unnamed adapter parameterizes **zero** eval cases, and the suite
says so out loud rather than reporting green.

**Zero cases is reported, never silently passed.** `test_eval_harness.py` carries
the anti-vacuity guard: if adapters were named, cases must exist; if none were,
the session prints a line stating that E1-E9 did not execute. B1-CR-69 is the
reason the guard is separate from the parameterization -- a guard built from the
same parameterization it is checking proves only that the code is consistent with
itself.

**`--eval-adapters` takes `id=model`**, matching `scripts/conformance_matrix.py`,
because `ADOPT_MODEL` is one process-wide value and two hosted vendors cannot
share a model id (Build 0 CR-51).
"""

import pytest

# `pytest_addoption` is **not** here, and that is the whole point of B1-CR-100.
# pytest registers options only from conftest files it loads while parsing the
# command line -- the rootdir's, and those of the paths named as arguments. This
# file sits under `tests/evals/`, which is neither, so `--eval-adapters` did not
# exist for the command `04` §8 and `05` S1.7 both tell an operator to run:
#
#     uv run pytest -q -m evals --eval-adapters=openai=...,anthropic=...
#     ERROR: unrecognized arguments: --eval-adapters=...
#
# The option now lives in `tests/conftest.py`, which `testpaths = ["tests"]`
# makes an initial conftest. Everything that *reads* the option stays here,
# beside the suites that use it.


def eval_targets(config: pytest.Config) -> list[tuple[str, str]]:
    """`(adapter_id, model)` pairs, parsed once."""
    raw = str(config.getoption("--eval-adapters")).strip()
    if not raw:
        return []
    targets = []
    for chunk in raw.split(","):
        if "=" not in chunk:
            raise pytest.UsageError(
                f"--eval-adapters takes id=model pairs; {chunk!r} names no model. "
                "One process-wide ADOPT_MODEL cannot serve two vendors."
            )
        adapter_id, model = chunk.split("=", 1)
        targets.append((adapter_id.strip(), model.strip()))
    return targets


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "eval_target" not in metafunc.fixturenames:
        return
    targets = eval_targets(metafunc.config)
    metafunc.parametrize(
        "eval_target", targets, ids=[f"{adapter}={model}" for adapter, model in targets]
    )


def pytest_report_header(config: pytest.Config) -> str:
    targets = eval_targets(config)
    if not targets:
        return (
            "evals: no --eval-adapters named, so E1-E9 DID NOT EXECUTE. "
            "A green run here is not evidence about any threshold in `04` §8."
        )
    return "evals: " + ", ".join(f"{adapter}={model}" for adapter, model in targets)
