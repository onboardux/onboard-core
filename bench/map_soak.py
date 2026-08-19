"""`01` N1, N2, N3 and N11 measured on real repositories -- the G3 soak.

    uv run python -m bench.map_soak --clone --assert
    uv run python -m bench.map_soak --self-test

**This harness asserts wherever it runs, and the seven Build 0 harnesses beside it
do not.** That difference is deliberate and is the first thing to understand here.
Build 0's benchmarks measure *this repository's own primitives* -- a store open, a
URI build, a coverage recompute -- so their numbers are a property of the machine
and `bench/RUNNER.md` rule 1 correctly refuses to fail a build on an anecdote from
a laptop. `01` N1-N3 and N11 are a different kind of claim: they are about what a
**cold FDE experiences on a client repository on their own machine**, which is the
whole of G3. A budget of that shape asserted only on a pinned 2-vCPU runner would
be measuring the one machine no user has, and it could never be measured at all --
no reference runner clones a client's 200k-LoC tree. So the soak asserts here, and
**every archived report names the machine it ran on** so that a number is never
read as if it came from somewhere else.

**The corpus is `perf/soak/corpus.json`, and it is a decision rather than a
constant.** PRD **Q4** -- *"which three real repos form the G3 soak corpus?"* --
was due at S1.4 start and never ruled, so this build proceeds on the PRD's own
stated fallback under `docs/pack/OPEN-DECISIONS.md` **OD-18**. The manifest is
tracked, so the corpus is an artefact somebody can disagree with rather than a
shell command in one contributor's history.

**What is measured, and why each is measured the way it is.**

- **N1 stage-1** and **N2 total** come from `run_report.json`'s own `timings`,
  not from wall-clock around the subprocess. Wall clock includes interpreter
  start and store seeding, neither of which is what the budget is about, and
  `CLI_COLD_START_MS` is Build 0's separate constant for exactly the part this
  would otherwise smuggle in.
- **N3** is the unchanged re-run, and it is **two assertions, not one**: inside
  `MAP_INCREMENTAL_BUDGET_S` *and* `revisions_written` all zero. A fast re-run
  that wrote revisions has failed N3 in the way that matters -- `03` section 10
  calls a non-zero count on an unchanged re-run *"stop the line"*, because every
  downstream delta becomes noise.
- **N11** is `peak_rss_bytes` from the map process itself, against
  `MAP_MAX_RSS_BYTES`. Until S1.8 that field was `None` on Windows and the
  constant did not exist, so this assertion is new in both halves (B1-CR-95).
- **Store growth** is recorded and **not asserted**. No document states a bound
  for it, and inventing one here would be a magic number with better formatting.

**Percentiles, honestly.** `01` N1 reads *"p50 <= budget, p90 <= 1.33x"*. Over a
three-repository corpus a p90 is arithmetic on three points and means very little,
so this harness asserts the **strictest** available form -- every repository inside
the budget -- and says so. If every sample is inside the budget then p50 and p90
are too, and no percentile constant has to be invented to reach that conclusion.

**`--self-test` drives synthetic results through the same verdict function.** A
soak that has never been watched failing is a soak nobody should quote, and the
failure mode here is specific: a harness that reports "no breaches" when it never
compared anything is indistinguishable from one that passed.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, cast

from adopt_const import (
    MAP_INCREMENTAL_BUDGET_S,
    MAP_MAX_RSS_BYTES,
    MAP_MAX_WORKERS_CEILING,
    MAP_STAGE1_BUDGET_S,
    MAP_TOTAL_BUDGET_S,
)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adopt_model._enums import Archetype  # noqa: E402
from tests.build1_conftest import build_scoped_store  # noqa: E402

#: The tracked corpus manifest (OD-18).
CORPUS_MANIFEST: Final[Path] = REPO_ROOT / "perf" / "soak" / "corpus.json"

#: Where clones land. Outside the repository on purpose: a 200k-LoC checkout is
#: not an artefact of this build, and a soak that pollutes the working tree is a
#: soak somebody eventually runs with `--assert` against their own uncommitted
#: edits.
DEFAULT_CORPUS_DIR: Final[Path] = REPO_ROOT.parent / ".soak-corpus"

#: `05` S1.8's two stated corpus constraints.
# const-sync: ok -- a corpus shape stated in the sprint plan, not a runtime tunable.
MIN_ARCHETYPES: Final[int] = 3
# const-sync: ok -- the sprint plan's ">200k LoC" corpus constraint, not a runtime tunable.
LARGE_REPO_MIN_LOC: Final[int] = 200_000

#: Bytes per MiB and per KiB, for display only.
# const-sync: ok -- a unit, not a decision anybody may revise.
BYTES_PER_MIB: Final[int] = 1_048_576
# const-sync: ok -- a unit, not a decision anybody may revise.
BYTES_PER_KIB: Final[int] = 1024


@dataclass(frozen=True)
class Budget:
    """One NFR, its number, and where the number lives."""

    nfr: str
    what: str
    limit: float
    unit: str
    constant: str


BUDGETS: Final[tuple[Budget, ...]] = (
    Budget("N1", "stage1_seconds", float(MAP_STAGE1_BUDGET_S), "s", "MAP_STAGE1_BUDGET_S"),
    Budget("N2", "total_seconds", float(MAP_TOTAL_BUDGET_S), "s", "MAP_TOTAL_BUDGET_S"),
    Budget(
        "N3",
        "rerun_total_seconds",
        float(MAP_INCREMENTAL_BUDGET_S),
        "s",
        "MAP_INCREMENTAL_BUDGET_S",
    ),
    Budget("N11", "peak_rss_bytes", float(MAP_MAX_RSS_BYTES), "bytes", "MAP_MAX_RSS_BYTES"),
)


@dataclass
class RepoResult:
    """One repository, mapped twice."""

    name: str
    archetype: str
    licence: str
    loc: int
    files_indexed: int
    facts: int
    exit_code: int
    stage1_seconds: float
    total_seconds: float
    rerun_stage1_seconds: float
    rerun_total_seconds: float
    peak_rss_bytes: int | None
    rerun_revisions_written: dict[str, int]
    store_bytes_after_first: int
    store_bytes_after_rerun: int
    degradations: int
    network_attempted: int
    client_imports_attempted: int
    error: str | None = None


@dataclass
class Breach:
    repo: str
    nfr: str
    what: str
    measured: str
    limit: str


@dataclass
class SoakReport:
    generated_at: str
    machine: dict[str, Any]
    corpus: dict[str, Any]
    repos: list[RepoResult] = field(default_factory=list)
    breaches: list[Breach] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def machine_facts() -> dict[str, Any]:
    """What this ran on. Every archived report carries it (`bench/RUNNER.md` rule 3)."""
    return {
        "platform": sys.platform,
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "workers": min(MAP_MAX_WORKERS_CEILING, os.cpu_count() or 1),
        "reference_runner": False,
        "reading": (
            "a developer machine, not `bench/RUNNER.md`'s pinned runner. `01` N1-N3 "
            "and N11 are claims about a client repository on an engineer's own "
            "machine, which is what G3 measures; they are not runner-pinned "
            "micro-benchmarks like Build 0's seven."
        ),
    }


#: Extensions the LoC count reads. Crude on purpose -- see `count_loc`.
TEXT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".sql",
        ".yml",
        ".yaml",
        ".json",
        ".html",
        ".md",
        ".toml",
        ".cfg",
        ".ini",
        ".graphql",
    }
)


def count_loc(root: Path) -> int:
    """Lines across text files, for the >200k constraint only.

    Deliberately crude and deliberately not a product measurement: it exists to
    answer `05` S1.8's *"at least one >200k LoC"* and nothing else reads it.
    """
    total = 0
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            with path.open("rb") as handle:
                total += sum(1 for _ in handle)
        except OSError:
            continue
    return total


def clone(spec: dict[str, Any], into: Path) -> Path:
    """Shallow-clone one corpus repository, or reuse an existing checkout."""
    target = into / str(spec["name"])
    if target.exists():
        return target
    into.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            str(spec["ref"]),
            str(spec["url"]),
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target


def _run_map(tree: Path, store_root: Path, out: Path, scope: Any, archetype: str) -> dict[str, Any]:
    """One `adopt map`, run as an operator runs it, returning its run report.

    A **subprocess** rather than an in-process call, for one reason that matters:
    `peak_rss_bytes` is the map process's own peak, and measuring it from inside a
    harness that has already imported the world would report the harness.
    """
    env = dict(os.environ)
    env["ADOPT_STORE_PATH"] = str(store_root / "store.db")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "adopt_cli.main",
            "map",
            str(tree),
            "--firm",
            scope.firm_id,
            "--engagement",
            scope.engagement_id,
            "--system",
            scope.system_id,
            "--environment",
            scope.environment_id,
            "--archetype",
            archetype,
            "--out",
            str(out),
            "--json",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    report_path = out / "run_report.json"
    if not report_path.exists():
        raise RuntimeError(
            f"no run_report.json after `adopt map` (exit {completed.returncode}): "
            f"{completed.stderr[-800:]}"
        )
    report: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    report["_exit_code"] = completed.returncode
    return report


def soak_one(spec: dict[str, Any], tree: Path, workdir: Path) -> RepoResult:
    """Map one repository twice and record what both runs did."""
    name = str(spec["name"])
    archetype = str(spec["archetype"])
    root = workdir / name
    if root.exists():
        shutil.rmtree(root)
    # `archetype` arrives from the tracked manifest as text and the seam takes the
    # closed `Archetype` literal. Cast rather than widen the seam: an archetype the
    # vocabulary does not contain is a corpus defect, and it surfaces as the run's
    # own `MAP_NO_ARCHETYPE` refusal rather than as a soak that quietly maps nothing.
    handle, scopes = build_scoped_store(root, archetype=cast(Archetype, archetype))
    scope = scopes["prod"]
    handle.close()

    store = root / "store.db"
    first = _run_map(tree, root, root / "out", scope, archetype)
    size_first = store.stat().st_size
    second = _run_map(tree, root, root / "out2", scope, archetype)
    size_second = store.stat().st_size

    return RepoResult(
        name=name,
        archetype=archetype,
        licence=str(spec["licence"]),
        loc=count_loc(tree),
        files_indexed=int(first["tree"]["files_indexed"]),
        facts=sum(int(v) for v in first["counts_by_kind"].values()),
        exit_code=int(first["_exit_code"]),
        stage1_seconds=float(first["timings"]["stage1_elapsed_s"]),
        total_seconds=float(first["timings"]["total_elapsed_s"]),
        rerun_stage1_seconds=float(second["timings"]["stage1_elapsed_s"]),
        rerun_total_seconds=float(second["timings"]["total_elapsed_s"]),
        peak_rss_bytes=first.get("peak_rss_bytes"),
        rerun_revisions_written={k: int(v) for k, v in second["revisions_written"].items()},
        store_bytes_after_first=size_first,
        store_bytes_after_rerun=size_second,
        degradations=len(first.get("degradations", [])),
        network_attempted=int(first.get("network_attempted", 0)),
        client_imports_attempted=int(first.get("client_imports_attempted", 0)),
    )


def verdict(results: Sequence[RepoResult]) -> list[Breach]:
    """Every breach, never only the first.

    Stopping at the first breach invites a partial answer -- one repository fixed,
    two still over, and a table recording the one. Build 0's `bench.all` made the
    same argument for the same reason.
    """
    breaches: list[Breach] = []
    for result in results:
        if result.error is not None:
            breaches.append(Breach(result.name, "--", "run", result.error, "a completed run"))
            continue
        for budget in BUDGETS:
            measured = getattr(result, budget.what)
            if measured is None:
                breaches.append(
                    Breach(
                        result.name,
                        budget.nfr,
                        budget.what,
                        "None -- the platform did not answer",
                        f"{budget.limit:g} {budget.unit} ({budget.constant})",
                    )
                )
                continue
            if float(measured) > budget.limit:
                breaches.append(
                    Breach(
                        result.name,
                        budget.nfr,
                        budget.what,
                        f"{float(measured):g} {budget.unit}",
                        f"{budget.limit:g} {budget.unit} ({budget.constant})",
                    )
                )
        written = sum(result.rerun_revisions_written.values())
        if written:
            breaches.append(
                Breach(
                    result.name,
                    "N3",
                    "rerun_revisions_written",
                    f"{written} ({result.rerun_revisions_written})",
                    "0 -- an unchanged re-run writes no revision",
                )
            )
    return breaches


def corpus_notes(results: Sequence[RepoResult]) -> list[str]:
    """`05` S1.8's two corpus constraints, checked rather than assumed."""
    notes: list[str] = []
    archetypes = {r.archetype for r in results}
    if len(archetypes) < MIN_ARCHETYPES:
        notes.append(
            f"CONSTRAINT UNMET: {len(archetypes)} archetype(s) {sorted(archetypes)}; "
            f"`05` S1.8 requires at least {MIN_ARCHETYPES}"
        )
    else:
        notes.append(f"archetypes covered: {sorted(archetypes)}")
    largest = max((r.loc for r in results), default=0)
    if largest < LARGE_REPO_MIN_LOC:
        notes.append(
            f"CONSTRAINT UNMET: largest repository is {largest} LoC; "
            f"`05` S1.8 requires at least one over {LARGE_REPO_MIN_LOC}"
        )
    else:
        notes.append(f"largest repository: {largest} LoC")
    notes.append(
        "corpus is PRD Q4's stated fallback, taken flagged under OD-18. `01` section 11's "
        "claims discipline applies: none of these numbers may be quoted outside "
        "engineering until the founder's corpus is ruled and re-measured."
    )
    return notes


def render(report: SoakReport) -> str:
    lines = [
        "G3 soak -- `01` N1, N2, N3, N11 on the reference corpus",
        f"machine   {report.machine['platform']} / {report.machine['cpu_count']} cpu / "
        f"{report.machine['workers']} workers -- NOT the reference runner",
        "",
    ]
    for result in report.repos:
        if result.error:
            lines.append(f"{result.name:22} FAILED -- {result.error}")
            continue
        rss = (
            "None"
            if result.peak_rss_bytes is None
            else f"{result.peak_rss_bytes / BYTES_PER_MIB:.0f} MiB"
        )
        lines.append(
            f"{result.name:22} {result.archetype:8} {result.loc:>8} loc  "
            f"{result.files_indexed:>6} files  {result.facts:>6} facts  exit {result.exit_code}"
        )
        lines.append(
            f"{'':22} stage1 {result.stage1_seconds:7.2f}s / {MAP_STAGE1_BUDGET_S}s   "
            f"total {result.total_seconds:8.2f}s / {MAP_TOTAL_BUDGET_S}s   rss {rss} / "
            f"{MAP_MAX_RSS_BYTES / BYTES_PER_MIB:.0f} MiB"
        )
        lines.append(
            f"{'':22} re-run {result.rerun_total_seconds:7.2f}s / "
            f"{MAP_INCREMENTAL_BUDGET_S}s   "
            f"revisions {result.rerun_revisions_written}   "
            f"store {result.store_bytes_after_first / BYTES_PER_KIB:.0f} KiB -> "
            f"{result.store_bytes_after_rerun / BYTES_PER_KIB:.0f} KiB"
        )
    lines.append("")
    for note in report.notes:
        lines.append(f"note      {note}")
    if report.breaches:
        lines.append("")
        for breach in report.breaches:
            lines.append(
                f"BREACH    {breach.repo} {breach.nfr} {breach.what}: "
                f"{breach.measured} exceeds {breach.limit}"
            )
    else:
        lines.append("")
        lines.append("no breaches -- every repository inside every budget")
    return "\n".join(lines)


def archive(report: SoakReport, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "soak_version": 1,
        "generated_at": report.generated_at,
        "machine": report.machine,
        "corpus": report.corpus,
        "budgets": {
            b.nfr: {"what": b.what, "limit": b.limit, "constant": b.constant} for b in BUDGETS
        },
        "repos": [asdict(r) for r in report.repos],
        "breaches": [asdict(b) for b in report.breaches],
        "notes": report.notes,
    }
    target = directory / "soak.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (directory / "soak.txt").write_text(render(report) + "\n", encoding="utf-8")
    return target


def _synthetic(**overrides: Any) -> RepoResult:
    base: dict[str, Any] = {
        "name": "synthetic",
        "archetype": "web",
        "licence": "MIT",
        # const-sync: ok -- a synthetic self-test fixture value, not a tunable.
        "loc": 250_000,
        "files_indexed": 100,
        "facts": 100,
        "exit_code": 0,
        "stage1_seconds": 1.0,
        "total_seconds": 2.0,
        "rerun_stage1_seconds": 1.0,
        "rerun_total_seconds": 1.5,
        "peak_rss_bytes": 100 * BYTES_PER_MIB,
        "rerun_revisions_written": {"identity": 0, "knowledge": 0, "binding": 0},
        "store_bytes_after_first": BYTES_PER_KIB,
        "store_bytes_after_rerun": BYTES_PER_KIB,
        "degradations": 0,
        "network_attempted": 0,
        "client_imports_attempted": 0,
    }
    base.update(overrides)
    return RepoResult(**base)


def self_test() -> int:
    """Prove the verdict reports, and prove it does not report on a clean run.

    Both halves are needed. A harness that flags everything passes the first half
    perfectly, which is why `quarantine_audit` and `escape_coverage` each carry a
    positive control and why this one does.
    """
    clean = verdict([_synthetic()])
    if clean:
        print(f"self-test FAILED: a clean result produced breaches {clean}", file=sys.stderr)
        return 1
    print("self-test: a clean result produces no breach (positive control) ->")

    planted: list[tuple[str, RepoResult]] = [
        ("N1", _synthetic(stage1_seconds=float(MAP_STAGE1_BUDGET_S) + 1)),
        ("N2", _synthetic(total_seconds=float(MAP_TOTAL_BUDGET_S) + 1)),
        ("N3", _synthetic(rerun_total_seconds=float(MAP_INCREMENTAL_BUDGET_S) + 1)),
        ("N11", _synthetic(peak_rss_bytes=MAP_MAX_RSS_BYTES + 1)),
        ("N11", _synthetic(peak_rss_bytes=None)),
        ("N3", _synthetic(rerun_revisions_written={"identity": 1, "knowledge": 0, "binding": 0})),
    ]
    for nfr, result in planted:
        found = verdict([result])
        if not any(b.nfr == nfr for b in found):
            print(
                f"self-test FAILED: a planted {nfr} breach was not reported (got {found})",
                file=sys.stderr,
            )
            return 1
        print(f"self-test: planted {nfr} breach reported -> {found[0].what}")

    fast_but_writing = verdict([_synthetic(rerun_revisions_written={"identity": 1})])
    if not any(b.what == "rerun_revisions_written" for b in fast_but_writing):
        print("self-test FAILED: a fast re-run that wrote revisions passed N3", file=sys.stderr)
        return 1
    print("self-test: a re-run inside its budget that wrote a revision still fails N3 ->")
    print("self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--manifest", type=Path, default=CORPUS_MANIFEST)
    parser.add_argument("--clone", action="store_true", help="clone any missing corpus repository")
    parser.add_argument("--only", action="append", default=None, help="restrict to a named repo")
    parser.add_argument("--archive", type=Path, default=None, help="directory to archive into")
    parser.add_argument("--assert", dest="do_assert", action="store_true", help="exit 1 on breach")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    specs = [s for s in manifest["repositories"] if not args.only or s["name"] in args.only]
    workdir = args.corpus_dir / "_runs"
    workdir.mkdir(parents=True, exist_ok=True)

    results: list[RepoResult] = []
    for spec in specs:
        tree = args.corpus_dir / str(spec["name"])
        if not tree.exists():
            if not args.clone:
                results.append(
                    _synthetic(
                        name=str(spec["name"]),
                        archetype=str(spec["archetype"]),
                        error=f"not cloned; pass --clone or place a checkout at {tree}",
                    )
                )
                continue
            tree = clone(spec, args.corpus_dir)
        try:
            results.append(soak_one(spec, tree, workdir))
        except Exception as exc:
            results.append(
                _synthetic(name=str(spec["name"]), archetype=str(spec["archetype"]), error=str(exc))
            )

    report = SoakReport(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        machine=machine_facts(),
        corpus={
            "manifest": str(args.manifest.relative_to(REPO_ROOT)),
            "decision": manifest["decision"],
        },
        repos=results,
        breaches=verdict(results),
        notes=corpus_notes(results),
    )

    if args.archive is not None:
        archive(report, args.archive)
    print(json.dumps(asdict(report), indent=2, sort_keys=True) if args.json else render(report))
    return 1 if (args.do_assert and report.breaches) else 0


if __name__ == "__main__":
    sys.exit(main())
