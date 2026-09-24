"""Run one `adopt` command the safe way, and summarise what came back.

    python adopt_run.py [--save DIR] [--adopt PATH] [--allow-in-tree] -- <adopt arguments>
    python adopt_run.py --save ../acme-adopt/runs -- map --report

What it does, each for a reason that has already cost somebody time:

* **Adds `--json`** when the command supports it and you left it off.
* **Keeps stdout and stderr apart.** The envelope is stdout; structured logs are
  stderr. Merging them (`2>&1`) breaks every parser.
* **Reads to the end.** Piping `adopt` into `head` closes the pipe early and the
  CLI exits 1 with nothing on stderr -- a failure that is not one.
* **Saves the full envelope** to a file and prints a short summary: the exit code
  and its meaning, the error code and hint if any, and the top-level keys.
* **Refuses an output path inside the git work tree** (`export`, `pack --out`,
  `handover ... --out`, `init --write-statement`, `import --into`), including the
  defaults those commands fall back to. `map` and `refresh` walk the tree, so
  output left there is mapped as the client's system -- 1,174 bogus identities in
  one measured case -- and the append-only store keeps them forever. Only
  `.adopt/`, which the walk always skips, is exempt. `--allow-in-tree` overrides,
  and should need a person's reason.

Exits with `adopt`'s own exit code, so a caller can branch on 0 / 1 / 2 / 3 / 4.
Python 3.8+; `surface.json` beside it is generated -- never edit it by hand.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_SURFACE = Path(__file__).resolve().with_name("surface.json")

EXIT_MEANING = {
    0: "success",
    1: "operational failure -- nothing below it is reliable evidence",
    2: "usage error -- the invocation was wrong; fix it, do not retry verbatim",
    3: "policy refusal -- it would not, by design; report it, do not route around it",
    4: "success with findings -- it worked and found something a person must see",
}

#: Commands whose output lands on disk, and where: `(command, option, default)`.
#: An option of `None` is the first positional argument. `scripts/gen_skills.py
#: --check` fails if a command gains an `--out` this table does not name.
OUTPUT_PATHS: tuple[tuple[str, str | None, str | None], ...] = (
    ("export", None, None),
    ("pack", "--out", "handover"),
    ("handover elicit", "--out", "handover"),
    ("handover pack", "--out", "handover"),
    ("handover snapshot", "--out", "handover/acceptance"),
    ("handover close", "--out", None),
    ("init", "--write-statement", None),
    ("import", "--into", None),
)


def _load_surface() -> dict[str, Any]:
    try:
        loaded: dict[str, Any] = json.loads(_SURFACE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded


def _command_path(args: list[str], known: set[str]) -> tuple[str, int]:
    """The longest known command path in `args`, skipping root options."""
    words: list[str] = []
    start = 0
    for index, token in enumerate(args):
        if token.startswith("-"):
            if words:
                break
            start = index + 1
            continue
        candidate = " ".join([*words, token])
        if candidate in known:
            words.append(token)
            continue
        break
    return " ".join(words), start + len(words)


def _option_value(args: list[str], option: str) -> str | None:
    for index, token in enumerate(args):
        if token == option and index + 1 < len(args):
            return args[index + 1]
        if token.startswith(option + "="):
            return token.split("=", 1)[1]
    return None


def _first_positional(args: list[str]) -> str | None:
    skip = False
    for token in args:
        if skip:
            skip = False
            continue
        if token.startswith("--"):
            skip = "=" not in token and token not in ("--json", "--help")
            continue
        return token
    return None


def _work_tree() -> Path | None:
    git = shutil.which("git")
    if git is None:
        return None
    done = subprocess.run(
        [git, "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
    )
    return Path(done.stdout.strip()).resolve() if done.returncode == 0 else None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _in_tree_outputs(command: str, rest: list[str]) -> list[str]:
    """Output paths this invocation would write inside the git work tree."""
    tree = _work_tree()
    if tree is None:
        return []
    offending: list[str] = []
    for name, option, default in OUTPUT_PATHS:
        if name != command:
            continue
        value = _first_positional(rest) if option is None else _option_value(rest, option)
        value = value if value is not None else default
        if value is None:
            continue
        target = Path(value).expanduser()
        target = (target if target.is_absolute() else Path.cwd() / target).resolve()
        if _inside(target, tree) and not _inside(target, tree / ".adopt"):
            offending.append(f"{option or 'DIRECTORY'} {value}")
    return offending


def _error_envelope(stderr: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(stderr):
        if char != "{":
            continue
        try:
            found, _ = decoder.raw_decode(stderr[index:])
        except ValueError:
            continue
        if isinstance(found, dict) and isinstance(found.get("error"), dict):
            error: dict[str, Any] = found["error"]
            return error
    return None


def _describe(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return f"[{len(value)} items]"
    if isinstance(value, dict):
        return f"{{{len(value)} keys}}"
    text = json.dumps(value)
    return text if len(text) <= 80 else text[:77] + "..."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--save", help="Directory for the saved envelope. Default: a temp dir.")
    parser.add_argument("--adopt", help="Path to the adopt executable.")
    parser.add_argument("--allow-in-tree", action="store_true", help="Permit in-tree output.")
    parser.add_argument("argv", nargs=argparse.REMAINDER, help="-- then the adopt arguments")
    ns = parser.parse_args()
    args = ns.argv[1:] if ns.argv[:1] == ["--"] else ns.argv
    if not args:
        parser.error("give the adopt arguments after --")

    executable = ns.adopt or shutil.which("adopt")
    if executable is None:
        print("adopt is not on PATH; run scripts/preflight.py", file=sys.stderr)
        return 2

    surface = _load_surface()
    commands: dict[str, Any] = surface.get("commands", {})
    command, consumed = _command_path(args, set(commands))
    rest = args[consumed:]

    if not ns.allow_in_tree:
        offending = _in_tree_outputs(command, rest)
        if offending:
            print(
                f"REFUSED: `adopt {command}` would write inside the git work tree "
                f"({', '.join(offending)}). map and refresh walk the tree, so this output "
                "would be mapped as the client's system, permanently. Point it at the "
                "engagement workspace (for example ../<system>-adopt/...).",
                file=sys.stderr,
            )
            return 2

    if commands.get(command, {}).get("json") and "--json" not in args and "--help" not in args:
        args = [*args, "--json"]

    env = dict(os.environ, PYTHONUTF8="1")
    done = subprocess.run([executable, *args], capture_output=True, env=env, check=False)
    stdout = done.stdout.decode("utf-8", errors="replace")
    stderr = done.stderr.decode("utf-8", errors="replace")

    save_dir = Path(ns.save) if ns.save else Path(tempfile.gettempdir()) / "adopt-runs"
    save_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f")
    slug = (command or "adopt").replace(" ", "-")
    out_file = save_dir / f"{stamp}-{slug}.json"
    out_file.write_text(stdout, encoding="utf-8")
    (save_dir / f"{stamp}-{slug}.stderr.log").write_text(stderr, encoding="utf-8")

    code = done.returncode
    print(f"adopt {' '.join(args)}")
    print(f"exit {code}: {EXIT_MEANING.get(code, 'unexpected exit code')}")
    error = _error_envelope(stderr)
    if error:
        print(f"error.code: {error.get('code')}  category: {error.get('category')}")
        print(f"message: {error.get('message')}")
        print(f"hint: {error.get('hint')}")
    try:
        payload = json.loads(stdout) if stdout.strip() else None
    except ValueError:
        payload = None
        if stdout.strip():
            print("stdout was not JSON (this command may not support --json)")
    if isinstance(payload, dict):
        for key, value in payload.items():
            print(f"  {key}: {_describe(value)}")
    print(f"saved: {out_file}")
    return code


if __name__ == "__main__":
    sys.exit(main())
