"""Recall and precision of a run's `surface.json` against a labeled identity set.

**B1-CR-64: two Final Output Validation lines called this tool and no checkbox
built it.** `05` S1.4's coverage line (after B1-CR-62's repair) and S1.5's M8
recall line both invoke it, and both named `python -m adopt.tools.label_eval` --
a dotted `adopt.` namespace that does not exist, because Build 0 shipped flat
`adopt_*` packages. It lives here instead, where `03` §4 puts gates and where
`coverage_floor.py` and `ci_metrics.py` already live.

**What it measures and why that measurement replaced a coverage ratio.**
B1-CR-62 established that `recompute_coverage` reports 0.0 for every identity this
build writes, structurally: the rule needs an applicable `audience_tag` and
`00` §5 forbids Build 1 to write one. So `05` S1.4's `jq '.coverage.ratio' >= 0.60`
was unreachable however well extraction went. Recall against a hand-labeled
identity set is the measurement that threshold was reaching for: it is a property
of the **facts**, which this build owns, rather than of a store rule it does not.

**Identities are compared as `(kind, namespace, local_key)`, not as URIs.** A
labeled set carrying full URIs would be bound to one firm/engagement/system/
environment slug quartet and would stop matching the moment a fixture is
registered under a different scope -- which is a property of the test harness, not
of extraction quality.

**`--self-test` plants a missing identity and requires a red run.** An evaluator
nobody has watched report a miss is one nobody should quote a recall figure from,
which is the same argument every other gate in this repository makes about
planting. It also plants a *spurious* fact and requires precision to drop, because
a recall-only instrument is one an extractor can satisfy by emitting everything.
"""

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from adopt_const import MAP_FIRST_SCREEN_LIST_MAX

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: Where a run leaves `surface.json` by default (`02` §8's `--out`).
DEFAULT_OUT_DIR: Final[Path] = Path(".adopt/out")

#: Where a pack's hand-labeled identity set lives (`03` §5.10).
LABELS_DIR: Final[Path] = REPO_ROOT / "fixtures" / "labeled"

#: `01` §6 M2's numerator: the evidence methods that are deterministic.
DETERMINISTIC_METHODS: Final[frozenset[str]] = frozenset({"grammar", "reflection"})


@dataclass(frozen=True)
class Identity:
    """One referent, scope-independent."""

    identity_kind: str
    namespace: str
    local_key: str

    def render(self) -> str:
        return f"{self.identity_kind}/{self.namespace or '-'}/{self.local_key}"


@dataclass(frozen=True)
class Result:
    """What one comparison found."""

    matched: tuple[Identity, ...]
    missing: tuple[Identity, ...]
    spurious: tuple[Identity, ...]
    deterministic_facts: int
    total_facts: int

    @property
    def recall(self) -> float:
        labeled = len(self.matched) + len(self.missing)
        return len(self.matched) / labeled if labeled else 0.0

    @property
    def precision(self) -> float:
        found = len(self.matched) + len(self.spurious)
        return len(self.matched) / found if found else 0.0

    @property
    def deterministic_share(self) -> float:
        """M2. **Undefined reports 0.0 rather than 1.0** -- a run with no facts
        has not achieved a perfect deterministic share, and `ci_metrics` made the
        same choice for the same reason."""
        return self.deterministic_facts / self.total_facts if self.total_facts else 0.0


def load_labels(path: Path) -> tuple[Identity, ...]:
    """The labeled identity set, as scope-independent triples."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    identities = payload.get("identities")
    if not isinstance(identities, list):
        raise ValueError(f"{path} carries no `identities` list")
    return tuple(
        Identity(
            identity_kind=str(item["identity_kind"]),
            namespace=str(item.get("namespace") or ""),
            local_key=str(item["local_key"]),
        )
        for item in identities
    )


def facts_of(payload: Mapping[str, Any]) -> tuple[tuple[Identity, str], ...]:
    """`(identity, evidence method)` for every fact in a `surface.json`.

    The key is recovered by parsing the fact's URI rather than read from a field,
    because `02` §9.2's `facts[]` carries `identity_uri` and no `local_key` --
    and parsing it through Build 0's own `parse_uri` means this gate agrees with
    the minting rules by construction rather than by a second implementation of
    the escaping.
    """
    from adopt_identity import parse_uri

    found: list[tuple[Identity, str]] = []
    for fact in payload.get("facts", []):
        uri = fact.get("identity_uri")
        if not isinstance(uri, str):
            continue
        parsed = parse_uri(uri)
        found.append(
            (
                Identity(
                    identity_kind=parsed.kind,
                    namespace=parsed.namespace or "",
                    local_key="/".join(parsed.key),
                ),
                str(fact.get("method") or ""),
            )
        )
    return tuple(found)


def compare(labels: Iterable[Identity], facts: Iterable[tuple[Identity, str]]) -> Result:
    labeled = set(labels)
    observed = list(facts)
    seen = {identity for identity, _method in observed}
    deterministic = sum(1 for _i, method in observed if method in DETERMINISTIC_METHODS)
    return Result(
        matched=tuple(sorted(labeled & seen, key=Identity.render)),
        missing=tuple(sorted(labeled - seen, key=Identity.render)),
        spurious=tuple(sorted(seen - labeled, key=Identity.render)),
        deterministic_facts=deterministic,
        total_facts=len(observed),
    )


def report(result: Result, *, name: str, limit: int) -> None:
    print(f"label-eval: {name}")
    print(
        f"  recall     {result.recall:.3f}  "
        f"({len(result.matched)} of {len(result.matched) + len(result.missing)} labeled)"
    )
    print(
        f"  precision  {result.precision:.3f}  ({len(result.spurious)} unlabeled fact(s) emitted)"
    )
    print(
        f"  det. share {result.deterministic_share:.3f}  "
        f"({result.deterministic_facts} of {result.total_facts} facts grammar|reflection)"
    )
    for title, entries in (("missing", result.missing), ("unlabeled", result.spurious)):
        if not entries:
            continue
        print(f"  {title}:")
        for identity in entries[:limit]:
            print(f"    {identity.render()}")
        if len(entries) > limit:
            print(f"    ... and {len(entries) - limit} more")


def self_test() -> int:
    """Prove the evaluator reports a miss and a spurious fact.

    Both directions, because an instrument that only notices absence can be
    satisfied by an extractor that emits the universe, and one that only notices
    excess can be satisfied by an extractor that emits nothing.
    """
    failures: list[str] = []
    labels = (
        Identity("endpoint", "http", "GET /a"),
        Identity("endpoint", "http", "GET /b"),
        Identity("job", "cron", "/usr/bin/x"),
    )

    complete = [(identity, "grammar") for identity in labels]
    green = compare(labels, complete)
    if green.recall != 1.0 or green.precision != 1.0:
        failures.append(
            f"a complete, exact run did NOT score 1.0/1.0 "
            f"(recall {green.recall}, precision {green.precision})"
        )
    else:
        print("self-test: a complete run scores recall 1.000, precision 1.000 ->")

    planted_missing = compare(labels, complete[:-1])
    if planted_missing.recall >= 1.0 or not planted_missing.missing:
        failures.append("a planted MISSING identity was NOT reported")
    else:
        print(
            f"self-test: planted missing identity reported -> recall "
            f"{planted_missing.recall:.3f}, missing "
            f"{[i.render() for i in planted_missing.missing]}"
        )

    planted_spurious = compare(
        labels, [*complete, (Identity("endpoint", "http", "GET /invented"), "regex")]
    )
    if planted_spurious.precision >= 1.0 or not planted_spurious.spurious:
        failures.append("a planted SPURIOUS fact was NOT reported")
    else:
        print(
            f"self-test: planted spurious fact reported -> precision "
            f"{planted_spurious.precision:.3f}, unlabeled "
            f"{[i.render() for i in planted_spurious.spurious]}"
        )

    if planted_spurious.deterministic_share >= 1.0:
        failures.append("a regex-method fact did NOT lower the deterministic share")
    else:
        print(
            f"self-test: a regex fact lowers the deterministic share -> "
            f"{planted_spurious.deterministic_share:.3f}"
        )

    empty = compare(labels, [])
    if empty.deterministic_share != 0.0:
        failures.append("an empty run reported a non-zero deterministic share")
    else:
        print("self-test: an empty run reports share 0.000, never 1.000 ->")

    if failures:
        for failure in failures:
            print(f"VIOLATION: {failure}")
        print(f"label-eval --self-test: {len(failures)} failure(s)")
        return 1
    print("label-eval --self-test: OK (5/5 checks)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fixture", help="fixture name; reads fixtures/labeled/<name>.identities.json"
    )
    parser.add_argument("--labels", type=Path, help="explicit path to a labeled identity set")
    parser.add_argument(
        "--surface",
        type=Path,
        default=DEFAULT_OUT_DIR / "surface.json",
        help="the run artifact to score (default: .adopt/out/surface.json)",
    )
    parser.add_argument("--min-recall", type=float, default=None, help="fail below this recall")
    parser.add_argument(
        "--min-deterministic-share",
        type=float,
        default=None,
        help="fail below this grammar|reflection share (`01` §6 M2)",
    )
    # The same cap the first screen uses. Genuinely one tunable: both answer
    # "how many entries before a list stops being readable", and if S1.8 retunes
    # the first-screen cap against the cold-FDE exercise this follows it.
    parser.add_argument(
        "--limit",
        type=int,
        default=MAP_FIRST_SCREEN_LIST_MAX,
        help="how many entries to list",
    )
    parser.add_argument("--json", action="store_true", help="emit the result as JSON")
    parser.add_argument("--self-test", action="store_true", help="prove the evaluator reports")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.labels is not None:
        labels_path = args.labels
    elif args.fixture:
        labels_path = LABELS_DIR / f"{args.fixture}.identities.json"
    else:
        parser.error("one of --fixture, --labels or --self-test is required")

    if not labels_path.exists():
        print(f"VIOLATION: no labeled identity set at {labels_path}")
        return 1
    if not args.surface.exists():
        print(
            f"VIOLATION: no run artifact at {args.surface}. Run `adopt map` against the "
            "fixture first; scoring an absent run would report recall 0.0 and blame "
            "extraction for a missing file."
        )
        return 1

    labels = load_labels(labels_path)
    payload = json.loads(args.surface.read_text(encoding="utf-8"))
    result = compare(labels, facts_of(payload))

    if args.json:
        print(
            json.dumps(
                {
                    "fixture": args.fixture or labels_path.stem,
                    "recall": round(result.recall, 6),
                    "precision": round(result.precision, 6),
                    "deterministic_share": round(result.deterministic_share, 6),
                    "labeled": len(result.matched) + len(result.missing),
                    "matched": len(result.matched),
                    "missing": [identity.render() for identity in result.missing],
                    "unlabeled": [identity.render() for identity in result.spurious],
                },
                indent=2,
            )
        )
    else:
        report(result, name=args.fixture or labels_path.stem, limit=args.limit)

    failed = False
    if args.min_recall is not None and result.recall < args.min_recall:
        print(f"VIOLATION: recall {result.recall:.3f} is below {args.min_recall:.3f}")
        failed = True
    if (
        args.min_deterministic_share is not None
        and result.deterministic_share < args.min_deterministic_share
    ):
        print(
            f"VIOLATION: deterministic share {result.deterministic_share:.3f} is below "
            f"{args.min_deterministic_share:.3f} -- `01` §6 M2 and ADR-0.1's reversal trigger"
        )
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
