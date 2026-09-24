"""Preflight for the adopt skills: is the CLI here, new enough, and whole?

Read-only. It runs `adopt version`, `adopt --help`, `adopt doctor` and one
`--help` per optional feature, and asks git two questions. It installs nothing
and writes nothing -- installing is the person's decision, not this script's.

    python preflight.py            # human-readable
    python preflight.py --json     # one JSON object on stdout
    python preflight.py --adopt /path/to/adopt-linux-x86_64

Exit 0 when ready, 1 when not; `problems` names every reason.

Written for Python 3.8+ on purpose: its first job is to tell someone their
Python is too old for `pip install adopt-cli`, which it cannot do if it needs
3.12 itself. `surface.json` beside it is generated from the CLI these skills
were written against; never edit it by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

#: The floor. `0.4.0` crashes on nine `--help` pages and its captured answers
#: never reach a pack; nothing older has the free builds at all.
MIN_VERSION = (0, 4, 1)
#: What `pip install adopt-cli` needs. The standalone binary needs no Python.
PIP_PYTHON = (3, 12)

#: A top-level command row in `adopt --help`: exactly one space after the box
#: border. Wrapped description lines are indented further and never match.
_COMMAND_ROW = re.compile(r"^[|│] ([a-z][a-z0-9-]*) ")

_SURFACE = Path(__file__).resolve().with_name("surface.json")


def _run(argv: list[str], timeout: int = 60) -> tuple[int, str, str]:
    env = dict(os.environ, PYTHONUTF8="1", NO_COLOR="1", COLUMNS="160")
    try:
        done = subprocess.run(argv, capture_output=True, timeout=timeout, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)
    return (
        done.returncode,
        done.stdout.decode("utf-8", errors="replace"),
        done.stderr.decode("utf-8", errors="replace"),
    )


def _version_tuple(text: str) -> tuple[int, ...]:
    """`0.4.1`, `0.4.2.dev3`, `0.5.0rc1` -> the leading numeric release."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(part) for part in match.groups()) if match else ()


def _load_surface() -> dict[str, Any]:
    try:
        loaded: dict[str, Any] = json.loads(_SURFACE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded


def check(adopt: str | None) -> dict[str, Any]:
    report: dict[str, Any] = {"ready": False, "problems": [], "warnings": []}
    problems: list[str] = report["problems"]
    warnings: list[str] = report["warnings"]
    surface = _load_surface()
    report["skills_generated_against"] = surface.get("cli_version")

    report["python"] = {
        "version": ".".join(str(part) for part in sys.version_info[:3]),
        "can_pip_install_adopt": sys.version_info[:2] >= PIP_PYTHON,
    }

    executable = adopt or shutil.which("adopt")
    report["adopt"] = {"path": executable}
    if not executable:
        problems.append(
            "adopt is not installed or not on PATH. Ask before installing: "
            '`uv tool install "adopt-cli>=0.4.1"` (or pipx), or the signed standalone '
            "binary -- see references/install.md."
        )
        return report

    code, out, err = _run([executable, "version", "--json"])
    if code != 0:
        problems.append(f"`adopt version --json` exited {code}: {err.strip()[:300]}")
        return report
    try:
        version = json.loads(out)
    except ValueError:
        problems.append("`adopt version --json` did not print JSON")
        return report
    report["adopt"].update(version)
    installed = _version_tuple(str(version.get("version", "")))
    report["adopt"]["meets_floor"] = bool(installed) and installed >= MIN_VERSION
    if not report["adopt"]["meets_floor"]:
        floor = ".".join(str(part) for part in MIN_VERSION)
        problems.append(
            f"adopt {version.get('version')} is below {floor}; upgrade with the tool that "
            "installed it."
        )
    if version.get("build_id") is None:
        warnings.append(
            "build_id is null: normal for a source checkout, unexpected for anything "
            "installed from PyPI or run as a release binary."
        )

    code, out, _ = _run([executable, "--help"])
    found = {m.group(1) for m in map(_COMMAND_ROW.match, out.splitlines()) if m}
    expected = set(surface.get("top_level_commands", []))
    missing = sorted(expected - found)
    report["surface"] = {"missing": missing, "extra": sorted(found - expected)}
    if code != 0 or not found:
        warnings.append("could not read the command list from `adopt --help`")
    elif missing:
        problems.append(
            f"these commands the skills use are missing: {missing} -- the CLI is older than "
            "the skills; upgrade it."
        )

    features: dict[str, bool] = {}
    for name, (command, flag) in sorted(surface.get("features", {}).items()):
        code, out, _ = _run([executable, *command.split(), "--help"])
        features[name] = code == 0 and flag in out
    report["features"] = features

    code, out, _ = _run([executable, "doctor", "--json"])
    remote: dict[str, Any] = {"configured": False}
    if code in (0, 4):
        try:
            doctor = json.loads(out)
        except ValueError:
            doctor = {}
        sources = {row.get("key"): row.get("source") for row in doctor.get("config", [])}
        remote["configured"] = sources.get("ADOPT_PLANE_URL", "default") != "default"
        report["config_findings"] = len(doctor.get("findings", []))
        report["project_config"] = doctor.get("environment", {}).get("project_config")
    report["remote"] = remote

    store = Path(os.environ.get("ADOPT_STORE_PATH", ".adopt/store.db"))
    report["store"] = {"path": str(store), "exists": store.exists()}
    if store.exists() and Path(str(store) + ".replica.json").exists():
        report["store"]["replica"] = True

    code, out, _ = _run(["git", "rev-parse", "--show-toplevel"], timeout=20)
    in_tree = code == 0
    report["git"] = {"work_tree": out.strip() if in_tree else None}
    if in_tree:
        ignored, _, _ = _run(["git", "check-ignore", "-q", ".adopt/store.db"], timeout=20)
        report["git"]["store_ignored"] = ignored == 0
        if ignored != 0:
            warnings.append(
                ".adopt/ is not ignored by git. Without touching the client's tracked files: "
                "`echo .adopt/ >> .git/info/exclude`."
            )

    report["ready"] = not problems
    return report


def _print_human(report: dict[str, Any]) -> None:
    adopt = report.get("adopt", {})
    print(f"ready: {report['ready']}")
    print(f"python {report['python']['version']}", end="")
    print("" if report["python"]["can_pip_install_adopt"] else " (too old for pip install)")
    if adopt.get("path"):
        print(f"adopt {adopt.get('version', '?')} at {adopt['path']}")
        print(f"build_id: {adopt.get('build_id')}")
    for name, present in sorted(report.get("features", {}).items()):
        print(f"feature {name}: {'yes' if present else 'no'}")
    if "store" in report:
        print(
            f"store {report['store']['path']}: {'present' if report['store']['exists'] else 'absent'}"
        )
    if report.get("remote", {}).get("configured"):
        print("remote mode: a control plane is configured")
    for problem in report["problems"]:
        print(f"PROBLEM: {problem}")
    for warning in report["warnings"]:
        print(f"warning: {warning}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="Print one JSON object.")
    parser.add_argument("--adopt", help="Path to the adopt executable, if not `adopt` on PATH.")
    args = parser.parse_args()
    report = check(args.adopt)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
