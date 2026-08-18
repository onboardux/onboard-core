"""Where an agent-authored module is run once, and what that run may not do -- `04` §6 step 3.

`04` §6 specifies *"SANDBOX RUN -- subprocess, no network, read-only tree bind,
60 s, 512 MiB"*, and three of those five are straightforward. The other two are
worth stating precisely, because the difference between what is **enforced** and
what is **detected** is the whole value of a sandbox claim in a client security
review.

**"Read-only tree bind" is delivered as prevention plus detection, not as a bind
mount -- B1-CR-85.** A bind mount requires `CAP_SYS_ADMIN` or a user namespace.
`adopt` is a CLI a consultant runs inside a client environment; acquiring that
privilege in order to read a repository would be a far larger security-review
liability than the one the mount closes, and a tool that asks for it is a tool a
client is right to refuse. What actually stands between a generated module and
the tree is:

* the **static audit**, which has already rejected the module unless it names no
  write-mode `open()`, no `os` mutation, no `subprocess` and no `shutil`
  (`adopt_map.plugins.AUDIT_RULES`) -- prevention, at the only place the module's
  intent is legible;
* a **digest of the tree taken before and after the run**, compared here. A
  module that reached the tree anyway fails its quarantine with
  `tree_modified` and its facts are discarded.

Detection is weaker than a mount and it is stated as weaker. It is also the half
a bind mount does *not* give you: a mount refuses the write, and nobody ever finds
out the module tried.

**No network is enforced by our own guard, in the child.** The bootstrap installs
`adopt_map.netguard` strict before the module is imported, so a connect attempt
raises inside the child and lands in the result rather than reaching a socket.
This is the one exemption `03` §6 names, used in the direction it was declared
for: the *adapter* may reach the network, and code the adapter produced may not.

**A platform that cannot enforce the limits is refused, never degraded.**
`resource.setrlimit` is POSIX-only, so on Windows the ceilings cannot be applied.
The sandbox returns `status="unsupported"` and the quarantine records
`quarantine_failed` -- which is `04` §6's own step-3 failure branch, keeps the
file for the reviewer, and writes nothing. The alternative -- running the module
with the limits quietly absent -- would produce a passing quarantine whose
sandbox proved nothing, and that is precisely the shape of failure this build
keeps finding in its own instruments.
"""

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from adopt_const import MAP_AGENT_SANDBOX_MAX_BYTES, MAP_AGENT_SANDBOX_TIMEOUT_S
from adopt_obs import get_logger

__all__ = ["SandboxResult", "limits_enforceable", "run_module", "tree_digest"]

_log = get_logger(__name__)

#: What the child prints its result on. A sentinel rather than "the last line",
#: because a module that prints is a module whose output would otherwise be
#: parsed as ours.
_RESULT_PREFIX: Final[str] = "__ADOPT_SANDBOX_RESULT__"

#: How much of the child's stderr reaches the review row. A display bound on a
#: field a human reads, not a decision anybody retunes against evidence -- and
#: bounded at all because agent-authored code can print without limit.
_STDERR_TAIL: Final[int] = 2000  # const-sync: ok -- a display bound on a review field

#: The bootstrap the child runs. It installs the egress guard, imports the
#: module under audit, drives its `extract` over the sampled tree and prints one
#: sentinel-prefixed JSON line. It is built here as source rather than shipped as
#: a file so that what runs is visible at the one place a reviewer looks.
_BOOTSTRAP: Final[str] = """
import json, sys, runpy
from adopt_map.netguard import EgressGuard, guarded
from adopt_map.context import Budget, ExtractorContext
from adopt_map.fileindex import build_index

module_path, root, archetype, tier, sentinel = sys.argv[1:6]
guard = EgressGuard(strict=True)
payload = {"status": "ok", "facts": [], "error": None}
try:
    with guarded(guard):
        namespace = runpy.run_path(module_path)
        factory = namespace.get("EXTRACTOR") or namespace.get("extractor")
        if factory is None:
            raise RuntimeError("module declares neither EXTRACTOR nor extractor")
        extractor = factory() if callable(factory) else factory
        index = build_index(root)
        budget = Budget.starting_at(0.0, stage1_s=float("inf"), total_s=float("inf"))
        ctx = ExtractorContext(root=root, index=index, budget=budget, archetype=archetype, tier=tier)
        for fact in extractor.extract(ctx):
            payload["facts"].append(fact.model_dump(mode="json"))
except BaseException as exc:  # noqa: BLE001 -- the child reports, it never decides
    payload["status"] = "error"
    payload["error"] = f"{type(exc).__name__}: {exc}"
payload["egress_attempts"] = len(guard.attempts)
print(sentinel + json.dumps(payload))
"""


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """What one sandboxed run produced, and why it is or is not usable.

    `status` is `ok`, `error`, `timeout`, `tree_modified` or `unsupported`. Only
    `ok` may contribute facts, and even then they go to quarantine rather than to
    the store -- the sandbox decides whether a reviewer is looking at a module
    that runs, never whether its output is true.
    """

    status: str
    facts: tuple[dict[str, object], ...] = ()
    error: str | None = None
    egress_attempts: int = 0
    stderr: str = ""

    @property
    def usable(self) -> bool:
        return self.status == "ok"


def limits_enforceable() -> bool:
    """Whether this platform can apply the `04` §6 ceilings. POSIX only."""
    return sys.platform != "win32"


def tree_digest(root: Path, paths: Sequence[str]) -> str:
    """A digest over the bytes of every named path, for before/after comparison.

    Paths rather than a walk: the comparison has to answer *"did the module change
    what it was shown"*, and a walk would also fail on a file the operator edited
    in another window during a run that may legitimately take a minute.
    """
    digest = hashlib.blake2b(digest_size=16)
    for rel in sorted(paths):
        target = root / rel
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(target.read_bytes() if target.is_file() else b"<absent>")
        digest.update(b"\0")
    return digest.hexdigest()


def _preexec() -> None:  # pragma: no cover -- runs in the child, after fork
    """Apply the `04` §6 ceilings between `fork` and `exec`.

    Guarded on `sys.platform` rather than on a `try`, because that is the form a
    type checker can act on: on Windows the body is unreachable and `resource`'s
    POSIX-only members are never resolved, while on CI's Linux runner the body is
    checked and `test_sandbox.py` asserts both ceilings actually bite. `run_module`
    never reaches this on a platform where `limits_enforceable()` is false.
    """
    if sys.platform == "win32":
        return
    # The paired code is what makes one directive correct on both platforms:
    # unreachable when `mypy` runs on Windows, and an unused ignore when it runs
    # on CI's Linux runner. Either alone fails on the other machine.
    import resource  # type: ignore[unreachable,unused-ignore]

    resource.setrlimit(
        resource.RLIMIT_AS, (MAP_AGENT_SANDBOX_MAX_BYTES, MAP_AGENT_SANDBOX_MAX_BYTES)
    )
    resource.setrlimit(
        resource.RLIMIT_CPU, (MAP_AGENT_SANDBOX_TIMEOUT_S, MAP_AGENT_SANDBOX_TIMEOUT_S)
    )


def run_module(
    module_path: Path,
    *,
    root: Path,
    sampled_paths: Sequence[str],
    archetype: str = "web",
    tier: str | None = None,
    timeout_s: int = MAP_AGENT_SANDBOX_TIMEOUT_S,
) -> SandboxResult:
    """Run one audited module once, under the `04` §6 ceilings.

    The environment handed to the child is **empty except for the import path**.
    `04` §4.2 hard constraint 2 forbids the module to read `os.environ` and the
    static audit rejects it for trying; passing an empty environment means the
    attempt has nothing to find even if both were somehow evaded.
    """
    if not limits_enforceable():
        _log.warn("sandbox_unsupported", platform=sys.platform)
        return SandboxResult(
            status="unsupported",
            error=(
                f"resource limits are not enforceable on {sys.platform}; the module was not run"
            ),
        )

    before = tree_digest(root, sampled_paths)
    command = [
        sys.executable,
        # `-s` drops the user site directory; `-I` is deliberately *not* used
        # because it implies `-E`, which would discard the `PYTHONPATH` this
        # child is given -- an isolation flag that isolates the child from the
        # only thing it needs.
        "-s",
        "-c",
        _BOOTSTRAP,
        str(module_path),
        str(root),
        archetype,
        tier or "",
        _RESULT_PREFIX,
    ]
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed argv, no shell, no interpolation
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(root),
            env={"PYTHONPATH": _import_path(), "PYTHONHASHSEED": "0"},
            preexec_fn=_preexec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(status="timeout", error=f"exceeded {timeout_s}s")

    if tree_digest(root, sampled_paths) != before:
        return SandboxResult(
            status="tree_modified",
            error="the module changed a file it was given to read",
            stderr=completed.stderr[-_STDERR_TAIL:],
        )

    for line in completed.stdout.splitlines():
        if line.startswith(_RESULT_PREFIX):
            payload = json.loads(line[len(_RESULT_PREFIX) :])
            return SandboxResult(
                status=str(payload["status"]),
                facts=tuple(payload["facts"]),
                error=payload["error"],
                egress_attempts=int(payload.get("egress_attempts", 0)),
                stderr=completed.stderr[-_STDERR_TAIL:],
            )
    return SandboxResult(
        status="error",
        error="the module produced no result line",
        stderr=completed.stderr[-_STDERR_TAIL:],
    )


def _import_path() -> str:
    """This interpreter's import roots, passed explicitly rather than inherited.

    The child gets an **empty environment** apart from this and a fixed hash seed,
    so nothing in the operator's shell -- an API key, a proxy, a token -- is
    visible to agent-authored code even if every other control failed. It still
    needs to import `adopt_map` and its dependencies, and `sys.path` is where this
    process found them, whether that is a checkout, an editable install or a
    wheel.
    """
    return os.pathsep.join(entry for entry in sys.path if entry)
