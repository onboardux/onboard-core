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
* One probe per **verb family** added by Builds 2-9 -- `ingest`, `gaps`, `ask`,
                 `pack`, `probe manifest validate`, `probe add`, `refresh` and
                 `handover` -- because until then this gate proved the installed
                 artefact for **Build 0 and Build 1 only** (N5). Five
                 distributions shipped in `0.4.0` whose verbs no installed
                 artefact had ever run: `ModuleNotFoundError` on a lazily
                 registered command is exactly what B-10 cost a release, and
                 every one of those verbs registers lazily.

                 Each probe expects a key **only that verb's package produces**,
                 and never a count. A count is satisfied by an empty answer; a
                 key is not.

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
    # One document for `ingest` and one probe manifest for `probe add`. Both are
    # the smallest thing their verb accepts: the question is whether the
    # packaged artefact can run the verb at all, not how well it runs it.
    (work / "README.md").write_text(
        textwrap.dedent(
            """\
            ---
            audience: technical
            kind: procedure
            ---
            # Orders

            The orders API serves refunds, and a refund needs a human decision.
            """
        ),
        encoding="utf-8",
    )
    # `127.0.0.1:9` is the discard port. The probe is **added, never run**:
    # nothing here may open a socket, and `add` is the verb that proves
    # `adopt-probe` is installed and its manifest validator is reachable.
    (work / "probe.yaml").write_text(
        textwrap.dedent(
            """\
            probe_id: packaged-artifact-smoke
            safe_path: sandbox
            network: { deny_by_default: true, allow: ["127.0.0.1:9"] }
            http_methods: { allow: [GET] }
            side_effect_policy: prohibited
            runtime: { max_seconds: 5, max_memory_mb: 64, max_requests: 1 }
            cost: { max_model_calls: 0, max_tokens: 100 }
            output: { retain_raw: false, redaction_policy: pii-default }
            cleanup: { required: true }
            diff_method: exact
            steps:
              - kind: http
                method: GET
                url: "http://127.0.0.1:9/health"
            """
        ),
        encoding="utf-8",
    )


def probes(work: Path) -> tuple[Probe, ...]:
    store = work / "store.db"
    mapped = work / "mapped.db"
    # Derived, never written down. This was `"schema_version": 3` in three
    # places and every one of them failed the first time a build added a table
    # -- a gate reporting a defect that was not one, which is the fastest way to
    # teach people to ignore it. `adopt_const` is importable here because the
    # checkout's own environment runs this script; what the *artefact* reports
    # is what the probes compare against.
    from adopt_const import SCHEMA_VERSION

    expected_schema = f'"schema_version": {SCHEMA_VERSION}'
    return (
        Probe("version", ("version", "--json"), expected_schema),
        Probe("version reports a real version", ("version", "--json"), '"version": "0.'),
        Probe("detect rules", ("detect", str(work), "--json"), '"scores"', allow_failure=True),
        Probe(
            "schema migrations",
            ("store", "migrate", "--store", str(store), "--json"),
            expected_schema,
        ),
        Probe(
            "the store is real",
            ("store", "info", "--store", str(store), "--json"),
            expected_schema,
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
        # -- Builds 2-9, one probe per verb family (N5) ----------------------
        #
        # Every one of these commands registers its package lazily inside the
        # command body, so an undeclared or unpacked distribution surfaces as a
        # `ModuleNotFoundError` the moment the verb runs and never before. That
        # is precisely what B-10 cost a release, and until this gate ran them
        # the five distributions `0.4.0` adds had no installed-artefact evidence
        # at all.
        #
        # These are not journeys. `*-journey` jobs prove the behaviour on real
        # repositories; what is proved here is that the *artefact* contains the
        # code and the data each verb needs.
        Probe(
            "adopt-knowledge is packaged: ingest writes a document",
            ("ingest", str(work / "README.md"), "--store", str(mapped), "--json"),
            '"ingested"',
        ),
        Probe(
            "adopt-coverage is packaged: gaps reports the join",
            ("gaps", "--store", str(mapped), "--json"),
            '"uncovered"',
            # `gaps` exits 4 when it has findings, which a freshly mapped store
            # reliably does. Degraded-with-findings is the command working.
            allow_failure=True,
        ),
        Probe(
            "adopt-ask is packaged: the store answers",
            ("ask", "what is the refund policy", "--store", str(mapped), "--json"),
            '"branch"',
            # UNKNOWN is a valid answer and exits 0; the branch key is what says
            # the retrieval path ran rather than that it found something.
            allow_failure=True,
        ),
        Probe(
            "adopt-handover is packaged: a pack is assembled",
            ("pack", "--out", str(work / "pack"), "--store", str(mapped), "--json"),
            '"sections"',
        ),
        Probe(
            "adopt-probe is packaged: a manifest validates",
            ("probe", "manifest", "validate", str(work / "probe.yaml"), "--json"),
            '"declared_hosts"',
        ),
        Probe(
            "adopt-probe is packaged: a probe is stored",
            ("probe", "add", str(work / "probe.yaml"), "--store", str(mapped), "--json"),
            '"revision"',
        ),
        # `--no-probes` on purpose: the artefact half only. This gate opens no
        # socket, and a refresh that reached one would make the release job
        # depend on the runner's network.
        Probe(
            "refresh runs the artifact half",
            ("refresh", "--no-probes", "--store", str(mapped), "--json"),
            '"counts_by_class"',
            # Exit 4 means it found something actionable, which is the command
            # doing its job.
            allow_failure=True,
        ),
        Probe(
            "adopt-handover is packaged: a handover opens",
            (
                "handover",
                "start",
                "--receiving-owner",
                "the-client",
                "--store",
                str(mapped),
                "--json",
            ),
            '"handover_id"',
        ),
        Probe(
            "the handover is readable afterwards",
            ("handover", "status", "--store", str(mapped), "--json"),
            '"receiving_owner"',
        ),
    )


def _environment() -> dict[str, str]:
    """A clean environment: no override may stand in for packaged data."""
    env = dict(os.environ)
    for masking in ("ADOPT_SCHEMA_ASSETS_ROOT", "ADOPT_SCHEMA_MANIFEST", "ADOPT_SCHEMA_OUT_ROOT"):
        env.pop(masking, None)
    return env


#: How much of a failing probe's output is reported. Bounded so one broken probe
#: cannot bury the other sixteen.
_REPORTED_OUTPUT_BYTES: Final[int] = 900


def _tail(output: str) -> str:
    """The **end** of a failing probe's output, not the beginning.

    A Python traceback names its cause on the last line. Truncating from the
    front kept the `File "<frozen runpy>"` frames and dropped
    `ModuleNotFoundError: No module named 'adopt_knowledge'` -- so the gate
    detected a missing distribution correctly and reported a stack of import
    machinery, which for anyone reading the log is the same as not detecting it.
    Found by the second `--self-test` plant, which is what a self-test is for.
    """
    text = output.strip()
    if len(text) <= _REPORTED_OUTPUT_BYTES:
        return text
    return "... " + text[-_REPORTED_OUTPUT_BYTES:]


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
            failures.append(f"{probe.name}: exited {result.returncode}\n{_tail(output)}")
        elif probe.expect not in output:
            failures.append(f"{probe.name}: no {probe.expect!r} in output\n{_tail(output)}")
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


#: The package `--self-test` removes to prove the verb probes are not decorative.
#: `adopt_knowledge` is one of the five distributions `0.4.0` adds, it is
#: imported **inside** `adopt ingest`'s body like every Build 2-9 verb, and no
#: installed artefact had ever run one of them before T1.14 (N5). Removing it
#: reproduces B-10's defect exactly: the artefact installs, `version --json`
#: passes, `detect` passes, `store migrate` passes, and the verb the build exists
#: for raises `ModuleNotFoundError`.
_PLANTED_MISSING_PACKAGE: Final[str] = "adopt_knowledge"


def _installed_package(venv_adopt: Path, package: str) -> Path:
    """The installed package directory inside the environment."""
    site = venv_adopt.parent.parent
    matches = sorted(path for path in site.glob(f"**/{package}") if (path / "__init__.py").exists())
    if not matches:
        raise SystemExit(
            f"no installed {package}/ in the environment. Either the wheel shipped "
            "without it -- which is the defect this plant exists for -- or the "
            "layout moved and this helper needs updating."
        )
    return matches[0]


def _self_test(scratch: Path) -> int:
    """Prove the gate fails, and fails by name -- twice, for two different causes.

    *Fails when* the check passes against an artefact with no schema assets, or
    against one missing a distribution whose verb it claims to prove. *Matters
    because* both artefacts are the kind that ship: the first passed a green
    suite and a `version --json` smoke test on the way out (CR-53), and the
    second is B-10's defect, where `pip install adopt-cli` produced a
    `ModuleNotFoundError` on the flagship verb for every project. *No other
    instrument catches either because* the check's own subject is a build, so
    only a planted build exercises the failing branch.

    Two plants, in two environments, because they are two claims. The assets
    plant proves the gate sees missing **data**; the package plant proves the
    verb probes see a missing **distribution** -- and a probe list that had
    grown stale would pass the first and fail nothing.
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

    # -- The second plant, in its own environment ---------------------------
    second = scratch / "missing-package"
    second.mkdir()
    adopt = _build_and_install(second)
    work = second / "work"
    work.mkdir()
    seed_tree(work)

    print(f"\nplanting: removing {_PLANTED_MISSING_PACKAGE}/ from the environment")
    shutil.rmtree(_installed_package(adopt, _PLANTED_MISSING_PACKAGE))

    failures = run_probes(adopt, work)
    if not failures:
        print(
            "SELF-TEST FAILED: the gate passed against an artefact missing "
            f"{_PLANTED_MISSING_PACKAGE}. The verb probes are not reaching the "
            "packages they claim to prove."
        )
        return 1
    reported = "\n".join(failures)
    if _PLANTED_MISSING_PACKAGE not in reported:
        print(
            f"SELF-TEST FAILED: the failure does not name {_PLANTED_MISSING_PACKAGE}. "
            "A missing distribution must be reported as one."
        )
        print(reported[:1500])
        return 1
    print(f"  OK -- the gate fails naming {_PLANTED_MISSING_PACKAGE} when it is removed")

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
