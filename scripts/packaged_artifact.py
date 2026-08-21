"""`packaged-artifact`: install what we ship, then use it.

**Every test in this repository runs against an editable install, and that is
why a wheel that could not create a store passed all of them** (CR-53).
`adopt_store`, `adopt_schema.manifest` and `adopt_store.annex` located `schema/`
by walking `parents[N]` from `__file__` to the checkout root. In an editable
install that walk is correct. In an installed wheel it leaves `site-packages`
and lands on the environment root -- for the venv that found this, literally
`AppData/Local/Temp` -- which holds no `schema/` at all. The migrations glob
found nothing, reported nothing pending, and the store was created empty; the
first query then failed with `no such table: firm`.

So the gap was never in the assertions. It was in the *subject*: nothing in CI
had ever installed a built artefact and used it. This script is that subject.

**What it does not do.** It does not re-test behaviour the suite already covers.
It runs the smallest set of commands that cannot answer without opening a
packaged file -- one per data set that has to travel:

* `version`   -- constants only. Included because it is the command the old
                 binaries smoke test relied on, and the point is that it passes
                 against an artefact carrying no data at all.
* `detect`    -- `adopt_detect/rules/*.yaml`.
* `store migrate` + `store info` -- `adopt_schema/_assets/schema/`.
* `init` + `map` -- **the flagship verb, run from the artefact** (B-10). The
                 v4-line CLI imported six extractor packs while declaring one,
                 so `pip install adopt-cli` produced a `ModuleNotFoundError` on
                 `adopt map` for every project and every archetype -- and this
                 journey ran `version`, `detect`, `store migrate` and `store
                 info` past it without once invoking the command under test.
                 B-10's standing lesson was that the journey has to include the
                 verb the build exists for.

It runs them from a working directory far from the checkout, because a relative
fallback is exactly what hid the defect, and a gate that a stray parent
directory can satisfy is not a gate.

**`--self-test` plants the violation** rather than trusting the assertions:
it deletes the bundled assets from the installed environment and requires the
check to fail, and to fail naming `SCHEMA_ASSETS_MISSING` rather than reporting
a missing table four layers down.

**What this gate cannot see, stated so nobody assumes otherwise.** It installs
into a virtualenv, where `site-packages` sits five directories down. A packed
binary does not: Nuitka's `--onefile` unpacks to `/tmp/onefile_<pid>_<n>/`, three
parents from the root, and **that difference alone crashed the first binary that
ever built** -- a module-scope `parents[4]` raised `IndexError` at import
(CR-55). No arrangement of this script reaches that, because the depth comes
from where a venv lives.

Two other instruments cover it, and both are cheaper than a C build:

* `tests/unit/test_schema_assets.py` forbids deep `parents[N]` outside the one
  helper that checks the length first, and pins the onefile path shape directly.
* The layout can be simulated with no packer at all -- copy the environment's
  `site-packages` to a shallow root and put it on `PYTHONPATH`:

      cp -r <venv>/lib/python3.12/site-packages/* /pkg/
      PYTHONPATH=/pkg python -c 'from adopt_cli.main import main; main(["version","--json"])'

  That reproduces the import failure exactly, in seconds, on a machine with no
  compiler -- which is how CR-55's fix was verified.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: The console script, and the directory a venv puts it in.
_BIN_DIR: Final[str] = "Scripts" if sys.platform == "win32" else "bin"
_EXE: Final[str] = "adopt.exe" if sys.platform == "win32" else "adopt"

#: Installed rather than the whole workspace: `adopt-cli` is what an operator
#: gets, and its dependency closure is what has to be complete.
_DISTRIBUTION: Final[str] = "adopt-cli"

_ASSETS_IN_ENV: Final[str] = "adopt_schema/_assets"


@dataclass(frozen=True)
class Probe:
    """One command, and the string that proves it read its data."""

    name: str
    argv: tuple[str, ...]
    expect: str
    #: `detect` exits 2 on an indeterminate tree by design -- a proposal is never
    #: a decision -- so a non-zero exit is not evidence of a packaging failure.
    #: The payload is.
    allow_failure: bool = False


def seed_tree(work: Path) -> None:
    """The smallest repository `adopt map` can find something real in.

    Six lines of FastAPI and a dotenv template, because the probe's question is
    "does the packaged artefact contain a working `adopt map`", not "how good
    are the extractors" -- that is the reference repositories' job, in
    `map-journey`. What this needs is a tree where a *correct* map is
    non-empty, so that an empty one is unambiguous evidence rather than a
    plausible answer.
    """
    (work / "app").mkdir(parents=True, exist_ok=True)
    (work / "app" / "api.py").write_text(
        textwrap.dedent(
            """\
            from fastapi import APIRouter

            router = APIRouter(prefix="/v1")


            @router.get("/orders")
            async def list_orders(limit: int = 10) -> list[str]:
                return []
            """
        ),
        encoding="utf-8",
    )
    (work / ".env.example").write_text("DATABASE_URL=postgresql://localhost/x\n", encoding="utf-8")
    (work / "answers.json").write_text(
        json.dumps({"artifact_access": True, "deploy_signal": True, "safe_interaction": True}),
        encoding="utf-8",
    )


def probes(work: Path) -> tuple[Probe, ...]:
    store = work / "store.db"
    mapped = work / "mapped.db"
    return (
        Probe("version", ("version", "--json"), '"schema_version": 3'),
        Probe("version reports a real version", ("version", "--json"), '"version": "0.'),
        Probe("detect rules", ("detect", str(work), "--json"), '"scores"', allow_failure=True),
        Probe(
            "schema migrations",
            ("store", "migrate", "--store", str(store), "--json"),
            '"schema_version": 3',
        ),
        Probe(
            "the store is real",
            ("store", "info", "--store", str(store), "--json"),
            '"schema_version": 3',
        ),
        Probe(
            "init records a scope and an archetype",
            (
                "init",
                str(work),
                "--scope",
                "acme/demo/orders-api/prod",
                "--answers",
                str(work / "answers.json"),
                "--archetype",
                "web",
                "--store",
                str(mapped),
                "--json",
            ),
            '"archetype": "web"',
        ),
        # **The flagship verb, from the artefact.** Expecting a named extractor
        # rather than a count: a `ModuleNotFoundError` on a pack and a genuinely
        # empty repository both produce a small number, and only the extractor
        # name says the web pack was imported, scheduled and run.
        Probe(
            "the packaged artefact can map a repository",
            ("map", str(work), "--store", str(mapped), "--json"),
            '"web.endpoints"',
        ),
        # And that it *found* something. `GET /v1/orders` also proves the router
        # prefix survived packaging, which is the S1.1 defect that recorded an
        # endpoint the application does not serve.
        Probe(
            "the map is not empty",
            ("map", str(work), "--store", str(mapped), "--report", "--json"),
            "GET%20%2Fv1%2Forders",
        ),
    )


def _environment() -> dict[str, str]:
    """A clean environment: no override may stand in for packaged data."""
    env = dict(os.environ)
    for masking in ("ADOPT_SCHEMA_ASSETS_ROOT", "ADOPT_SCHEMA_MANIFEST", "ADOPT_SCHEMA_OUT_ROOT"):
        env.pop(masking, None)
    return env


def run_probes(adopt: Path, work: Path) -> list[str]:
    """The reasons the gate fails. Empty when the artefact is complete."""
    failures: list[str] = []
    for probe in probes(work):
        result = subprocess.run(
            [str(adopt), *probe.argv],
            cwd=work,
            env=_environment(),
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout + result.stderr
        if result.returncode != 0 and not probe.allow_failure:
            failures.append(f"{probe.name}: exited {result.returncode}\n{output.strip()[:800]}")
        elif probe.expect not in output:
            failures.append(f"{probe.name}: no {probe.expect!r} in output\n{output.strip()[:800]}")
        else:
            print(f"  OK -- {probe.name}")
    return failures


def _build_and_install(scratch: Path) -> Path:
    """Build every wheel, install `adopt-cli` into a fresh venv, return `adopt`."""
    dist = scratch / "dist"
    venv = scratch / "venv"

    print("building wheels ...")
    subprocess.run(
        ["uv", "build", "--all-packages", "--out-dir", str(dist)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    print("creating a clean environment ...")
    subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True, text=True)
    # `--no-cache`: uv keys its cache on name and version, and every build here
    # carries the same development version. A cached wheel from before a
    # packaging fix would make this gate report on an artefact nobody built.
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--no-cache",
            "--python",
            str(venv / _BIN_DIR / ("python.exe" if sys.platform == "win32" else "python")),
            "--find-links",
            str(dist),
            _DISTRIBUTION,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return venv / _BIN_DIR / _EXE


def _bundled_assets(venv_adopt: Path) -> Path:
    """The bundled `schema/` inside the installed environment."""
    site = venv_adopt.parent.parent
    matches = sorted(site.glob(f"**/{_ASSETS_IN_ENV}"))
    if not matches:
        raise SystemExit(
            f"no {_ASSETS_IN_ENV} in the installed environment. Either the wheel "
            "shipped without it -- which is the defect this gate exists for -- or "
            "the layout moved and this helper needs updating."
        )
    return matches[0]


def _self_test(scratch: Path) -> int:
    """Prove the gate fails, and fails by name.

    *Fails when* the check passes against an artefact with no schema assets.
    *Matters because* that artefact is what shipped, and it passed a green suite
    and a `version --json` smoke test on the way out. *No other instrument
    catches it because* the check's own subject is a build, so only a planted
    build exercises the failing branch.
    """
    adopt = _build_and_install(scratch)
    work = scratch / "work"
    work.mkdir()
    seed_tree(work)

    print("\nplanting: removing the bundled schema assets from the environment")
    assets = _bundled_assets(adopt)
    shutil.rmtree(assets)

    failures = run_probes(adopt, work)
    if not failures:
        print("SELF-TEST FAILED: the gate passed against an artefact carrying no schema assets.")
        return 1
    print(f"  OK -- the gate fails when the assets are removed ({len(failures)} probe(s))")

    # Naming the cause is half the fix. The whole reason this defect cost a
    # release dry run is that it surfaced as `no such table: firm`.
    reported = "\n".join(failures)
    if "SCHEMA_ASSETS_MISSING" not in reported:
        print("SELF-TEST FAILED: the failure does not name SCHEMA_ASSETS_MISSING.")
        print("Missing assets must be reported as missing assets, not as a missing table.")
        print(reported[:1500])
        return 1
    print("  OK -- it names SCHEMA_ASSETS_MISSING rather than a missing table")

    print("\nself-test OK: the gate detects a stripped artefact and says why")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Build, install and use the artefact.")
    parser.add_argument(
        "--self-test", action="store_true", help="Prove the gate still fails when it should."
    )
    parser.add_argument("--json", action="store_true", help="Emit the verdict as one JSON object.")
    arguments = parser.parse_args(argv)

    if not arguments.check and not arguments.self_test:
        parser.error("give --check or --self-test")

    with tempfile.TemporaryDirectory() as raw:
        scratch = Path(raw)
        if arguments.self_test:
            return _self_test(scratch)

        adopt = _build_and_install(scratch)
        work = scratch / "work"
        work.mkdir()
        seed_tree(work)
        print(f"\nusing the installed artefact from {work}\n")
        failures = run_probes(adopt, work)

    if arguments.json:
        print(json.dumps({"ok": not failures, "failures": failures}, indent=2, sort_keys=True))
    if failures:
        for failure in failures:
            print(f"::error::packaged-artifact: {failure}")
        return 1
    print("\npackaged-artifact: OK -- the installed artefact carries everything it needs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
