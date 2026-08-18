"""The quarantine sandbox -- `04` §6 step 3, and what it refuses to pretend.

**Two arms, and exactly one of them runs on any given machine.** CI runs
`ubuntu-24.04`, where `resource.setrlimit` applies the ceilings and the
enforcement cases below execute. A Windows developer machine cannot apply them at
all, so `run_module` returns `unsupported` and the refusal case executes instead.

`test_one_arm_ran` is what stops that being a hole: it asserts the platform
answered one way or the other, so a future change that made
`limits_enforceable()` return something meaningless on both platforms fails here
rather than leaving a suite that silently tests nothing on every machine.

**`unsupported` is a refusal, not a degrade** (`adopt_map.sandbox`). Running an
agent-authored module with the ceilings quietly absent would produce a passing
quarantine whose sandbox proved nothing, which is the exact shape of failure this
build has now found five times in its own instruments.
"""

import sys
from pathlib import Path

import pytest
from adopt_map.sandbox import SandboxResult, limits_enforceable, run_module, tree_digest

pytestmark = pytest.mark.unit

_ENFORCED = limits_enforceable()

_TRIVIAL = '''"""A module that yields nothing."""


class Extractor:
    def extract(self, ctx):
        return iter(())


EXTRACTOR = Extractor
'''


def test_one_arm_ran() -> None:
    """The platform answered, and the suite knows which arm it exercised."""
    assert (sys.platform != "win32") == _ENFORCED


def test_the_result_shape_says_whether_facts_may_be_used() -> None:
    """Only `ok` is usable, and every other status names why it is not.

    A caller reading `facts` without reading `status` would treat a timed-out run
    as a run that found nothing -- which is the difference between "this family is
    absent" and "we never looked".
    """
    assert SandboxResult(status="ok").usable
    for status in ("error", "timeout", "tree_modified", "unsupported"):
        assert not SandboxResult(status=status).usable


@pytest.mark.skipif(_ENFORCED, reason="the enforcement arm runs where rlimits apply")
def test_an_unsupported_platform_refuses_rather_than_running(tmp_path: Path) -> None:
    """Windows: the module is **not run**, and the reason names the platform."""
    module = tmp_path / "m.py"
    module.write_text(_TRIVIAL, encoding="utf-8")

    result = run_module(module, root=tmp_path, sampled_paths=[])

    assert result.status == "unsupported"
    assert not result.usable
    assert sys.platform in (result.error or "")
    assert result.facts == ()


@pytest.mark.skipif(not _ENFORCED, reason="the refusal arm runs where rlimits do not apply")
def test_a_trivial_module_runs_and_reports_ok(tmp_path: Path) -> None:
    """The positive control: a module that does nothing forbidden completes."""
    module = tmp_path / "m.py"
    module.write_text(_TRIVIAL, encoding="utf-8")

    result = run_module(module, root=tmp_path, sampled_paths=[])

    assert result.status == "ok", result.error
    assert result.egress_attempts == 0


@pytest.mark.skipif(not _ENFORCED, reason="the refusal arm runs where rlimits do not apply")
def test_a_module_that_never_returns_is_killed(tmp_path: Path) -> None:
    """`04` §6: crash or timeout -> `quarantine_failed`, file kept for the reviewer.

    Driven with a one-second timeout rather than `MAP_AGENT_SANDBOX_TIMEOUT_S`,
    because `03` §5 bans sleeps in tests and a sixty-second assertion is a sleep
    wearing a constant's name. What is asserted is that the ceiling is applied at
    all; its *value* is the tunable S1.8 ratifies.
    """
    module = tmp_path / "m.py"
    module.write_text("while True:\n    pass\n", encoding="utf-8")

    result = run_module(module, root=tmp_path, sampled_paths=[], timeout_s=1)

    assert result.status == "timeout"
    assert module.is_file(), "the file is kept for the reviewer (`04` §6 step 3)"


@pytest.mark.skipif(not _ENFORCED, reason="the refusal arm runs where rlimits do not apply")
def test_a_module_that_writes_to_the_tree_fails_its_quarantine(tmp_path: Path) -> None:
    """B1-CR-85: the read-only guarantee is prevention plus **detection**.

    The static audit refuses a module that *names* a write, so this plants one
    that reaches the tree anyway and asserts the digest comparison catches it. A
    bind mount would have refused the write and told nobody it was attempted.
    """
    (tmp_path / "seen.txt").write_text("original\n", encoding="utf-8")
    module = tmp_path / "m.py"
    module.write_text(
        "import pathlib\n"
        "pathlib.Path('seen.txt').write_text('changed\\n', encoding='utf-8')\n"
        "class Extractor:\n"
        "    def extract(self, ctx):\n"
        "        return iter(())\n"
        "EXTRACTOR = Extractor\n",
        encoding="utf-8",
    )

    result = run_module(module, root=tmp_path, sampled_paths=["seen.txt"])

    assert result.status == "tree_modified"
    assert not result.usable


def test_the_tree_digest_notices_a_changed_byte(tmp_path: Path) -> None:
    """The detection half, in isolation and on every platform."""
    target = tmp_path / "a.txt"
    target.write_text("one\n", encoding="utf-8")
    before = tree_digest(tmp_path, ["a.txt"])

    assert tree_digest(tmp_path, ["a.txt"]) == before
    target.write_text("two\n", encoding="utf-8")
    assert tree_digest(tmp_path, ["a.txt"]) != before


def test_the_digest_distinguishes_an_absent_file_from_an_empty_one(tmp_path: Path) -> None:
    """Otherwise a module that deleted a sampled file would digest as unchanged."""
    absent = tree_digest(tmp_path, ["gone.txt"])
    (tmp_path / "gone.txt").write_text("", encoding="utf-8")
    assert tree_digest(tmp_path, ["gone.txt"]) != absent
