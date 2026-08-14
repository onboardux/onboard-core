"""The one subprocess seam -- `03` §6, `03` §2. **B1-CR-54.**

`03` §6 said *"Build 0's exec seam only, with a tool allowlist and no shell
interpolation."* **Build 0 has no exec seam.** The only `subprocess.run` in
`packages/` is `adopt_schema.lint`'s fixed-argv `git` call, carrying its own
`noqa` and its own justification -- a gate script, not a seam anything else may
use. So the sentence named a primitive that does not exist, in the same family as
B1-CR-22/33/37/53, and `03` §6 is repaired to point here.

`03` §2 already fixes the policy this module implements: `universal-ctags` is
**GPL-2.0+, subprocess only, never linked**, and *"needs a `subprocess-deps.toml`
entry, without which the licence gate treats it as `in-binary` and rejects it."*
That is the whole reason a seam exists rather than a call site: the licence
argument is about **how** we invoke a tool, and "how" has to be a place, not a
habit.

Three properties, each of which is the reason for a rule:

1. **Allowlist by binary name.** A tool not on the list is refused before
   `shutil.which` is consulted, so an extractor cannot reach an arbitrary
   executable by naming it.
2. **`shell=False`, always, and argv only.** Client paths are *arguments*, never
   interpolated into a command string (`03` §6). A repository whose directory is
   named ``; rm -rf ~`` is a repository we index, not a shell we hand it to.
3. **A missing tool is a degradation, not a failure.** `01` F9.2's ladder runs
   grammar -> ctags -> regex -> decline, and the tool-absent arm is the one that
   fires on a machine where nobody installed ctags. It returns `None` and the
   ladder records the transition; raising would turn a normal laptop into a
   failed run.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from adopt_const import MAP_TOOL_TIMEOUT_S
from adopt_obs import AdoptError, ErrorCode, get_logger

__all__ = ["ALLOWED_TOOLS", "ToolResult", "run_tool", "tool_available"]

_log = get_logger(__name__)

#: Every binary this build may invoke, with the reason and the licence mode that
#: admits it. **Adding a row here is a licence decision** (`03` §2, `03` §7.3):
#: a copyleft tool is permissible `subprocess`-only and needs its
#: `subprocess-deps.toml` row in the same change, or the gate treats it as
#: `in-binary` and fails closed.
ALLOWED_TOOLS: Final[dict[str, str]] = {
    "ctags": "universal-ctags -- the degrade ladder's second rung (GPL-2.0+, subprocess only)",
}


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What one invocation produced. `stderr` is deliberately absent.

    A tool's stderr can contain fragments of the client file it choked on, and
    `03` §5.9 forbids client source content in any report. The exit status says
    whether it worked; the caller degrades on anything else.
    """

    tool: str
    exit_status: int
    stdout: str


def tool_available(tool: str) -> bool:
    """Whether an allowlisted tool is present on this machine.

    Raises:
        AdoptError: ``MAP_EXTRACTOR_FAILED`` when `tool` is not allowlisted --
            asked and answered before the filesystem is consulted, so an
            unknown name cannot be probed for.
    """
    _require_allowed(tool)
    return shutil.which(tool) is not None


def run_tool(tool: str, arguments: list[str], *, cwd: Path | None = None) -> ToolResult | None:
    """Run an allowlisted tool with an argv. **No shell, ever.**

    Args:
        tool: The binary name. Must be a key of `ALLOWED_TOOLS`.
        arguments: Arguments as a list. Client paths belong here, as elements --
            never concatenated into `tool`.
        cwd: Working directory. The client tree is read-only; nothing this seam
            runs is permitted to write into it, which the allowlist enforces by
            only ever admitting read-only analysers.

    Returns:
        The result, or `None` when the tool is not installed. `None` is the
        ladder's tool-absent arm (`01` F9.2) and is not an error.

    Raises:
        AdoptError: ``MAP_EXTRACTOR_FAILED`` when `tool` is not allowlisted.
    """
    _require_allowed(tool)
    resolved = shutil.which(tool)
    if resolved is None:
        _log.info("map_tool_absent", tool=tool)
        return None
    try:
        completed = subprocess.run(  # noqa: S603 -- allowlisted binary, argv only, shell=False
            [resolved, *arguments],
            cwd=None if cwd is None else str(cwd),
            capture_output=True,
            text=True,
            timeout=MAP_TOOL_TIMEOUT_S,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        # A tool that will not start, or one that hangs past its timeout, is the
        # same answer as a tool that is not installed: the rung is unavailable
        # and the ladder degrades. It is never the reason a run fails.
        _log.warn("map_tool_unusable", tool=tool)
        return None
    return ToolResult(tool=tool, exit_status=completed.returncode, stdout=completed.stdout)


def _require_allowed(tool: str) -> None:
    if tool in ALLOWED_TOOLS:
        return
    raise AdoptError(
        ErrorCode.MAP_EXTRACTOR_FAILED,
        message=f"{tool!r} is not on the subprocess allowlist",
        hint="`03` §6 permits the declared analysis binaries only, and `03` §2 makes "
        "adding one a licence decision: a copyleft tool is permitted subprocess-only "
        "and needs its `subprocess-deps.toml` row in the same change, or the licence "
        "gate treats it as in-binary and fails closed.",
    )
