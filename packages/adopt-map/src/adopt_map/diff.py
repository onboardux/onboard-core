"""The five-class cascade -- v6.1 §6 Build 6, and the guards that keep it honest.

**Pure.** Stored state and observed state in, a `DiffOutcome` out; no store, no
clock, no id. That is what lets Build 8 run this same function server-side
against a Postgres realization: the judgement travels because it touches
nothing. Everything that writes is the caller's, and everything that decides is
here.

## The cascade

Four steps, matching `decided_by`'s four values, evaluated per referent:

| Step | Condition | Class |
|---|---|---|
| 1 | a live identity this run did not see, and could not pair | `BINDING_DEAD` |
| 2 | paired with an appeared referent by digest | `BINDING_MOVED` |
| 3 | same URI, digest differs **at the same extractor version** | `BINDING_INTACT_SEMANTICS_CHANGED` |
| 4 | an observed URI with no stored identity | `UNBOUND_NEW` |
| 4 | a walked file whose content changed while every identity in it kept its digest | `BINDING_INTACT_RENDER_ONLY` |

Steps 1 and 2 are decided by what `detect_moves` already concluded -- this module
does not re-pair, because two pairing implementations would eventually disagree
and the disagreement would be a permanent false alias.

## Absence is not death, and this is where that is enforced

`adopt_map.moves` reports absence and refuses to write it, saying retirement
"belongs to Build 6". It does -- but Build 6 does not get to be careless with
it either. A referent can be missing from a run for four reasons and only one of
them is death:

* **its extractor crashed** -- caught, recorded `failed`, and its identities are
  exempt. A broken extractor that retired its own inventory is the worst
  available outcome: the retirement is append-only, and every bound piece of
  knowledge stales at once.
* **its pack did not run** -- `--packs generic` on a web system must not kill
  every endpoint. Exempt by extractor membership.
* **its file was skipped** -- the walk's size cap means the file was never read.
  Exempt by path.
* **it is genuinely gone** -- the remaining case, and the only one that is
  `BINDING_DEAD`.

Each exemption is reported rather than silently applied, because "we did not
look" is a fact a reviewer needs and a silent exemption is indistinguishable
from a clean run.

## The version fence (H5)

`attribute_digest` mixes the extractor version into the digest, so across an
upgrade **every** digest differs by construction. Comparing across versions
would therefore report every identity as semantically changed the day an
extractor is improved -- a change storm caused entirely by the instrument. When
versions differ the referent is **re-baselined**: the new digest is recorded, no
event is produced, and the run reports "instrument changed, system not
re-judged".
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from adopt_map.moves import MoveOutcome, ObservedIdentity, StoredIdentity

__all__ = [
    "CLASS_DEAD",
    "CLASS_MOVED",
    "CLASS_NEW",
    "CLASS_RENDER_ONLY",
    "CLASS_SEMANTICS",
    "STEP_DEAD",
    "STEP_MOVED",
    "STEP_NEW",
    "STEP_SEMANTICS",
    "ChangeEntry",
    "DiffOutcome",
    "Exemption",
    "FileDelta",
    "Rebaseline",
    "compute",
]

#: The manifest's `impact_class` values. v6.1's prose spells them `BINDING-DEAD`
#: and `SEMANTICS-CHANGED`; the machine-gated enum is the authority (§0
#: precedence) and the CLI renders the short forms.
CLASS_DEAD: Final[str] = "BINDING_DEAD"
CLASS_MOVED: Final[str] = "BINDING_MOVED"
CLASS_SEMANTICS: Final[str] = "BINDING_INTACT_SEMANTICS_CHANGED"
CLASS_NEW: Final[str] = "UNBOUND_NEW"
CLASS_RENDER_ONLY: Final[str] = "BINDING_INTACT_RENDER_ONLY"

#: `decided_by`. The cascade step that reached the conclusion, so a reader can
#: tell "we could not find it" from "we found it and it differs".
STEP_DEAD: Final[str] = "cascade_step_1"
STEP_MOVED: Final[str] = "cascade_step_2"
STEP_SEMANTICS: Final[str] = "cascade_step_3"
STEP_NEW: Final[str] = "cascade_step_4"

_INACTIVE: Final[frozenset[str]] = frozenset({"moved", "dead"})


@dataclass(frozen=True, slots=True)
class FileDelta:
    """What the file-content snapshot says changed since the last refresh.

    `available` is `False` on the first refresh of a store, or when the annex
    was lost. RENDER-ONLY is then **not reported at all** rather than reported
    as empty: "no cosmetic changes" and "we could not tell" are different
    statements, and printing the first when the second is true is how a reader
    learns to trust a number that was never measured.
    """

    changed_paths: frozenset[str] = frozenset()
    available: bool = True


@dataclass(frozen=True, slots=True)
class ChangeEntry:
    """One classified referent: what it is, what happened, and the evidence.

    `identity_id` is `None` for `UNBOUND_NEW`, whose identity the caller is
    about to create, and for RENDER-ONLY entries that name a file rather than a
    referent. The caller resolves those before recording, because
    `classification.identity_id` is NOT NULL -- and an entry that could invent
    an id here would be a diff writing to a store.
    """

    uri: str
    impact_class: str
    decided_by: str
    evidence: str
    identity_id: str | None = None
    #: For a move: where the referent went. For a semantics change: the digest
    #: now observed, which the caller records so the next run compares against it.
    digest: str | None = None
    successor_uri: str | None = None
    extractor: str | None = None
    extractor_version: str | None = None
    source_path: str | None = None


@dataclass(frozen=True, slots=True)
class Rebaseline:
    """A referent whose extractor version changed: re-recorded, never judged."""

    uri: str
    identity_id: str
    from_version: str | None
    to_version: str | None
    digest: str
    extractor: str | None = None
    source_path: str | None = None


@dataclass(frozen=True, slots=True)
class Exemption:
    """A referent that was absent but not examined, and why. Reported, never written."""

    uri: str
    identity_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class DiffOutcome:
    """Everything one refresh concluded. The caller writes; this decides."""

    changes: tuple[ChangeEntry, ...] = ()
    rebaselines: tuple[Rebaseline, ...] = ()
    exemptions: tuple[Exemption, ...] = ()
    #: Reported by `detect_moves` and carried through: several referents share a
    #: digest, so no pairing was made. Not an error and not a change -- the tool
    #: declining to guess, which a reader has to be told about.
    ambiguous: tuple[tuple[str, ...], ...] = ()
    render_only_available: bool = True

    @property
    def actionable(self) -> tuple[ChangeEntry, ...]:
        """Everything but RENDER-ONLY -- the classes that name a referent."""
        return tuple(entry for entry in self.changes if entry.impact_class != CLASS_RENDER_ONLY)

    def by_class(self) -> Mapping[str, int]:
        """Counts per class, every class present as a key only if non-zero."""
        counts: dict[str, int] = {}
        for entry in self.changes:
            counts[entry.impact_class] = counts.get(entry.impact_class, 0) + 1
        return dict(sorted(counts.items()))


def compute(
    *,
    stored: Sequence[StoredIdentity],
    observed: Iterable[ObservedIdentity],
    moves: MoveOutcome,
    file_delta: FileDelta | None = None,
    failed_extractors: Iterable[str] = (),
    ran_extractors: Iterable[str] | None = None,
    oversized_paths: Iterable[str] = (),
) -> DiffOutcome:
    """Classify one refresh run. Pure.

    Args:
        stored: Scope identities **as they were before the run**, each carrying
            the digest, extractor and extractor version of its latest
            digest-bearing revision.
        observed: What this run saw.
        moves: `detect_moves`' conclusion for this run. Its pairs become step 2
            and its unpaired absences become step-1 candidates.
        file_delta: The content snapshot's verdict. `None` means unavailable.
        failed_extractors: Names of extractors that raised this run. Their
            identities are exempt from death.
        ran_extractors: Names of extractors that ran at all. `None` means "every
            extractor ran", which is the honest reading only when no pack was
            filtered; the caller passes the real set.
        oversized_paths: Repo-relative paths the walk skipped for size.

    Returns:
        A `DiffOutcome`. Ordering is total and declared: entries are sorted by
        (class, URI), so two runs over one tree produce the same batch order and
        a reviewer's queue does not reshuffle between sessions.
    """
    seen = {entry.uri: entry for entry in observed}
    stored_by_uri = {entry.uri: entry for entry in stored}
    delta = file_delta if file_delta is not None else FileDelta(available=False)

    failed = frozenset(failed_extractors)
    ran = None if ran_extractors is None else frozenset(ran_extractors)
    oversized = frozenset(oversized_paths)
    moved_ids = {candidate.identity_id for candidate in moves.moves}

    changes: list[ChangeEntry] = []
    rebaselines: list[Rebaseline] = []
    exemptions: list[Exemption] = []

    # -- step 2: moves, taken from detection rather than re-derived ----------
    for candidate in moves.moves:
        source = stored_by_uri.get(candidate.from_uri)
        changes.append(
            ChangeEntry(
                uri=candidate.from_uri,
                impact_class=CLASS_MOVED,
                decided_by=STEP_MOVED,
                evidence=f"referent moved to {candidate.to.uri} (matched by attribute digest)",
                identity_id=candidate.identity_id,
                digest=candidate.to.digest,
                successor_uri=candidate.to.uri,
                extractor=source.extractor if source else None,
                extractor_version=source.extractor_version if source else None,
                source_path=source.source_path if source else None,
            )
        )

    # -- steps 1 and 3: what the store already knew about ---------------------
    for entry in stored:
        if entry.status in _INACTIVE or entry.identity_id in moved_ids:
            continue

        arrival = seen.get(entry.uri)
        if arrival is None:
            exemption = _exemption_for(entry, failed=failed, ran=ran, oversized=oversized)
            if exemption is not None:
                exemptions.append(exemption)
                continue
            changes.append(
                ChangeEntry(
                    uri=entry.uri,
                    impact_class=CLASS_DEAD,
                    decided_by=STEP_DEAD,
                    evidence="the referent was looked for and not found, and no appeared "
                    "referent shares its attribute digest",
                    identity_id=entry.identity_id,
                    extractor=entry.extractor,
                    extractor_version=entry.extractor_version,
                    source_path=entry.source_path,
                )
            )
            continue

        if entry.digest is None:
            # Observed, but nothing to compare against: record the digest so the
            # next run can. Not a change -- claiming one would mean every
            # identity written before digests existed changed the day we started
            # recording them.
            rebaselines.append(
                Rebaseline(
                    uri=entry.uri,
                    identity_id=entry.identity_id,
                    from_version=None,
                    to_version=arrival.extractor_version,
                    digest=arrival.digest,
                    extractor=arrival.extractor,
                    source_path=arrival.source_path,
                )
            )
            continue

        if _versions_differ(entry.extractor_version, arrival.extractor_version):
            # The fence. Every digest differs across an upgrade by construction,
            # so this is the one comparison that must never be made.
            rebaselines.append(
                Rebaseline(
                    uri=entry.uri,
                    identity_id=entry.identity_id,
                    from_version=entry.extractor_version,
                    to_version=arrival.extractor_version,
                    digest=arrival.digest,
                    extractor=arrival.extractor,
                    source_path=arrival.source_path,
                )
            )
            continue

        if arrival.digest != entry.digest:
            changes.append(
                ChangeEntry(
                    uri=entry.uri,
                    impact_class=CLASS_SEMANTICS,
                    decided_by=STEP_SEMANTICS,
                    evidence=f"attribute digest changed at extractor version "
                    f"{arrival.extractor_version or 'unknown'}",
                    identity_id=entry.identity_id,
                    digest=arrival.digest,
                    extractor=arrival.extractor,
                    extractor_version=arrival.extractor_version,
                    source_path=arrival.source_path,
                )
            )

    # -- step 4a: referents this run saw for the first time -------------------
    for uri, arrival in seen.items():
        if uri in stored_by_uri:
            continue
        if any(candidate.to.uri == uri for candidate in moves.moves):
            # Already accounted for as the destination of a move; reporting it
            # again as new would double-count one referent under two classes.
            continue
        changes.append(
            ChangeEntry(
                uri=uri,
                impact_class=CLASS_NEW,
                decided_by=STEP_NEW,
                evidence="observed for the first time; no knowledge covers it yet",
                digest=arrival.digest,
                extractor=arrival.extractor,
                extractor_version=arrival.extractor_version,
                source_path=arrival.source_path,
            )
        )

    # -- step 4b: mapped territory that changed without changing meaning ------
    #
    # **Reported per identity, not per file**, because the class is spelled
    # `BINDING_INTACT_RENDER_ONLY`: it is a statement that *this referent's*
    # binding survived an edit to the file it lives in, and `classification`
    # requires an identity to say it about. A file-shaped entry would have
    # nowhere to be recorded and would leave the one class v6.1 calls
    # "informational" as the only class with no row -- silent, in the build
    # whose promise is that nothing is.
    if delta.available:
        semantic_paths = {
            entry.source_path for entry in changes if entry.source_path is not None
        } | {entry.source_path for entry in rebaselines if entry.source_path is not None}
        by_path: dict[str, list[StoredIdentity]] = {}
        for entry in stored:
            if entry.source_path is not None and entry.status not in _INACTIVE:
                by_path.setdefault(entry.source_path, []).append(entry)
        for path in sorted(delta.changed_paths):
            if path in semantic_paths or path not in by_path:
                continue
            for entry in sorted(by_path[path], key=lambda row: row.uri):
                if entry.uri not in seen:
                    # Absent this run: it is a death, an exemption or a move, and
                    # each has already been decided above. Calling it cosmetic
                    # too would classify one referent twice in one run, which the
                    # UNIQUE index on (event, identity) would refuse anyway.
                    continue
                changes.append(
                    ChangeEntry(
                        uri=entry.uri,
                        impact_class=CLASS_RENDER_ONLY,
                        decided_by=STEP_NEW,
                        evidence=f"{path} changed; this referent's attribute digest did not",
                        identity_id=entry.identity_id,
                        source_path=path,
                    )
                )

    return DiffOutcome(
        changes=tuple(sorted(changes, key=lambda entry: (entry.impact_class, entry.uri))),
        rebaselines=tuple(sorted(rebaselines, key=lambda entry: entry.uri)),
        exemptions=tuple(sorted(exemptions, key=lambda entry: entry.uri)),
        ambiguous=moves.ambiguous,
        render_only_available=delta.available,
    )


def _versions_differ(stored: str | None, observed: str | None) -> bool:
    """Whether the fence should fire.

    Two unknowns are **not** treated as differing: a store whose revisions
    predate version recording would otherwise re-baseline on every run and never
    compare anything, which is a diff that silently stops working.
    """
    if stored is None and observed is None:
        return False
    return stored != observed


def _exemption_for(
    entry: StoredIdentity,
    *,
    failed: frozenset[str],
    ran: frozenset[str] | None,
    oversized: frozenset[str],
) -> Exemption | None:
    """Why this absent referent was not examined, or `None` if it truly was.

    Ordered most-specific first so the reported reason is the most informative
    one: a crashed extractor explains an absence better than "its pack ran".
    """
    if entry.extractor is not None and entry.extractor in failed:
        return Exemption(
            uri=entry.uri,
            identity_id=entry.identity_id,
            reason=f"extractor {entry.extractor!r} failed this run, so this referent was "
            "never looked for",
        )
    if ran is not None and entry.extractor is not None and entry.extractor not in ran:
        return Exemption(
            uri=entry.uri,
            identity_id=entry.identity_id,
            reason=f"extractor {entry.extractor!r} did not run this time (pack not selected)",
        )
    if entry.source_path is not None and entry.source_path in oversized:
        return Exemption(
            uri=entry.uri,
            identity_id=entry.identity_id,
            reason=f"{entry.source_path} exceeded the read cap and was skipped by the walk",
        )
    if entry.extractor is None and ran is not None:
        # No recorded extractor and a filtered run: we cannot tell whether the
        # thing that would have found it ran. Exempt, because a death we cannot
        # justify is a death we must not write.
        return Exemption(
            uri=entry.uri,
            identity_id=entry.identity_id,
            reason="no extractor recorded on its latest observation, so its absence "
            "cannot be attributed to a look that happened",
        )
    return None
