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

**`01` §6 M8 is a *subset* recall, and this tool could not express it (B1-CR-73).**
M8 is *"outside-VCS recall vs the labeled AI fixture, >= 0.90"*, and `05` S1.5's
validation line calls this script for it -- but overall recall over every labeled
identity is a different number, and on a fixture where eight of forty-two
identities live outside version control it can sit at 0.98 while every one of the
eight is missed. So a labeled identity may carry `outside_vcs: true`, a
`surface.json` fact already carries the flag (`02` §9.2), and the report prints
the subset beside the whole whenever the labeled set declares one.

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

from adopt_const import MAP_FIRST_SCREEN_LIST_MAX, MAP_OUTSIDE_VCS_RECALL_FLOOR

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
class Observation:
    """One fact a run emitted, reduced to what scoring needs."""

    identity: Identity
    method: str
    outside_vcs: bool


@dataclass(frozen=True)
class Labels:
    """A labeled identity set, and the outside-VCS ground truth inside it."""

    identities: tuple[Identity, ...]
    outside_vcs: frozenset[Identity]


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


def load_labels(path: Path) -> Labels:
    """The labeled identity set, as scope-independent triples."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    identities = payload.get("identities")
    if not isinstance(identities, list):
        raise ValueError(f"{path} carries no `identities` list")
    parsed = tuple(
        (
            Identity(
                identity_kind=str(item["identity_kind"]),
                namespace=str(item.get("namespace") or ""),
                local_key=str(item["local_key"]),
            ),
            bool(item.get("outside_vcs", False)),
        )
        for item in identities
    )
    return Labels(
        identities=tuple(identity for identity, _flag in parsed),
        outside_vcs=frozenset(identity for identity, flag in parsed if flag),
    )


def facts_of(payload: Mapping[str, Any]) -> tuple[Observation, ...]:
    """`(identity, evidence method)` for every fact in a `surface.json`.

    The key is recovered by parsing the fact's URI rather than read from a field,
    because `02` §9.2's `facts[]` carries `identity_uri` and no `local_key` --
    and parsing it through Build 0's own `parse_uri` means this gate agrees with
    the minting rules by construction rather than by a second implementation of
    the escaping.
    """
    from adopt_identity import parse_uri

    found: list[Observation] = []
    for fact in payload.get("facts", []):
        uri = fact.get("identity_uri")
        if not isinstance(uri, str):
            continue
        parsed = parse_uri(uri)
        found.append(
            Observation(
                identity=Identity(
                    identity_kind=parsed.kind,
                    namespace=parsed.namespace or "",
                    local_key="/".join(parsed.key),
                ),
                method=str(fact.get("method") or ""),
                outside_vcs=bool(fact.get("outside_vcs", False)),
            )
        )
    return tuple(found)


def compare(labels: Iterable[Identity], facts: Iterable[Observation]) -> Result:
    labeled = set(labels)
    observed = list(facts)
    seen = {item.identity for item in observed}
    deterministic = sum(1 for item in observed if item.method in DETERMINISTIC_METHODS)
    return Result(
        matched=tuple(sorted(labeled & seen, key=Identity.render)),
        missing=tuple(sorted(labeled - seen, key=Identity.render)),
        spurious=tuple(sorted(seen - labeled, key=Identity.render)),
        deterministic_facts=deterministic,
        total_facts=len(observed),
    )


def outside_vcs_recall(labels: Labels, facts: Iterable[Observation]) -> Result | None:
    """`01` §6 **M8**: recall over the outside-VCS identities alone.

    `None` when the labeled set declares none -- a fixture with nothing outside
    version control has no M8 to report, and printing `0.000` for it would look
    like a failure of extraction rather than an absence of subject.

    **Scored against the facts that carry the flag**, not against every fact that
    matched: an identity recovered *without* its `outside_vcs` marker is a miss
    for this metric, because the whole point of M8 is the sentence on the first
    screen, and a fact that reaches the map unflagged never appears in it.
    """
    if not labels.outside_vcs:
        return None
    flagged = tuple(item for item in facts if item.outside_vcs)
    return compare(labels.outside_vcs, flagged)


def report(result: Result, *, name: str, limit: int, m8: Result | None = None) -> None:
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
    if m8 is not None:
        print(
            f"  M8 ovcs    {m8.recall:.3f}  "
            f"({len(m8.matched)} of {len(m8.matched) + len(m8.missing)} labeled outside-VCS)"
        )
    for title, entries in (("missing", result.missing), ("unlabeled", result.spurious)):
        if not entries:
            continue
        print(f"  {title}:")
        for identity in entries[:limit]:
            print(f"    {identity.render()}")
        if len(entries) > limit:
            print(f"    ... and {len(entries) - limit} more")
    if m8 is not None and m8.missing:
        print("  missing outside-VCS:")
        for identity in m8.missing[:limit]:
            print(f"    {identity.render()}")


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
    outside = Identity("prompt", "console", "greeting")
    label_set = Labels(identities=(*labels, outside), outside_vcs=frozenset({outside}))

    complete = [Observation(identity, "grammar", False) for identity in labels]
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
        labels,
        [*complete, Observation(Identity("endpoint", "http", "GET /invented"), "regex", False)],
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

    # M8's own direction. A run that recovers every ordinary identity and misses
    # the one outside version control scores 0.75 overall and **0.0** on M8, and
    # the whole reason B1-CR-73 added the subset is that the first number hides
    # the second.
    m8_blind = outside_vcs_recall(label_set, complete)
    if m8_blind is None or m8_blind.recall != 0.0:
        failures.append("a missed outside-VCS identity was NOT reported by the M8 subset")
    else:
        overall = compare(label_set.identities, complete)
        print(
            f"self-test: missed outside-VCS identity -> overall recall "
            f"{overall.recall:.3f} but M8 {m8_blind.recall:.3f} ->"
        )

    m8_green = outside_vcs_recall(label_set, [*complete, Observation(outside, "grammar", True)])
    if m8_green is None or m8_green.recall != 1.0:
        failures.append("a flagged outside-VCS fact did NOT satisfy the M8 subset")
    else:
        print("self-test: a flagged outside-VCS fact scores M8 1.000 ->")

    # The flag itself is the measurement. An identity recovered without it never
    # reaches the first screen, so it is a miss rather than a match.
    m8_unflagged = outside_vcs_recall(
        label_set, [*complete, Observation(outside, "grammar", False)]
    )
    if m8_unflagged is None or m8_unflagged.recall != 0.0:
        failures.append("an UNFLAGGED outside-VCS fact was counted as recovered")
    else:
        print("self-test: an unflagged outside-VCS fact is a miss, not a match ->")

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
    print("label-eval --self-test: OK (8/8 checks)")
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
    # Defaulted from the constant rather than from a literal here: `01` §6 M8 and
    # `01` §9's flip trigger are one number, and a gate that made the operator
    # retype it is a gate that measures whatever they typed.
    parser.add_argument(
        "--min-outside-vcs-recall",
        type=float,
        nargs="?",
        const=MAP_OUTSIDE_VCS_RECALL_FLOOR,
        default=None,
        help=(
            "fail below this recall over the labeled outside-VCS subset (`01` §6 M8); "
            f"bare flag uses MAP_OUTSIDE_VCS_RECALL_FLOOR ({MAP_OUTSIDE_VCS_RECALL_FLOOR})"
        ),
    )
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
    observations = facts_of(payload)
    result = compare(labels.identities, observations)
    m8 = outside_vcs_recall(labels, observations)

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
                    "outside_vcs_recall": None if m8 is None else round(m8.recall, 6),
                    "outside_vcs_labeled": None
                    if m8 is None
                    else len(m8.matched) + len(m8.missing),
                    "outside_vcs_missing": (
                        [] if m8 is None else [identity.render() for identity in m8.missing]
                    ),
                },
                indent=2,
            )
        )
    else:
        report(result, name=args.fixture or labels_path.stem, limit=args.limit, m8=m8)

    failed = False
    if args.min_outside_vcs_recall is not None:
        if m8 is None:
            print(
                "VIOLATION: --min-outside-vcs-recall was asked for and this labeled set "
                "declares no outside-VCS identity. `01` §6 M8 has no subject here, and "
                "scoring it 1.0 would be a metric about an empty set."
            )
            failed = True
        elif m8.recall < args.min_outside_vcs_recall:
            print(
                f"VIOLATION: outside-VCS recall {m8.recall:.3f} is below "
                f"{args.min_outside_vcs_recall:.3f} -- `01` §6 M8"
            )
            failed = True
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
