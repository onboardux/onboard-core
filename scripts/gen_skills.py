"""Generate, and check, the machine-derived parts of the `adopt` Claude Code plugin.

`plugins/adopt/` is a set of agent skills for driving the published CLI on a
client engagement. Most of each skill is judgement a person wrote. The rest is
**facts about the CLI** -- which commands and flags exist, which error codes a
command raises, which version the skills describe -- and a fact maintained by
hand in prose drifts from the binary the day after it is written, silently.
This script is the other half of that bargain, exactly as `tools/gen_docs.py` is
for the handbook and `adopt-schema generate` is for the manifest's targets.

    uv run python scripts/gen_skills.py              # write every generated part
    uv run python scripts/gen_skills.py --check      # fail on drift; writes nothing
    uv run python scripts/gen_skills.py --self-test  # prove --check can fail

What it owns, read from the *installed* `adopt_cli` and `adopt_obs`:

* `skills/adopt-cli/references/commands.md` -- every command and flag.
* `skills/adopt-cli/references/errors.md` -- exit codes and the error registry.
* `skills/adopt-cli/scripts/surface.json` -- the command surface the bundled
  scripts read, and the optional features preflight detects.
* `<!-- BEGIN GENERATED: errors PREFIX[,PREFIX] -->` and
  `<!-- BEGIN GENERATED: options <command path> -->` blocks inside any skill.
* The plugin's `version`, in `plugin.json` and the marketplace entry, which is
  **the CLI's version**: the plugin is regenerated with every release or this
  check fails.

What it checks that it does not write, each a way the skills could lie:

* **Every `adopt ...` a skill shows resolves** -- the command path exists and
  every `--flag` after it is that command's own. A skill telling an agent to run
  a flag that does not exist is the failure this whole file exists to prevent,
  and nothing else in the repository would notice it.
* **`adopt_run.py`'s output-path guard covers every `--out`.** The guard refuses
  output inside the mapped tree; a new command with an `--out` it does not name
  would write there unguarded.
* **Every declared feature names a real flag**, so preflight can never report a
  capability the CLI does not have.

`--self-test` plants one violation of each kind into a temporary copy of the
plugin and requires `--check` to name it. A gate nobody has seen fail is a gate
nobody should trust.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shlex
import shutil
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: Optional flags a skill branches on, detected by `preflight.py` on whatever CLI
#: the engagement actually has installed. `name -> (command path, flag)`. Every
#: entry must exist in the CLI this script runs against.
FEATURES: Final[dict[str, tuple[str, str]]] = {
    "ingest_unverified": ("ingest", "--unverified"),
}

GENERATED_BANNER: Final[str] = (
    "<!-- GENERATED FILE -- do not edit by hand.\n"
    "     Regenerate: cd adopt-core && uv run python scripts/gen_skills.py\n"
    "     Verify:     cd adopt-core && uv run python scripts/gen_skills.py --check -->\n"
)

_BLOCK: Final[re.Pattern[str]] = re.compile(
    r"(?P<begin><!-- BEGIN GENERATED: (?P<kind>[a-z]+) (?P<arg>[^>]+?) -->\n)(?P<body>.*?)"
    r"(?P<end><!-- END GENERATED -->)",
    re.DOTALL,
)
_FENCE: Final[re.Pattern[str]] = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE: Final[re.Pattern[str]] = re.compile(r"`([^`\n]+)`")
_MENTION: Final[re.Pattern[str]] = re.compile(r"(?:^|[\s/(\"'=])adopt(?:\.exe)?\s+(\S.*)$")
_WORD: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z-]*")


@dataclass(frozen=True)
class Param:
    opts: tuple[str, ...]
    kind: str
    required: bool
    default: str
    help: str
    is_argument: bool
    name: str


@dataclass(frozen=True)
class Command:
    path: str
    summary: str
    is_group: bool
    params: tuple[Param, ...]

    @property
    def options(self) -> frozenset[str]:
        return frozenset(o for p in self.params if not p.is_argument for o in p.opts)

    @property
    def has_argument(self) -> bool:
        return any(p.is_argument for p in self.params)


@dataclass
class Surface:
    version: str
    root_options: frozenset[str]
    commands: dict[str, Command] = field(default_factory=dict)

    @property
    def runnable(self) -> list[Command]:
        return [c for c in self.commands.values() if not c.is_group]


@dataclass(frozen=True)
class Layout:
    """Where the plugin lives. A parameter so `--self-test` can point at a copy."""

    plugin: Path
    marketplace: Path

    @property
    def skills(self) -> Path:
        return self.plugin / "skills"

    @property
    def cli_skill(self) -> Path:
        return self.skills / "adopt-cli"


DEFAULT_LAYOUT: Final[Layout] = Layout(
    plugin=REPO_ROOT / "plugins" / "adopt",
    marketplace=REPO_ROOT / ".claude-plugin" / "marketplace.json",
)


# --- reading the CLI ---------------------------------------------------------


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _default(param: Any) -> str:
    if param.required:
        return "**required**"
    value = param.default
    if value is None or value == "" or value == ():
        return "—"
    if isinstance(value, bool):
        return "`true`" if value else "`false`"
    if isinstance(value, Path):
        return f"`{value.as_posix()}`"
    return f"`{value}`"


def _params(command: Any) -> tuple[Param, ...]:
    """Discriminated on `param_type_name`, never the class: typer vendors click (CR-79)."""
    found: list[Param] = []
    for param in command.params:
        if param.name == "help":
            continue
        found.append(
            Param(
                opts=tuple(param.opts),
                kind=str(getattr(param.type, "name", "text")),
                required=bool(param.required),
                default=_default(param),
                help=_text(getattr(param, "help", "")),
                is_argument=getattr(param, "param_type_name", "option") == "argument",
                name=str(param.name),
            )
        )
    return tuple(found)


def read_surface() -> Surface:
    from typer.main import get_command

    from adopt_cli.commands import version as version_command
    from adopt_cli.main import app

    root = get_command(app)
    surface = Surface(
        version=str(version_command.build_payload()["version"]),
        root_options=frozenset(o for p in root.params for o in p.opts if p.name != "help"),
    )

    def walk(command: Any, path: list[str]) -> None:
        children = getattr(command, "commands", None) or {}
        if path:
            key = " ".join(path)
            surface.commands[key] = Command(
                path=key,
                summary=_text(getattr(command, "help", "")),
                is_group=bool(children),
                params=_params(command),
            )
        for name in sorted(children):
            walk(children[name], [*path, name])

    walk(root, [])
    return surface


# --- rendering ------------------------------------------------------------------


def _option_rows(command: Command) -> list[str]:
    rows: list[str] = []
    arguments = [p for p in command.params if p.is_argument]
    options = [p for p in command.params if not p.is_argument]
    if arguments:
        rows += ["| Argument | Type | Default | Meaning |", "|---|---|---|---|"]
        rows += [
            f"| `{p.name.upper()}` | {p.kind} | {p.default} | {_cell(p.help)} |" for p in arguments
        ]
        rows.append("")
    if options:
        rows += ["| Option | Type | Default | Meaning |", "|---|---|---|---|"]
        rows += [
            f"| {', '.join(f'`{o}`' for o in p.opts)} | {p.kind} | {p.default} | {_cell(p.help)} |"
            for p in options
        ]
        rows.append("")
    return rows


def _cell(text: str) -> str:
    return (text or "—").replace("|", "\\|")


def render_commands(surface: Surface) -> str:
    out = [
        GENERATED_BANNER,
        f"# Command reference — adopt {surface.version}",
        "",
        f"Every command of the CLI these skills were generated against: "
        f"**{len(surface.runnable)} runnable commands**. Command names, flags, JSON keys "
        "and exit codes are additive-only from `0.3.0`, so a newer CLI still has "
        "everything here. Root options go **before** the command: "
        f"{', '.join(f'`{o}`' for o in sorted(surface.root_options))}.",
        "",
        "## Index",
        "",
        "| Command | What it does |",
        "|---|---|",
    ]
    out += [f"| `adopt {c.path}` | {_cell(c.summary)} |" for c in surface.runnable]
    out.append("")
    for command in surface.runnable:
        arguments = " ".join(
            p.name.upper() if p.required else f"[{p.name.upper()}]"
            for p in command.params
            if p.is_argument
        )
        options = " [OPTIONS]" if command.options else ""
        out += [
            f"## `adopt {command.path}`",
            "",
            command.summary,
            "",
            "```shell",
            f"adopt {command.path}{' ' + arguments if arguments else ''}{options}",
            "```",
            "",
            *_option_rows(command),
        ]
    return "\n".join(out).rstrip() + "\n"


def _error_registry() -> tuple[dict[str, str], dict[str, int]]:
    from adopt_obs import errors

    categories = {str(code): str(errors.ERROR_CATEGORIES[code]) for code in errors.ErrorCode}
    exits = {str(k): int(v) for k, v in errors.CATEGORY_EXIT_CODES.items()}
    return categories, exits


def render_errors(surface: Surface) -> str:
    categories, exits = _error_registry()
    by_category: dict[str, list[str]] = defaultdict(list)
    for code, category in categories.items():
        by_category[category].append(code)
    out = [
        GENERATED_BANNER,
        f"# Exit codes and error registry — adopt {surface.version}",
        "",
        'A typed failure prints `{"error": {"code", "category", "message", '
        '"hint", "run_id"}}` to **stderr**. Branch on `code`; read `hint` first. '
        "The category fixes the exit code.",
        "",
        "| Exit | Meaning |",
        "|---|---|",
        "| `0` | Success. |",
        "| `1` | Operational failure: nothing below it is reliable evidence. |",
        "| `2` | Usage error: the invocation or its inputs were wrong. Nothing ran. |",
        "| `3` | Policy refusal: it would not, by design. Not a bug; do not route around it. |",
        "| `4` | Success with findings: it worked and found something a person must see. |",
        "",
        f"## {len(categories)} codes by category",
        "",
    ]
    for category in sorted(by_category, key=lambda c: (exits.get(c, -1), c)):
        codes = sorted(by_category[category])
        out += [f"### `{category}` → exit `{exits.get(category)}`", ""]
        out += [f"- `{code}`" for code in codes]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_surface(surface: Surface) -> str:
    payload = {
        "_generated": "scripts/gen_skills.py -- do not edit by hand",
        "cli_version": surface.version,
        "root_options": sorted(surface.root_options),
        "top_level_commands": sorted(p for p in surface.commands if " " not in p),
        "commands": {
            path: {"json": "--json" in c.options, "group": c.is_group}
            for path, c in sorted(surface.commands.items())
        },
        "features": {name: list(spec) for name, spec in sorted(FEATURES.items())},
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _render_block(kind: str, arg: str, surface: Surface) -> str:
    if kind == "options":
        command = surface.commands.get(arg.strip())
        if command is None or command.is_group:
            raise ValueError(f"no runnable command `adopt {arg.strip()}`")
        return "\n".join(_option_rows(command))
    if kind == "errors":
        categories, exits = _error_registry()
        prefixes = tuple(p.strip() for p in arg.split(",") if p.strip())
        codes = sorted(c for c in categories if c.startswith(prefixes))
        if not codes:
            raise ValueError(f"no error code starts with {', '.join(prefixes)}")
        rows = ["| Code | Category | Exit |", "|---|---|---|"]
        rows += [f"| `{c}` | {categories[c]} | `{exits.get(categories[c])}` |" for c in codes]
        return "\n".join(rows) + "\n"
    raise ValueError(f"unknown generated block kind {kind!r}")


def fill_blocks(text: str, surface: Surface, where: str, problems: list[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        try:
            body = _render_block(match["kind"], match["arg"], surface)
        except ValueError as exc:
            problems.append(f"{where}: generated block `{match['kind']} {match['arg']}`: {exc}")
            return match.group(0)
        return f"{match['begin']}{body}{match['end']}"

    return _BLOCK.sub(replace, text)


def _with_version(path: Path, version: str, plugin_name: str | None = None) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if plugin_name is None:
        data["version"] = version
    else:
        for entry in data.get("plugins", []):
            if entry.get("name") == plugin_name:
                entry["version"] = version
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# --- checking what is not written ---------------------------------------------


def _code_segments(text: str, *, markdown: bool) -> Iterator[str]:
    chunks: list[str] = []
    if markdown:
        chunks += _FENCE.findall(text)
        chunks += _INLINE.findall(_FENCE.sub("", text))
    else:
        chunks.append(text)
    for chunk in chunks:
        joined = re.sub(r"\\\n\s*", " ", chunk)
        for line in joined.splitlines():
            line = re.sub(r"(^|\s)#.*$", "", line)
            if "adopt_run.py" in line and " -- " in line:
                yield "adopt " + line.split(" -- ", 1)[1]
                continue
            yield from re.split(r"&&|\|\||;|\|", line)


def _tokens(rest: str) -> list[str]:
    try:
        return shlex.split(rest)
    except ValueError:
        return rest.split()


def check_mentions(text: str, surface: Surface, where: str, *, markdown: bool) -> list[str]:
    problems: list[str] = []
    for segment in _code_segments(text, markdown=markdown):
        match = _MENTION.search(segment)
        if match is None:
            continue
        tokens = _tokens(match.group(1))
        index = 0
        while index < len(tokens) and tokens[index].startswith("-"):
            flag = tokens[index].split("=", 1)[0]
            if flag not in surface.root_options and flag != "--help":
                problems.append(f"{where}: `adopt {flag}` is not a root option")
            index += 1
        path: list[str] = []
        while index < len(tokens) and " ".join([*path, tokens[index]]) in surface.commands:
            path.append(tokens[index])
            index += 1
        if not path:
            if index < len(tokens) and _WORD.fullmatch(tokens[index]):
                problems.append(f"{where}: `adopt {tokens[index]}` is not a command")
            continue
        command = surface.commands[" ".join(path)]
        for token in tokens[index:]:
            if "$(" in token or "`" in token:
                break
            if not token.startswith("--"):
                continue
            flag = token.split("=", 1)[0]
            if flag != "--help" and flag not in command.options:
                problems.append(f"{where}: `adopt {command.path}` has no option `{flag}`")
    return problems


def _output_table(runner: Path) -> list[tuple[str, str | None, str | None]]:
    tree = ast.parse(runner.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target: ast.expr | None = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        value = getattr(node, "value", None)
        if isinstance(target, ast.Name) and target.id == "OUTPUT_PATHS" and value is not None:
            rows: list[tuple[str, str | None, str | None]] = ast.literal_eval(value)
            return rows
    return []


def check_output_guard(layout: Layout, surface: Surface) -> list[str]:
    runner = layout.cli_skill / "scripts" / "adopt_run.py"
    rows = _output_table(runner)
    problems: list[str] = []
    if not rows:
        return [f"{runner.name}: OUTPUT_PATHS not found"]
    named = set()
    for command_path, option, _ in rows:
        command = surface.commands.get(command_path)
        if command is None:
            problems.append(f"{runner.name}: OUTPUT_PATHS names unknown `adopt {command_path}`")
            continue
        if option is None and not command.has_argument:
            problems.append(f"{runner.name}: `adopt {command_path}` takes no positional path")
        if option is not None and option not in command.options:
            problems.append(f"{runner.name}: `adopt {command_path}` has no option `{option}`")
        named.add((command_path, option))
    for command in surface.runnable:
        if "--out" in command.options and (command.path, "--out") not in named:
            problems.append(
                f"{runner.name}: `adopt {command.path} --out` writes output the in-tree guard "
                "does not know about; add it to OUTPUT_PATHS"
            )
    return problems


def check_features(surface: Surface) -> list[str]:
    problems = []
    for name, (command_path, flag) in FEATURES.items():
        command = surface.commands.get(command_path)
        if command is None or flag not in command.options:
            problems.append(f"feature {name!r}: `adopt {command_path} {flag}` does not exist")
    return problems


# --- the run --------------------------------------------------------------------


@dataclass
class Result:
    rendered: dict[Path, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def build(layout: Layout, surface: Surface) -> Result:
    result = Result()
    references = layout.cli_skill / "references"
    fully_generated = {
        references / "commands.md": render_commands(surface),
        references / "errors.md": render_errors(surface),
        layout.cli_skill / "scripts" / "surface.json": render_surface(surface),
        layout.plugin / ".claude-plugin" / "plugin.json": _with_version(
            layout.plugin / ".claude-plugin" / "plugin.json", surface.version
        ),
        layout.marketplace: _with_version(layout.marketplace, surface.version, "adopt"),
    }
    result.rendered.update(fully_generated)

    for path in sorted(layout.plugin.rglob("*")):
        if not path.is_file() or path in fully_generated:
            continue
        where = path.relative_to(layout.plugin).as_posix()
        if path.suffix == ".md":
            text = path.read_text(encoding="utf-8")
            filled = fill_blocks(text, surface, where, result.problems)
            result.rendered[path] = filled
            result.problems += check_mentions(filled, surface, where, markdown=True)
        elif path.suffix in {".yml", ".yaml"}:
            text = path.read_text(encoding="utf-8")
            result.problems += check_mentions(text, surface, where, markdown=False)

    # The contributor skills beside the plugin (`adopt-core/.claude/skills/`) are
    # not generated, but every `adopt ...` they show is held to the same rule: a
    # skill for changing the product that names a flag the product lacks misleads
    # the one reader most likely to trust it.
    contributor = layout.plugin.parent.parent / ".claude" / "skills"
    if contributor.is_dir():
        for path in sorted(contributor.rglob("*.md")):
            where = path.relative_to(contributor.parent.parent).as_posix()
            text = path.read_text(encoding="utf-8")
            result.problems += check_mentions(text, surface, where, markdown=True)

    result.problems += check_output_guard(layout, surface)
    result.problems += check_features(surface)
    return result


def stale_files(result: Result) -> list[Path]:
    return [
        path
        for path, text in result.rendered.items()
        if not path.exists() or path.read_text(encoding="utf-8") != text
    ]


def run(layout: Layout, surface: Surface, *, check: bool, quiet: bool = False) -> int:
    result = build(layout, surface)
    stale = stale_files(result)
    if not check:
        for path in stale:
            path.write_text(result.rendered[path], encoding="utf-8", newline="\n")
            if not quiet:
                print(f"wrote {path.relative_to(layout.plugin.parent.parent).as_posix()}")
        stale = []
    for path in stale:
        if not quiet:
            print(f"STALE: {path.as_posix()}", file=sys.stderr)
    for problem in result.problems:
        if not quiet:
            print(f"VIOLATION: {problem}", file=sys.stderr)
    if stale or result.problems:
        if not quiet and stale:
            print("regenerate with `uv run python scripts/gen_skills.py`", file=sys.stderr)
        return 1
    if not quiet:
        print(f"skills: up to date against adopt {surface.version}")
    return 0


def _copy(layout: Layout, into: Path) -> Layout:
    plugin = into / "plugins" / "adopt"
    shutil.copytree(layout.plugin, plugin)
    marketplace = into / ".claude-plugin" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    shutil.copy2(layout.marketplace, marketplace)
    return Layout(plugin=plugin, marketplace=marketplace)


def self_test(layout: Layout, surface: Surface) -> int:
    """Plant each violation kind in a copy and require `--check` to name it."""

    def planted(edit: Any, expect: str) -> bool:
        with tempfile.TemporaryDirectory() as scratch:
            copy = _copy(layout, Path(scratch))
            run(copy, surface, check=False, quiet=True)
            if run(copy, surface, check=True, quiet=True) != 0:
                print("self-test: the unplanted copy does not pass", file=sys.stderr)
                return False
            edit(copy)
            result = build(copy, surface)
            named = [str(p) for p in stale_files(result)] + result.problems
            caught = any(expect in item for item in named)
            print(f"self-test: {'caught' if caught else 'MISSED'} -- {expect}")
            return caught

    def append(copy: Layout, text: str) -> None:
        skill = copy.skills / "adopt-cli" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + text, encoding="utf-8")

    def stale_block(copy: Layout) -> None:
        skill = next(p for p in copy.skills.rglob("SKILL.md") if "BEGIN GENERATED" in p.read_text())
        text = skill.read_text(encoding="utf-8")
        skill.write_text(
            text.replace("<!-- END GENERATED -->", "planted\n<!-- END GENERATED -->", 1),
            encoding="utf-8",
        )

    def drop_guard(copy: Layout) -> None:
        runner = copy.cli_skill / "scripts" / "adopt_run.py"
        text = runner.read_text(encoding="utf-8")
        runner.write_text(text.replace('    ("pack", "--out", "handover"),\n', ""), "utf-8")

    checks = [
        planted(lambda c: append(c, "\n`adopt ingest --no-such-flag`\n"), "--no-such-flag"),
        planted(lambda c: append(c, "\n```shell\nadopt frobnicate --json\n```\n"), "frobnicate"),
        planted(stale_block, "SKILL.md"),
        planted(drop_guard, "adopt pack --out"),
    ]
    if not all(checks):
        print("self-test FAILED: --check did not catch every planted violation", file=sys.stderr)
        return 1
    print("self-test: --check names every planted violation")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Fail on drift; write nothing.")
    mode.add_argument("--self-test", action="store_true", help="Prove --check can fail.")
    args = parser.parse_args()
    surface = read_surface()
    if args.self_test:
        return self_test(DEFAULT_LAYOUT, surface)
    return run(DEFAULT_LAYOUT, surface, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
