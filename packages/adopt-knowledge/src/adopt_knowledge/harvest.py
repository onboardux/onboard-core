"""`adopt harvest` -- local history becomes decision candidates, never canon.

v6.1 §6 Build 2 F7: deterministic mining of **locally present** history. Six
signals, each a deterministic statement about a commit rather than a judgement
about it, and each one a reason a person might want to look:

| Signal | What qualifies | Why it is a decision |
|---|---|---|
| `rationale` | the message has a body beyond its subject | somebody wrote down *why* |
| `merge` | more than one parent | an integration decision |
| `revert` | `Revert "..."` or `This reverts commit` | the strongest signal there is: an earlier choice was judged wrong |
| `dependency` | touched a dependency manifest | what the system is built on changed |
| `config` | touched a configuration file | how the system is run changed |
| `decision_record` | touched an ADR directory or a CHANGELOG | the repository's own decision log moved |

A commit needs **one** signal to become a candidate, and produces **one**
candidate however many it has: a commit is one decision, and the signals are the
reasons to notice it, not separate things to review.

**Everything mined lands `unverified`** (plan D2, F6). This is the opposite of
ingest and the asymmetry is the point: a document is a human's own prose
transcribed, a candidate is a machine's inference about what a commit meant.
`recompute_coverage` counts neither an unverified item nor a binding to one, so
a harvest of four hundred commits moves the coverage number by exactly zero
until a person confirms something.

**Mined and authored never merge.** The first revision is `artifact_observed`
with a `commit` provenance row citing the sha. Confirming or editing in review
*appends* -- `human_confirmed`, with `human` provenance -- and the superseded
revision keeps its commit citation forever. There is no path here that lets
something a person wrote acquire `artifact_observed`, and
`test_harvest_provenance` is the assertion that it stays that way.

**Bindings are real, and that is deliberate (plan D4).** A commit's touched file
resolving to exactly one identity is structural justification, so the binding is
created at harvest with the same standing as an ingest structural match.
Coverage honesty comes from the verification rule, not from withholding the
binding -- which means a confirmed candidate is covered immediately, with no
second write and no second rule.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from adopt_knowledge.documents import DEFAULT_AUDIENCE
from adopt_knowledge.gitlog import Commit
from adopt_knowledge.matchers import IdentityView, Match, path_matches
from adopt_knowledge.ports import BindingWriter, KnowledgeWriter, ReviewWriter
from adopt_model._enums import AuthorityClass, ItemKind, SourceType, Verification
from adopt_obs import get_logger
from adopt_scope import Scope

__all__ = [
    "CANDIDATE_AUDIENCE",
    "CONFIG_SUFFIXES",
    "DECISION_RECORD_SEGMENTS",
    "DEPENDENCY_MANIFESTS",
    "HARVEST_EXTRACTOR",
    "HARVEST_EXTRACTOR_VERSION",
    "NOT_CONFIG",
    "Candidate",
    "HarvestReport",
    "Signal",
    "batch_key",
    "decision_record_titles",
    "mine",
    "run_harvest",
]

_log = get_logger("adopt_knowledge")

#: What `binding_revision.extractor` records for a binding a commit justified.
#: Its own value rather than reuse of an ingest tier: "a commit touched this
#: file" and "a document names this path" are different evidence, and Build 4's
#: recipe work will want to weigh them differently.
HARVEST_EXTRACTOR: Final[str] = "harvest-structural-path"
HARVEST_EXTRACTOR_VERSION: Final[str] = "1"

#: A mined decision is *why* something is done that way. `procedure` is what
#: ingest defaults to (how it is done), and the two must not collapse: a pack
#: assembled from Build 4's decision appendix would otherwise be full of
#: installation instructions.
CANDIDATE_KIND: Final[ItemKind] = "rationale"
_CANDIDATE_AUTHORITY: Final[AuthorityClass] = "artifact_observed"
_CANDIDATE_VERIFICATION: Final[Verification] = "unverified"
_COMMIT_SOURCE: Final[SourceType] = "commit"
_ADR_SOURCE: Final[SourceType] = "adr"

#: **A candidate is tagged, and it has to be.** `recompute_coverage` input 4
#: refuses to count an item with no `audience_tag` row, so an untagged candidate
#: could be mined, bound and confirmed and still serve nothing -- which would
#: make "confirm it and watch it count" false for the whole harvest population
#: while every row in the store looked correct. A decision read out of a commit
#: is knowledge about code, which is what `technical` names in the vocabulary
#: `documents` declares; ingest defaults to the same value for the same reason.
#: Build 4's pack assembly is where a firm re-audiences anything.
CANDIDATE_AUDIENCE: Final[str] = DEFAULT_AUDIENCE

#: **Build 1's taxonomy, restated rather than imported.** `adopt_knowledge` does
#: not depend on `adopt-map` -- adding that edge for three filenames would pull
#: the whole extractor machinery into the harvest import graph -- so the names
#: are here and `test_harvest_miners` asserts they still match the pack that
#: owns them. A restated list with no alarm on it is drift waiting to happen.
DEPENDENCY_MANIFESTS: Final[frozenset[str]] = frozenset(
    {"pyproject.toml", "package.json", "requirements.txt"}
)
#: `generic.ConfigKeyExtractor`'s rule, same source and same alarm: a `.toml` or
#: `.json` file that is not somebody else's manifest is configuration.
CONFIG_SUFFIXES: Final[frozenset[str]] = frozenset({".toml", ".json"})
NOT_CONFIG: Final[frozenset[str]] = frozenset(
    {"pyproject.toml", "package.json", "package-lock.json", "tsconfig.json", "uv.lock"}
)

#: Path segments that name a repository's own decision log. Segment equality
#: rather than substring: `adr` as a substring matches `quadratic.py`, and a
#: signal that fires on a coincidence is a signal a reviewer learns to ignore.
DECISION_RECORD_SEGMENTS: Final[frozenset[str]] = frozenset({"adr", "adrs", "decisions"})
_CHANGELOG_PREFIX: Final[str] = "CHANGELOG"

#: git's own two spellings of a revert. `^revert\b` case-insensitively also
#: catches the `revert: ...` convention some teams write by hand.
_REVERT_SUBJECT: Final[re.Pattern[str]] = re.compile(r"^revert\b", re.IGNORECASE)
_REVERT_BODY: Final[str] = "This reverts commit"

SIGNAL_RATIONALE: Final[str] = "rationale"
SIGNAL_MERGE: Final[str] = "merge"
SIGNAL_REVERT: Final[str] = "revert"
SIGNAL_DEPENDENCY: Final[str] = "dependency"
SIGNAL_CONFIG: Final[str] = "config"
SIGNAL_DECISION_RECORD: Final[str] = "decision_record"


@dataclass(frozen=True, slots=True)
class Signal:
    """One reason a commit qualified, with the evidence for it.

    The evidence is carried rather than a score, for the reason the matchers
    carry it: a reviewer asked to trust a number learns to click confirm, and a
    reviewer shown `pyproject.toml` decides.
    """

    name: str
    evidence: str


@dataclass(frozen=True, slots=True)
class Candidate:
    """One mined decision, before anything is written."""

    sha: str
    title: str
    body_md: str
    authored_at: str
    signals: tuple[Signal, ...]
    #: ADR / CHANGELOG paths the commit touched. Each becomes an `adr`
    #: provenance row beside the `commit` one.
    decision_records: tuple[str, ...]
    files: tuple[str, ...]

    @property
    def signal_names(self) -> tuple[str, ...]:
        return tuple(signal.name for signal in self.signals)


@dataclass(slots=True)
class HarvestReport:
    """The run, as the CLI renders it."""

    commits_read: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    known: list[str] = field(default_factory=list)
    bound: list[Match] = field(default_factory=list)
    ambiguous_paths: tuple[str, ...] = ()
    review_batch_id: str | None = None
    review_item_ids: tuple[str, ...] = ()


def _segments(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.replace("\\", "/").split("/") if part)


def _basename(path: str) -> str:
    return _segments(path)[-1] if _segments(path) else path


def is_dependency_manifest(path: str) -> bool:
    return _basename(path) in DEPENDENCY_MANIFESTS


def is_config_file(path: str) -> bool:
    name = _basename(path)
    if name in NOT_CONFIG or name in DEPENDENCY_MANIFESTS:
        return False
    return any(name.endswith(suffix) for suffix in CONFIG_SUFFIXES)


def is_decision_record(path: str) -> bool:
    parts = _segments(path)
    if any(part.lower() in DECISION_RECORD_SEGMENTS for part in parts[:-1]):
        return True
    return _basename(path).upper().startswith(_CHANGELOG_PREFIX)


def signals_of(commit: Commit) -> tuple[Signal, ...]:
    """Every signal one commit carries, in a fixed order.

    Ordered by how strongly each speaks to a decision having been made, so a
    reviewer scanning a queue reads the sharpest reason first and a report is
    the same report on two machines.
    """
    found: list[Signal] = []

    if _REVERT_SUBJECT.search(commit.subject) or _REVERT_BODY in commit.body:
        found.append(Signal(SIGNAL_REVERT, commit.subject))
    if commit.is_merge:
        found.append(Signal(SIGNAL_MERGE, f"{len(commit.parents)} parents"))
    for path in commit.files:
        if is_decision_record(path):
            found.append(Signal(SIGNAL_DECISION_RECORD, path))
    for path in commit.files:
        if is_dependency_manifest(path):
            found.append(Signal(SIGNAL_DEPENDENCY, path))
    for path in commit.files:
        if is_config_file(path):
            found.append(Signal(SIGNAL_CONFIG, path))
    if commit.body.strip():
        found.append(Signal(SIGNAL_RATIONALE, commit.subject))

    return tuple(found)


def decision_records_of(commit: Commit) -> tuple[str, ...]:
    return tuple(path for path in commit.files if is_decision_record(path))


def mine(
    commits: Sequence[Commit],
    *,
    decision_titles: Mapping[str, str] | None = None,
) -> tuple[Candidate, ...]:
    """Commits in, candidates out. **Pure**, so the miners can be driven directly.

    Args:
        decision_titles: `path -> heading` for ADR documents the commit touched,
            resolved by the caller from the working tree. A decision record
            titles itself far better than the commit that added it does --
            "Add ADR 0007" versus "Use Postgres for the primary store" -- and
            the title is what a reviewer scans. Absent or unreadable, the
            subject stands.
    """
    titles = decision_titles or {}
    candidates: list[Candidate] = []

    for commit in commits:
        signals = signals_of(commit)
        if not signals:
            continue
        records = decision_records_of(commit)
        candidates.append(
            Candidate(
                sha=commit.sha,
                title=_title_for(commit, records, titles),
                body_md=_body_for(commit),
                authored_at=commit.authored_at,
                signals=signals,
                decision_records=records,
                files=commit.files,
            )
        )

    return tuple(candidates)


def _title_for(commit: Commit, records: Sequence[str], titles: Mapping[str, str]) -> str:
    for path in records:
        heading = titles.get(path, "").strip()
        if heading:
            return heading
    return commit.subject.strip() or commit.sha[:12]


def _body_for(commit: Commit) -> str:
    """Subject then body, and **nothing this module invented**.

    No signal summary, no file list, no "harvested by" banner. The revision is
    `artifact_observed`, which is a claim that this text was read out of the
    client's own artifact -- and a body carrying a sentence we wrote would make
    that claim false in the one field a later build would trust it in. The
    evidence lives in `provenance` and in the bindings, where a reviewer can see
    it without it becoming knowledge.
    """
    body = commit.body.strip()
    subject = commit.subject.strip()
    return f"{subject}\n\n{body}\n" if body else f"{subject}\n"


def decision_record_titles(root: Path, paths: Sequence[str]) -> dict[str, str]:
    """`path -> first Markdown heading` for decision records still on disk.

    Reads the **working tree**, never `git show`. A file added inside the range
    and not later deleted is at HEAD, which is the overwhelming case and costs
    no subprocess; one that was deleted has no heading to offer and falls back
    to the commit subject. Trading an exact answer for a free one is the right
    way round here: the title is a label on a review item, not evidence.
    """
    titles: dict[str, str] = {}
    for path in sorted(set(paths)):
        candidate = root / path
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
                if heading:
                    titles[path] = heading
                    break
    return titles


def batch_key(since: str, head: str) -> str:
    """The queue's coalescing key for one harvest run.

    Named after the range so two harvests of two ranges are two sittings, and a
    re-harvest of the same range -- which creates nothing -- cannot be confused
    with a new one.
    """
    return f"harvest:{since}..{head}"


def run_harvest(
    candidates: Sequence[Candidate],
    *,
    scope: Scope,
    identities: Sequence[IdentityView],
    known: Mapping[str, str],
    knowledge: KnowledgeWriter,
    bindings: BindingWriter,
    reviews: ReviewWriter,
    key: str,
    bound_pairs: frozenset[tuple[str, str]] = frozenset(),
    actor_id: str | None = None,
) -> HarvestReport:
    """Write the candidates, bind what their files resolve to, queue them.

    Args:
        known: `sha -> item_id` for candidates a previous harvest already wrote,
            read back from the `commit` provenance rows. **This is idempotence**:
            a second harvest of one range writes no item, no revision and no
            review item.
        bound_pairs: `(item_id, identity_id)` pairs that already exist.
            `idx_binding_pair` is UNIQUE and a second create raises
            `REVISION_CHAIN_FORK`, so a re-harvest would otherwise fail rather
            than do nothing.

    Returns:
        A `HarvestReport`. **One batch per run**, as ingest does it and for the
        same reason: a reviewer who harvested a release should sit down once.

    Note:
        A commit already known is still matched against the registry, exactly as
        an unchanged document is at ingest. `adopt map` may have found the
        endpoint since, and a decision that could only ever bind on the day it
        was mined is a decision whose links depend on the order two commands
        were run in.
    """
    report = HarvestReport(commits_read=len(candidates), candidates=list(candidates))
    pending: list[tuple[str, str | None]] = []
    ambiguous: set[str] = set()

    for candidate in candidates:
        existing = known.get(candidate.sha)
        if existing is None:
            item_id, revision_id = knowledge.record(
                scope=scope,
                kind=CANDIDATE_KIND,
                title=candidate.title,
                body_md=candidate.body_md,
                authority_class=_CANDIDATE_AUTHORITY,
                verification=_CANDIDATE_VERIFICATION,
                source_version=candidate.sha,
                actor_id=actor_id,
            )
            knowledge.record_provenance(
                revision_id=revision_id,
                source_type=_COMMIT_SOURCE,
                source_ref=candidate.sha,
            )
            for path in candidate.decision_records:
                knowledge.record_provenance(
                    revision_id=revision_id,
                    source_type=_ADR_SOURCE,
                    source_ref=path,
                )
            knowledge.tag_audience(item_id=item_id, audience=CANDIDATE_AUDIENCE)
            report.created.append(item_id)
            pending.append((item_id, revision_id))
        else:
            item_id = existing
            report.known.append(item_id)

        matched, unresolved = path_matches(candidate.files, identities)
        ambiguous.update(unresolved)
        for match in matched:
            if (item_id, match.identity_id) in bound_pairs:
                continue
            bindings.bind(
                item_id=item_id,
                identity_id=match.identity_id,
                # Load-bearing, like every structural binding: when the
                # identity this decision is about changes, the decision is what
                # should be re-read.
                is_load_bearing=True,
                extractor=HARVEST_EXTRACTOR,
                extractor_version=HARVEST_EXTRACTOR_VERSION,
                actor_id=actor_id,
            )
            report.bound.append(match)

    report.ambiguous_paths = tuple(sorted(ambiguous))

    if pending:
        batch_id, item_ids = reviews.open_batch(
            system_id=str(scope.system.id) if scope.system is not None else "",
            batch_key=key,
            items=pending,
            owner_actor_id=actor_id,
        )
        report.review_batch_id = batch_id
        report.review_item_ids = item_ids

    _log.info(
        "harvest.completed",
        candidates=len(candidates),
        created=len(report.created),
        known=len(report.known),
        bindings=len(report.bound),
    )
    return report
