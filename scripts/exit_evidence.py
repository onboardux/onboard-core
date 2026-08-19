"""`03` section 11 item 6's exit evidence -- M2 per archetype, M4, M5, in one command.

    uv run python scripts/exit_evidence.py --json
    uv run python scripts/exit_evidence.py --self-test

Three numbers close Build 1, and `05` S1.8 asks for each separately. They are
collected here together because they are quoted together, and three commands whose
outputs someone pastes into a document by hand is three chances for the document to
disagree with the repository.

**M2 is reported twice, and that is the finding rather than a hedge** (B1-CR-90,
`docs/pack/OPEN-DECISIONS.md` OD-19). `01` section 6 defines M2 as
`facts[method in {grammar, reflection}] / facts[*]`, and the `data` pack scores
**0.000** on it while recovering every labelled identity, because a dbt project is
YAML and SQL a person wrote -- evidence method `declared`. ADR-0.1's
reversal trigger asks whether **deterministic plugins** are carrying an archetype or
whether the agent is, and `declared` evidence reaches no model, runs no heuristic
and degrades no rung. So this prints M2 exactly as `01` section 6 defines it **and**
the non-model share beside it, and the exit-evidence document rules the trigger
against the second while quoting the first. The predicate in `01` section 6 is
unchanged: a metric edited to make a flag stop flipping is a metric nobody can trust
afterwards, and S1.6 refused that trade when it raised B1-CR-78 rather than retuning.

**M5 has no denominator in this environment and reports `null`.** `01` section 6 M5
is `reviews[outcome='rewritten'] / reviews[outcome != 'pending']` over the review
ledger, and no human has reviewed an agent-authored module here because the glue
pass has never called a model -- no adapter credential exists (`05` S1.7, three open
items). An undefined ratio reports `null` and never `0.0`: a build with no reviews
has not achieved a perfect rewrite rate, it has measured nothing, and `ci_metrics`
established that rule for the same reason.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adopt_map.review_ledger import m5_rewrite_rate, read_all  # noqa: E402

from scripts.label_eval import DETERMINISTIC_METHODS  # noqa: E402

#: The evidence rungs that reach no model and run no heuristic. `grammar` and
#: `reflection` are `01` §6 M2's own numerator; `declared` is the rung a
#: declarative artefact lands on, and OD-19 is the argument for reporting it.
NON_MODEL_METHODS: Final[frozenset[str]] = DETERMINISTIC_METHODS | {"declared"}

#: One entry per shipped archetype: its fixture, whether that fixture is an export
#: bundle, and the labelled identity set S1.4-S1.6 shipped with it.
ARCHETYPES: Final[tuple[tuple[str, str, bool, str], ...]] = (
    ("web", "fixtures/repos/django-orders", False, "django-orders"),
    ("ai", "fixtures/repos/langgraph-support", False, "langgraph-support"),
    ("platform", "fixtures/repos/sf-metadata-bundle", True, "sf-metadata-bundle"),
    ("lowcode", "fixtures/repos/powerapps-export", True, "powerapps-export"),
    ("data", "fixtures/repos/dbt-warehouse", False, "dbt-warehouse"),
)


@dataclass
class ArchetypeEvidence:
    """One archetype's M2, its non-model share, and its labelled-set recall."""

    archetype: str
    fixture: str
    facts: int
    m2_deterministic_share: float | None
    non_model_share: float | None
    recall: float | None
    precision: float | None
    method_counts: dict[str, int]


def _share(counts: dict[str, int], methods: frozenset[str]) -> float | None:
    """A share over `counts`, or `None` when there is nothing to divide.

    **Undefined is `None`, never 0.0 and never 1.0.** A pack that emitted no fact
    has not achieved a deterministic share of zero; it has measured nothing, and a
    zero here would fire ADR-0.1's reversal trigger on an empty run.
    """
    total = sum(counts.values())
    if not total:
        return None
    return sum(value for key, value in counts.items() if key in methods) / total


def collect_archetype(name: str, tree: str, is_bundle: bool, labels: str) -> ArchetypeEvidence:
    """Run one archetype's fixture and score it."""
    from adopt_extractors_common import pack as common_pack
    from adopt_map.orchestrator import run as run_map
    from adopt_map.plugins import ExtractorRegistry

    from tests.build1_conftest import build_scoped_store, surface_writer_for

    packs = {
        "web": "adopt_extractors_web",
        "ai": "adopt_extractors_ai",
        "platform": "adopt_extractors_platform",
        "lowcode": "adopt_extractors_lowcode",
        "data": "adopt_extractors_data",
    }
    module = __import__(packs[name], fromlist=["pack"])

    work = Path(tempfile.mkdtemp(prefix=f"exit-{name}-"))
    handle, scopes = build_scoped_store(work, archetype=name)  # type: ignore[arg-type]
    try:
        registry = ExtractorRegistry(enabled_packs=frozenset({"common", name}))
        registry.register_all(common_pack())
        registry.register_all(module.pack())
        result = run_map(
            resolved=scopes["prod"],
            root=Path() if is_bundle else Path(tree),
            export_bundle=Path(tree) if is_bundle else None,
            registry=registry,
            adopt_version="exit-evidence",
            writer=surface_writer_for(handle),
            out_dir=work / "out",
            sequential=True,
        )
    finally:
        handle.close()

    # The method lives on the batch's **manifest**, not on the fact: `02` §7
    # obligation 5 makes the evidence method a property the extractor declares
    # once, and the framework -- never the extractor -- turns it into a
    # confidence. Counting per batch is therefore counting per declared rung.
    counts: dict[str, int] = {}
    for batch in result.batches:
        method = batch.manifest.method
        counts[method] = counts.get(method, 0) + len(batch.facts)

    recall, precision = _score_labels(work / "out" / "surface.json", labels)
    return ArchetypeEvidence(
        archetype=name,
        fixture=tree,
        facts=sum(counts.values()),
        m2_deterministic_share=_share(counts, DETERMINISTIC_METHODS),
        non_model_share=_share(counts, NON_MODEL_METHODS),
        recall=recall,
        precision=precision,
        method_counts=dict(sorted(counts.items())),
    )


def _score_labels(surface: Path, fixture: str) -> tuple[float | None, float | None]:
    """Labelled-set recall and precision, via the instrument that already owns them."""
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/label_eval.py",
            "--fixture",
            fixture,
            "--surface",
            str(surface),
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None, None
    return payload.get("recall"), payload.get("precision")


def collect_m4() -> dict[str, Any]:
    """M4, via `scripts/move_eval.py` -- the instrument that owns the corpus."""
    completed = subprocess.run(
        [sys.executable, "scripts/move_eval.py", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    try:
        payload: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"precision": None, "note": "move_eval produced no parsable report"}
    return payload


def collect_m5(ledger: Path) -> dict[str, Any]:
    """M5 from the review ledger, or `null` with the reason."""
    if not ledger.exists():
        return {
            "rewrite_rate": None,
            "decided": 0,
            "reason": (
                "no review ledger exists. The glue pass has never called a model in "
                "this environment (no adapter credential), so no module has been "
                "authored, quarantined or reviewed -- `05` S1.7's three open items."
            ),
        }
    entries = read_all(ledger)
    decided = [e for e in entries if e.outcome in {"approved", "rewritten", "rejected"}]
    return {
        "rewrite_rate": m5_rewrite_rate(entries),
        "decided": len(decided),
        "entries": len(entries),
        "reason": None if decided else "the ledger exists and holds no decided review",
    }


def render(evidence: Sequence[ArchetypeEvidence], m4: dict[str, Any], m5: dict[str, Any]) -> str:
    def show(value: float | None) -> str:
        return "undefined" if value is None else f"{value:.3f}"

    lines = ["Build 1 exit evidence -- `01` §6 M2 per archetype, M4, M5", ""]
    lines.append(f"  {'archetype':10} {'facts':>6}  {'M2':>9}  {'non-model':>9}  {'recall':>7}")
    for entry in evidence:
        lines.append(
            f"  {entry.archetype:10} {entry.facts:>6}  "
            f"{show(entry.m2_deterministic_share):>9}  {show(entry.non_model_share):>9}  "
            f"{show(entry.recall):>7}"
        )
    lines.append("")
    lines.append(f"  M4 move precision      {show(m4.get('precision'))}")
    lines.append(f"  M4 declination accuracy {show(m4.get('declination_accuracy'))}")
    lines.append(f"  M5 glue rewrite rate   {show(m5.get('rewrite_rate'))}")
    if m5.get("reason"):
        lines.append(f"     M5 is undefined because: {m5['reason']}")
    return "\n".join(lines)


def self_test() -> int:
    """Prove the shares refuse to invent a number for an empty pack."""
    if _share({}, DETERMINISTIC_METHODS) is not None:
        print("self-test FAILED: an empty pack reported a share", file=sys.stderr)
        return 1
    print("self-test: an empty pack reports undefined, never 0.0 or 1.0 ->")

    declared_only = {"declared": 9}
    if _share(declared_only, DETERMINISTIC_METHODS) != 0.0:
        print("self-test FAILED: a declared-only pack did not score 0.000 on M2", file=sys.stderr)
        return 1
    if _share(declared_only, NON_MODEL_METHODS) != 1.0:
        print(
            "self-test FAILED: a declared-only pack did not score 1.000 on the "
            "non-model share -- the two readings B1-CR-90 distinguishes have collapsed",
            file=sys.stderr,
        )
        return 1
    print("self-test: a declared-only pack scores M2 0.000 and non-model 1.000 ->")
    print("self-test: that difference is B1-CR-78's finding and OD-19's ruling ->")
    print("self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--ledger", type=Path, default=Path(".adopt/review_ledger.jsonl"))
    parser.add_argument("--skip-m4", action="store_true", help="M4 runs twenty maps; skip it")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    evidence = [collect_archetype(*entry) for entry in ARCHETYPES]
    m4 = {"precision": None, "note": "skipped"} if args.skip_m4 else collect_m4()
    m5 = collect_m5(args.ledger)

    if args.json:
        print(
            json.dumps(
                {
                    "archetypes": [asdict(entry) for entry in evidence],
                    "m4": m4,
                    "m5": m5,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(render(evidence, m4, m5))
    return 0


if __name__ == "__main__":
    sys.exit(main())
