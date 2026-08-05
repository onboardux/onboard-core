"""The archetype rule sets, loaded from `rules/*.yaml`.

**Rules are data so that adding a signal is a data change** (implementation spec
§4.11). Expressed as Python they would be a scoring function, and every new
framework marker would arrive as a review of control flow rather than of the
claim "this file means this archetype".

**What a rule may say is deliberately small.** A rule matches a path glob, or a
path glob plus a literal substring within the first `DETECT_MAX_SNIFF_BYTES` of
the file. There is no regular expression and no arbitrary predicate, for two
reasons: a rule that can run is a rule that can be slow or non-terminating on a
client tree we have never seen, and PRD F10.1 requires detection from file-tree
heuristics with **no code execution in the target**. A substring match is the
strongest thing that cannot become either.

**Weights are data, not tunables.** `03` §2 owns the numbers that change
behaviour across the product -- thresholds, budgets, limits. A rule's weight is
part of the rule, is meaningless outside it, and moves whenever the rule set is
edited; hoisting fifty of them into the constants table would make that table a
copy of these files with worse locality. `DETECT_CONFIDENCE_MIN` remains a
constant, because it is the one number the *engine* applies to every archetype.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final, get_args

import yaml

from adopt_model._enums import Archetype
from adopt_obs import AdoptError, ErrorCode

__all__ = ["ARCHETYPES", "ArchetypeRules", "Rule", "load_rule_sets", "rules_directory"]

#: Exactly the manifest's `archetype` enum, read from the generated models rather
#: than retyped. Implementation spec §4.11 makes "archetype values are exactly
#: `web|platform|lowcode|data|ai`" an invariant, and a second literal list is the
#: usual way an invariant like that stops being true.
ARCHETYPES: Final[tuple[Archetype, ...]] = tuple(get_args(Archetype))

_RULES_DIRNAME: Final[str] = "rules"
_RULE_KEYS: Final[frozenset[str]] = frozenset({"id", "weight", "path", "contains", "why"})
_REQUIRED_RULE_KEYS: Final[frozenset[str]] = frozenset({"id", "weight", "path", "why"})


def _invalid(message: str, hint: str) -> AdoptError:
    return AdoptError(ErrorCode.MANIFEST_INVALID, message=message, hint=hint)


@dataclass(frozen=True, slots=True)
class Rule:
    """One signal: a path glob, optionally narrowed by a literal substring."""

    id: str
    weight: float
    path: str
    why: str
    contains: str | None = None

    def matches(self, relative_path: str, head: bytes | None) -> bool:
        """Whether this rule fires for one file.

        Args:
            relative_path: POSIX-style path relative to the detection root.
            head: The first `DETECT_MAX_SNIFF_BYTES` of the file, or `None` when
                it was unreadable. An unreadable file **never** fires a
                `contains` rule: "we could not look" is not evidence, and
                treating it as a match would let a permissions error change an
                archetype.
        """
        if not _glob_matches(relative_path, self.path):
            return False
        if self.contains is None:
            return True
        if head is None:
            return False
        return self.contains.encode("utf-8") in head


@cache
def _glob_pattern(pattern: str) -> re.Pattern[str]:
    """Compile one whole-path glob.

    Written out rather than delegated for two reasons. `fnmatch` lets `*` cross a
    `/`, which would make `*/settings.py` match `a/b/c/settings.py` and turn a
    rule about a top-level marker into a rule about anything anywhere.
    `PurePath.full_match` has the semantics we want but arrived in Python 3.13,
    and `03` §1.1 locks 3.12.

    `**` spans any number of segments including none, so `**/models.py` matches
    `models.py` as well as `app/models.py`. `*` and `?` stay inside one segment.
    """
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            parts.append("(?:[^/]+/)*")
            # const-sync: ok -- the length of the token `**/`, not SCHEMA_VERSION.
            index += 3
        elif pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif char == "*":
            parts.append("[^/]*")
            index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(char))
            index += 1
    return re.compile("".join(parts) + r"\Z")


def _glob_matches(relative_path: str, pattern: str) -> bool:
    """Whether a POSIX-style relative path matches a whole-path glob."""
    return _glob_pattern(pattern).match(relative_path) is not None


@dataclass(frozen=True, slots=True)
class ArchetypeRules:
    """Every rule for one archetype.

    There is deliberately no `total_weight` here. Detection scores an
    archetype's share of the weight actually *matched*, not its share of the
    weight available (`detect._score`), so an archetype's own total is not a
    denominator anywhere -- and exposing one would invite a caller to make it
    one, which is the bug that reading gets rid of.
    """

    archetype: Archetype
    rules: tuple[Rule, ...]


def rules_directory() -> Path:
    """Where the rule files live, found from this module rather than the cwd."""
    return Path(__file__).resolve().parent / _RULES_DIRNAME


def _parse_rule(archetype: str, raw: object, seen: set[str]) -> Rule:
    if not isinstance(raw, Mapping):
        raise _invalid(
            f"{archetype}: every rule must be a mapping, found {type(raw).__name__}",
            hint="Each list entry under `rules:` is a mapping with `id`, `weight`, "
            "`path` and `why`.",
        )
    keys = {str(key) for key in raw}
    if unknown := sorted(keys - _RULE_KEYS):
        raise _invalid(
            f"{archetype}: rule carries unknown key(s) {unknown}",
            hint=f"A rule takes exactly {sorted(_RULE_KEYS)}. A misspelled key that "
            "were accepted would be a signal silently never firing.",
        )
    if missing := sorted(_REQUIRED_RULE_KEYS - keys):
        raise _invalid(
            f"{archetype}: rule is missing {missing}",
            hint="`why` is required because a rule nobody can explain is a rule "
            "nobody can review when it misfires on a client tree.",
        )

    rule_id = str(raw["id"])
    if rule_id in seen:
        raise _invalid(
            f"{archetype}: duplicate rule id {rule_id!r}",
            hint="Rule ids appear in `rules_fired` and in the ambiguity report; two "
            "rules with one id make that output unreadable.",
        )
    seen.add(rule_id)

    weight = raw["weight"]
    if not isinstance(weight, int | float) or isinstance(weight, bool) or weight <= 0:
        raise _invalid(
            f"{archetype}: rule {rule_id!r} has a non-positive or non-numeric weight",
            hint="A zero-weight rule contributes nothing but appears in `rules_fired`, "
            "which reads as evidence that did not count.",
        )

    contains = raw.get("contains")
    if contains is not None and (not isinstance(contains, str) or not contains):
        raise _invalid(
            f"{archetype}: rule {rule_id!r} has an empty or non-string `contains`",
            hint="`contains` is a literal substring. An empty one matches every file "
            "that exists, which is not a signal.",
        )

    return Rule(
        id=rule_id,
        weight=float(weight),
        path=str(raw["path"]),
        why=str(raw["why"]),
        contains=contains,
    )


def _parse_file(archetype: Archetype, path: Path) -> ArchetypeRules:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise _invalid(
            f"{path.name}: the rule file must be a mapping",
            hint="Top level is `archetype:` and `rules:`.",
        )
    declared = document.get("archetype")
    if declared != archetype:
        raise _invalid(
            f"{path.name}: declares archetype {declared!r} but is named for {archetype!r}",
            hint="The file name and the declared archetype must agree, so that a rule "
            "set cannot be scored against an archetype it was not written for.",
        )
    raw_rules = document.get("rules")
    if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, str) or not raw_rules:
        raise _invalid(
            f"{path.name}: `rules` must be a non-empty list",
            hint="An archetype with no rules scores 0/0 and can never be detected.",
        )

    seen: set[str] = set()
    rules = tuple(_parse_rule(archetype, raw, seen) for raw in raw_rules)
    return ArchetypeRules(archetype=archetype, rules=rules)


def _load(directory: Path) -> tuple[ArchetypeRules, ...]:
    present = {path.stem for path in directory.glob("*.yaml")}
    if missing := sorted(set(ARCHETYPES) - present):
        raise _invalid(
            f"no rule file for archetype(s) {missing}",
            hint="Every declared archetype needs a rule set. One without rules can "
            "never win, which reads in the field as 'we do not support that' "
            "rather than as a missing file.",
        )
    if extra := sorted(present - set(ARCHETYPES)):
        raise _invalid(
            f"rule file(s) {extra} name no declared archetype",
            hint=f"Archetypes are exactly {list(ARCHETYPES)} (contracts §2.1). A rule "
            "set for anything else would be scored and could never be returned.",
        )
    return tuple(
        _parse_file(archetype, directory / f"{archetype}.yaml") for archetype in ARCHETYPES
    )


@cache
def _load_cached(directory: str) -> tuple[ArchetypeRules, ...]:
    return _load(Path(directory))


def load_rule_sets(directory: Path | None = None) -> tuple[ArchetypeRules, ...]:
    """Every archetype's rules, in `ARCHETYPES` order.

    Ordered rather than mapped because the order is load-bearing: it is the
    tie-break the ranked-score output uses, and a dict ordered by filesystem
    iteration would make two machines disagree about which of two equal scores
    ranks first (PRD N15).

    Raises:
        AdoptError: ``MANIFEST_INVALID`` when a rule file is malformed, names an
            undeclared archetype, or is missing for a declared one.
    """
    return _load_cached(str(directory if directory is not None else rules_directory()))


def needs_content(rule_sets: Sequence[ArchetypeRules]) -> bool:
    """Whether any rule carries `contains`, and the walk must therefore read bytes.

    A rule set of pure path rules -- which is what a narrowly scoped archetype
    tends toward -- makes the walk a `stat`-only pass over the tree. Checking is
    cheaper than reading every candidate file to discover nobody wanted it.
    """
    return any(rule.contains is not None for rule_set in rule_sets for rule in rule_set.rules)
