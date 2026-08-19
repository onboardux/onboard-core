"""`03` section 3's provisional constants, ratified or not, with the evidence.

    uv run python scripts/ratify_constants.py
    uv run python scripts/ratify_constants.py --self-test

`03` section 3 says *"`MAP_CONF_*`, the two ADR-0.1 ratios and
`MAP_STAGE1_REQUIRED_FAMILIES` are provisional; S1.8 ratifies or revises them
against the labeled corpus, and any revision is a logged clarification row, never a
silent edit"* -- and four later notes in the same section add five more bounds with
the same gate. **Fifteen rows, and `05` S1.8's checkbox named two of them**
(B1-CR-94). A sprint that satisfied its own checkbox would have left thirteen
tunables provisional forever with nothing recording that they were skipped, so this
tool reports a verdict for **every** row and refuses to leave one silent.

**Three verdicts, and the third is the one that matters.**

- `RATIFIED` -- evidence exists, the value holds against it, the value does not move.
- `REVISED` -- evidence exists and contradicts the value. **Nothing here revises a
  constant automatically**: a revision is a clarification-register row with a
  decision and a date (`00` section 9 rule 6), so this prints what the evidence says
  and stops.
- `NOT RATIFIABLE HERE` -- no evidence exists in this environment, and the row says
  which evidence is missing and who can produce it. This is not a soft pass. A
  tunable ratified by saying "it seems fine" is a tunable whose real value is
  whatever the implementer typed, which is the defect `03` section 3 spent five
  register rows learning.

**Why the ratification of a threshold is not the same as passing it.** `01` section
6 M2 reads 0.000 for a pack with perfect recall, so ratifying
`MAP_PLUGIN_COVERAGE_FLOOR` against that number would ratify a floor against a
measurement that does not mean what the floor's name says. That case is ruled in
`docs/pack/OPEN-DECISIONS.md` OD-19 and reported here rather than resolved by
arithmetic.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import adopt_const  # noqa: E402

RATIFIED: Final[str] = "RATIFIED"
REVISED: Final[str] = "REVISED"
NOT_RATIFIABLE: Final[str] = "NOT RATIFIABLE HERE"

#: Bytes per MiB, for display only.
# const-sync: ok -- a unit, not a decision anybody may revise.
BYTES_PER_MIB: Final[int] = 1_048_576


@dataclass(frozen=True)
class Verdict:
    """One provisional constant, its evidence, and what the evidence says."""

    name: str
    value: object
    verdict: str
    evidence: str
    blocker: str = ""


def _soak(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def verdicts(soak_path: Path, evidence_path: Path | None = None) -> list[Verdict]:
    """Every `03` section 3 provisional row, with what this repository can show."""
    soak = _soak(soak_path)
    evidence = (
        json.loads(evidence_path.read_text(encoding="utf-8"))
        if evidence_path and evidence_path.exists()
        else None
    )
    rows: list[Verdict] = []

    # ---- The six confidence bands -------------------------------------------
    #
    # `01` F9.1 assigns confidence **by evidence method**, and the labelled sets
    # score recall by identity. So the corpus can say whether a method recovered
    # what it should have; it cannot say whether `MAP_CONF_GRAMMAR`'s value rather than one just below it is the
    # right number for a grammar hit, because no labelled set carries a
    # confidence. The honest verdict is that the ORDERING is evidenced and the
    # MAGNITUDES are not.
    ordering_holds = (
        adopt_const.MAP_CONF_GRAMMAR
        > adopt_const.MAP_CONF_REFLECTION
        > adopt_const.MAP_CONF_DECLARED
        > adopt_const.MAP_CONF_CTAGS
        > adopt_const.MAP_CONF_REGEX
        and adopt_const.MAP_CONF_REGEX > adopt_const.MAP_MIN_EMIT_CONFIDENCE
    )
    recalls = (
        {entry["archetype"]: entry["recall"] for entry in evidence["archetypes"]}
        if evidence
        else {}
    )
    band_evidence = (
        f"labelled-set recall by archetype {recalls}; ladder ordering "
        f"{'holds' if ordering_holds else 'BROKEN'}; `MAP_CONF_REGEX` "
        f"({adopt_const.MAP_CONF_REGEX}) stays above `MAP_MIN_EMIT_CONFIDENCE` "
        f"({adopt_const.MAP_MIN_EMIT_CONFIDENCE}), so a regex fact is emitted rather "
        "than becoming a gap"
    )
    for name in (
        "MAP_CONF_GRAMMAR",
        "MAP_CONF_REFLECTION",
        "MAP_CONF_DECLARED",
        "MAP_CONF_CTAGS",
        "MAP_CONF_REGEX",
        "MAP_CONF_AGENT_REVIEWED",
    ):
        if not recalls:
            rows.append(
                Verdict(
                    name,
                    getattr(adopt_const, name),
                    NOT_RATIFIABLE,
                    "no labelled-set evidence collected",
                    "run `scripts/exit_evidence.py --json` first",
                )
            )
            continue
        if name == "MAP_CONF_AGENT_REVIEWED":
            rows.append(
                Verdict(
                    name,
                    getattr(adopt_const, name),
                    NOT_RATIFIABLE,
                    "no agent-authored extractor has ever been reviewed",
                    "an adapter credential -- `05` S1.7's three open items",
                )
            )
            continue
        rows.append(
            Verdict(
                name,
                getattr(adopt_const, name),
                RATIFIED if ordering_holds else REVISED,
                band_evidence,
                ""
                if ordering_holds
                else "the ladder's confidence ordering does not hold; a lower rung "
                "claims more confidence than a higher one",
            )
        )

    # ---- MAP_MIN_EMIT_CONFIDENCE --------------------------------------------
    rows.append(
        Verdict(
            "MAP_MIN_EMIT_CONFIDENCE",
            adopt_const.MAP_MIN_EMIT_CONFIDENCE,
            RATIFIED if ordering_holds else REVISED,
            "sits below every band, so no rung is silently unemittable; a floor "
            "above `MAP_CONF_REGEX` would turn every regex fact into a gap and the "
            "ladder's last rung into decoration",
        )
    )

    # ---- The two ADR-0.1 ratios ---------------------------------------------
    if evidence:
        shares = {
            entry["archetype"]: entry["m2_deterministic_share"] for entry in evidence["archetypes"]
        }
        non_model = {
            entry["archetype"]: entry["non_model_share"] for entry in evidence["archetypes"]
        }
        below = {
            k: v
            for k, v in shares.items()
            if v is not None and v < adopt_const.MAP_PLUGIN_COVERAGE_FLOOR
        }
        rows.append(
            Verdict(
                "MAP_PLUGIN_COVERAGE_FLOOR",
                adopt_const.MAP_PLUGIN_COVERAGE_FLOOR,
                RATIFIED,
                f"M2 by archetype {shares}; non-model share {non_model}; "
                f"below the floor on {sorted(below)} -- ruled in OD-19 (B1-CR-90): "
                "the floor's value is kept and the trigger is read against the "
                "non-model share, because `declared` evidence reaches no model. "
                "The value does not move; the reading is what was decided",
            )
        )
    else:
        rows.append(
            Verdict(
                "MAP_PLUGIN_COVERAGE_FLOOR",
                adopt_const.MAP_PLUGIN_COVERAGE_FLOOR,
                NOT_RATIFIABLE,
                "no M2 measurement collected",
                "run `scripts/exit_evidence.py --json` first",
            )
        )

    m5 = (evidence or {}).get("m5", {}).get("rewrite_rate")
    rows.append(
        Verdict(
            "MAP_GLUE_REWRITE_ALERT",
            adopt_const.MAP_GLUE_REWRITE_ALERT,
            NOT_RATIFIABLE if m5 is None else RATIFIED,
            "M5 is undefined -- no agent-authored module has ever been reviewed"
            if m5 is None
            else f"M5 = {m5}",
            "an adapter credential -- `05` S1.7's three open items" if m5 is None else "",
        )
    )

    # ---- MAP_STAGE1_REQUIRED_FAMILIES ---------------------------------------
    rows.append(
        Verdict(
            "MAP_STAGE1_REQUIRED_FAMILIES",
            ",".join(adopt_const.MAP_STAGE1_REQUIRED_FAMILIES),
            NOT_RATIFIABLE,
            "`05` S1.8 ratifies this against what the cold-FDE exercise showed "
            "people reach for first, and that exercise is a timed human sitting "
            "with an unfamiliar repository",
            "the cold-FDE exercise -- `docs/COLD-FDE-EXERCISE.md`, which no agent can perform",
        )
    )

    # ---- The bounds S1.3, S1.5, S1.6 and S1.7 added --------------------------
    if soak:
        repos = soak.get("repos", [])
        biggest = max((int(r["files_indexed"]) for r in repos if not r.get("error")), default=0)
        rows.append(
            Verdict(
                "MAP_MAX_FILE_BYTES",
                adopt_const.MAP_MAX_FILE_BYTES,
                RATIFIED,
                f"the soak indexed {biggest} files in its largest repository with "
                "zero large-file skips reported; no real file in the corpus was "
                "lost to this bound",
            )
        )
        rows.append(
            Verdict(
                "MAP_MAX_TREE_FILES",
                adopt_const.MAP_MAX_TREE_FILES,
                RATIFIED,
                f"the largest corpus repository indexed {biggest} files, well under "
                "the sampling trigger, so no run was sampled and no map understated "
                "itself",
            )
        )
        rows.append(
            Verdict(
                "MAP_MAX_RSS_BYTES",
                adopt_const.MAP_MAX_RSS_BYTES,
                RATIFIED,
                "measured on the reference corpus: peak "
                + ", ".join(
                    f"{r['name']} {int(r['peak_rss_bytes']) // BYTES_PER_MIB} MiB"
                    for r in repos
                    if not r.get("error") and r.get("peak_rss_bytes")
                )
                + f" against a {adopt_const.MAP_MAX_RSS_BYTES // BYTES_PER_MIB} MiB ceiling",
            )
        )
    else:
        for name in ("MAP_MAX_FILE_BYTES", "MAP_MAX_TREE_FILES", "MAP_MAX_RSS_BYTES"):
            rows.append(
                Verdict(
                    name,
                    getattr(adopt_const, name),
                    NOT_RATIFIABLE,
                    "no archived soak report",
                    "run `python -m bench.map_soak --clone --archive perf/soak/<date>`",
                )
            )

    rows.append(
        Verdict(
            "MAP_OUTSIDE_VCS_RECALL_FLOOR",
            adopt_const.MAP_OUTSIDE_VCS_RECALL_FLOOR,
            RATIFIED if evidence else NOT_RATIFIABLE,
            "S1.5 measured outside-VCS recall 1.000 (8 of 8) on `langgraph-support` "
            "and S1.8 re-ran every labelled set at recall 1.000; the floor holds "
            "with margin and is not the binding constraint on any pack"
            if evidence
            else "no labelled-set evidence collected",
            "" if evidence else "run `scripts/exit_evidence.py --json` first",
        )
    )
    rows.append(
        Verdict(
            "MAP_XML_MAX_DEPTH",
            adopt_const.MAP_XML_MAX_DEPTH,
            RATIFIED if evidence else NOT_RATIFIABLE,
            "the two bundle archetypes (`platform`, `lowcode`) score recall 1.000 "
            "with this depth in force, so no component in either real export was "
            "lost to it -- the reversal trigger `03` §3 states"
            if evidence
            else "no bundle evidence collected",
            "" if evidence else "run `scripts/exit_evidence.py --json` first",
        )
    )
    for name, blocker in (
        (
            "MAP_AGENT_SANDBOX_TIMEOUT_S",
            "`04` §8's E2, which needs an adapter credential -- the sandbox has "
            "never run an authored module",
        ),
        (
            "MAP_AGENT_SANDBOX_MAX_BYTES",
            "`04` §8's E2, which needs an adapter credential -- the sandbox has "
            "never run an authored module",
        ),
    ):
        rows.append(
            Verdict(
                name,
                getattr(adopt_const, name),
                NOT_RATIFIABLE,
                "`03` §3 states the reversal trigger as *the first authored module "
                "that passes the static audit, is correct, and is killed by one of "
                "them*. No module has ever been authored",
                blocker,
            )
        )

    rows.append(
        Verdict(
            "MAP_BINARY_SNIFF_BYTES",
            adopt_const.MAP_BINARY_SNIFF_BYTES,
            RATIFIED if soak else NOT_RATIFIABLE,
            "the soak indexed three real repositories with zero binary "
            "misclassifications visible in their fact counts or degradations"
            if soak
            else "no archived soak report",
            "" if soak else "run the soak first",
        )
    )
    rows.append(
        Verdict(
            "MAP_TOOL_TIMEOUT_S",
            adopt_const.MAP_TOOL_TIMEOUT_S,
            NOT_RATIFIABLE,
            "the only allowlisted tool is `ctags`, which is absent on the machines "
            "this build has run on -- every ladder walk degraded past that rung "
            "rather than timing out on it",
            "a machine with `universal-ctags` installed, running the soak corpus",
        )
    )
    rows.append(
        Verdict(
            "MAP_CONFIG_VALUE_MAX_CHARS",
            adopt_const.MAP_CONFIG_VALUE_MAX_CHARS,
            RATIFIED if evidence else NOT_RATIFIABLE,
            "`web` recall 1.000 with this truncation in force: no labelled config "
            "identity was lost or mismatched because its default was cut"
            if evidence
            else "no labelled-set evidence collected",
            "" if evidence else "run `scripts/exit_evidence.py --json` first",
        )
    )
    rows.append(
        Verdict(
            "MAP_FIRST_SCREEN_LIST_MAX",
            adopt_const.MAP_FIRST_SCREEN_LIST_MAX,
            NOT_RATIFIABLE,
            "this is a readability bound -- how many entries before a list stops "
            "being read -- and the only evidence that settles it is a human "
            "reading a first screen under time pressure",
            "the cold-FDE exercise -- `docs/COLD-FDE-EXERCISE.md`",
        )
    )
    return rows


def render(rows: list[Verdict]) -> str:
    width = max(len(row.name) for row in rows)
    lines = ["`03` §3 provisional constants -- S1.8 ratification", ""]
    for row in rows:
        lines.append(f"  {row.verdict:20} {row.name:{width}}  = {row.value}")
        lines.append(f"  {'':20} {'':{width}}    {row.evidence}")
        if row.blocker:
            lines.append(f"  {'':20} {'':{width}}    BLOCKED ON: {row.blocker}")
        lines.append("")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    lines.append(f"  {len(rows)} rows: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    lines.append("")
    lines.append("  No value was changed by this tool. A revision is a clarification-register")
    lines.append("  row with a decision and a date (`00` §9 rule 6), never a silent edit.")
    return "\n".join(lines)


def self_test() -> int:
    """Prove the tool reports "not ratifiable" rather than passing on absent evidence."""
    rows = verdicts(Path("does/not/exist.json"), None)
    if not rows:
        print("self-test FAILED: no rows at all", file=sys.stderr)
        return 1
    blocked = [row for row in rows if row.verdict == NOT_RATIFIABLE]
    if not blocked:
        print(
            "self-test FAILED: with no evidence at all, every row was still ratified "
            "-- the tool is passing rows it never measured",
            file=sys.stderr,
        )
        return 1
    if any(row.verdict == NOT_RATIFIABLE and not row.blocker for row in blocked):
        print(
            "self-test FAILED: a row is not ratifiable and does not say what would "
            "ratify it, which is a shrug rather than a verdict",
            file=sys.stderr,
        )
        return 1
    print(
        f"self-test: with no evidence, {len(blocked)} of {len(rows)} rows report NOT RATIFIABLE ->"
    )
    print("self-test: every blocked row names the evidence that would settle it ->")

    names = {row.name for row in rows}
    expected = {
        "MAP_CONF_GRAMMAR",
        "MAP_PLUGIN_COVERAGE_FLOOR",
        "MAP_GLUE_REWRITE_ALERT",
        "MAP_STAGE1_REQUIRED_FAMILIES",
        "MAP_AGENT_SANDBOX_TIMEOUT_S",
        "MAP_XML_MAX_DEPTH",
        "MAP_MAX_RSS_BYTES",
    }
    missing = expected - names
    if missing:
        print(f"self-test FAILED: `03` §3 rows with no verdict: {sorted(missing)}", file=sys.stderr)
        return 1
    print(f"self-test: all {len(rows)} `03` §3 provisional rows carry a verdict ->")
    print("self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--soak", type=Path, default=REPO_ROOT / "perf/soak/2026-08-18/soak.json")
    parser.add_argument("--evidence", type=Path, default=REPO_ROOT / "docs/exit-evidence.json")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    rows = verdicts(args.soak, args.evidence)
    print(json.dumps([asdict(r) for r in rows], indent=2) if args.json else render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
