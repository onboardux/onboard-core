"""The cascade, and the four reasons an absent referent is not a dead one.

`adopt_map.diff.compute` is pure, so these tests drive it directly rather than
through a store: the classification is the thing under test, and a fixture tree
would put a walk, a store and two transactions between the input and the
assertion. The end-to-end proof that real tree edits produce these classes is
invariant #6's e2e; this is where each rule is pinned individually.
"""

import pytest
from adopt_map.diff import (
    CLASS_DEAD,
    CLASS_MOVED,
    CLASS_NEW,
    CLASS_RENDER_ONLY,
    CLASS_SEMANTICS,
    STEP_DEAD,
    STEP_SEMANTICS,
    FileDelta,
    compute,
)
from adopt_map.moves import MoveCandidate, MoveOutcome, ObservedIdentity, StoredIdentity

pytestmark = pytest.mark.unit

_BASE = "onboard-v1://northwind/acme-erp/orders-api/prod"
_ORDERS = f"{_BASE}/endpoint/-/POST %2Fv1%2Forders"
_REFUNDS = f"{_BASE}/endpoint/-/POST %2Fv1%2Frefunds"


def _stored(
    uri: str = _ORDERS,
    *,
    digest: str | None = "sha256:aaa",
    status: str = "active",
    extractor: str | None = "endpoints",
    extractor_version: str | None = "v1",
    source_path: str | None = "src/api.py",
    identity_id: str = "idn_orders",
) -> StoredIdentity:
    return StoredIdentity(
        identity_id=identity_id,
        uri=uri,
        digest=digest,
        status=status,
        extractor_version=extractor_version,
        extractor=extractor,
        source_path=source_path,
    )


def _observed(
    uri: str = _ORDERS,
    *,
    digest: str = "sha256:aaa",
    extractor: str | None = "endpoints",
    extractor_version: str | None = "v1",
    source_path: str | None = "src/api.py",
) -> ObservedIdentity:
    return ObservedIdentity(
        uri=uri,
        kind="endpoint",
        namespace=None,
        key=("POST /v1/orders",),
        digest=digest,
        extractor=extractor,
        extractor_version=extractor_version,
        source_path=source_path,
    )


def _classes(outcome) -> list[str]:  # type: ignore[no-untyped-def]
    return [entry.impact_class for entry in outcome.changes]


# -- the five classes -------------------------------------------------------


def test_an_unchanged_referent_produces_nothing() -> None:
    """**Idempotence, at the unit level.** *Fails when* a re-run manufactures a
    change. *Matters because* refresh's clean-run promise (exit 0, zero rows) is
    what makes a non-clean run mean something; a diff that always reports would
    make the queue noise from day one."""
    outcome = compute(
        stored=[_stored()],
        observed=[_observed()],
        moves=MoveOutcome(),
        ran_extractors=["endpoints"],
    )

    assert outcome.changes == ()
    assert outcome.rebaselines == ()


def test_a_changed_digest_at_the_same_version_is_semantics_changed() -> None:
    """*Fails when* the comparison stops firing. *Matters because* this is the
    build's whole reason to exist -- a real change to a mapped referent that
    nothing else in the system would notice."""
    outcome = compute(
        stored=[_stored()],
        observed=[_observed(digest="sha256:bbb")],
        moves=MoveOutcome(),
        ran_extractors=["endpoints"],
    )

    assert _classes(outcome) == [CLASS_SEMANTICS]
    assert outcome.changes[0].decided_by == STEP_SEMANTICS
    # The new digest travels, so the caller can record it and the next run
    # compares against this observation rather than the original one.
    assert outcome.changes[0].digest == "sha256:bbb"


def test_an_absent_referent_that_was_looked_for_is_dead() -> None:
    """*Fails when* a genuinely deleted referent stops being reported. *Matters
    because* knowledge bound to a deleted endpoint is the rot the product
    exists to catch, and `moves.py` deliberately refuses to write it -- this is
    the code that decides."""
    outcome = compute(
        stored=[_stored()], observed=[], moves=MoveOutcome(), ran_extractors=["endpoints"]
    )

    assert _classes(outcome) == [CLASS_DEAD]
    assert outcome.changes[0].decided_by == STEP_DEAD
    assert outcome.exemptions == ()


def test_a_paired_referent_is_moved_and_its_destination_is_not_also_new() -> None:
    """*Fails when* one move is double-counted as a death plus an arrival, or as
    a move plus a new identity. *Matters because* a reviewer would be asked
    twice about one event, and the second answer would contradict the first."""
    arrival = _observed(uri=_REFUNDS)
    moves = MoveOutcome(
        moves=(MoveCandidate(identity_id="idn_orders", from_uri=_ORDERS, to=arrival),)
    )

    outcome = compute(
        stored=[_stored()], observed=[arrival], moves=moves, ran_extractors=["endpoints"]
    )

    assert _classes(outcome) == [CLASS_MOVED]
    assert outcome.changes[0].successor_uri == _REFUNDS


def test_an_unknown_uri_is_new() -> None:
    """*Fails when* a newly added referent is silent. *Matters because*
    UNBOUND_NEW is what opens a coverage gap: an endpoint nobody documented is
    invisible until the map says it exists."""
    outcome = compute(
        stored=[], observed=[_observed()], moves=MoveOutcome(), ran_extractors=["endpoints"]
    )

    assert _classes(outcome) == [CLASS_NEW]


# -- the comment-only edit, which is the point of H5 ------------------------


def test_a_content_change_with_no_digest_change_is_render_only() -> None:
    """**Invariant #6's negative half, at the unit level.** *Fails when* a
    cosmetic edit is classified as semantic. *Matters because* false staleness
    is the failure that makes reviewers abandon the queue -- and a comment edit
    is the commonest edit there is. *No other instrument catches it* because the
    file genuinely did change: only the digest knows the meaning did not."""
    outcome = compute(
        stored=[_stored()],
        observed=[_observed()],
        moves=MoveOutcome(),
        file_delta=FileDelta(changed_paths=frozenset({"src/api.py"})),
        ran_extractors=["endpoints"],
    )

    assert _classes(outcome) == [CLASS_RENDER_ONLY]
    assert CLASS_SEMANTICS not in _classes(outcome)


def test_a_semantic_change_is_not_also_reported_as_render_only() -> None:
    """*Fails when* one edit produces two entries for one file. *Matters because*
    the reviewer would see the real change buried beside a cosmetic entry about
    the same lines, and the informational group would stop being ignorable."""
    outcome = compute(
        stored=[_stored()],
        observed=[_observed(digest="sha256:bbb")],
        moves=MoveOutcome(),
        file_delta=FileDelta(changed_paths=frozenset({"src/api.py"})),
        ran_extractors=["endpoints"],
    )

    assert _classes(outcome) == [CLASS_SEMANTICS]


def test_render_only_is_withheld_rather_than_reported_empty_when_unavailable() -> None:
    """*Fails when* a missing snapshot reads as "no cosmetic changes". *Matters
    because* the first refresh of every store has no snapshot: reporting zero
    would be a measurement nobody took, and the reader has no way to tell it
    from a real zero."""
    outcome = compute(
        stored=[_stored()], observed=[_observed()], moves=MoveOutcome(), file_delta=None
    )

    assert outcome.render_only_available is False
    assert _classes(outcome) == []


# -- the guards: four reasons absence is not death --------------------------


def test_a_failed_extractor_exempts_its_identities_from_death() -> None:
    """**The guard that matters most.** *Fails when* a crashed extractor retires
    everything it used to find. *Matters because* retirement is append-only and
    stales every bound item at once -- a single broken regex would fabricate the
    largest change event the system can produce. *No other instrument catches
    it* because the run "succeeded": `run_map` catches extractor exceptions by
    design so one failure does not cost the others."""
    outcome = compute(
        stored=[_stored()],
        observed=[],
        moves=MoveOutcome(),
        failed_extractors=["endpoints"],
        ran_extractors=["endpoints"],
    )

    assert outcome.changes == ()
    assert len(outcome.exemptions) == 1
    assert "failed this run" in outcome.exemptions[0].reason


def test_an_extractor_that_did_not_run_exempts_its_identities() -> None:
    """*Fails when* `--packs generic` on a web system retires every endpoint.
    *Matters because* narrowing a run is a routine thing an FDE does, and it
    must never be destructive."""
    outcome = compute(
        stored=[_stored()], observed=[], moves=MoveOutcome(), ran_extractors=["config"]
    )

    assert outcome.changes == ()
    assert "did not run" in outcome.exemptions[0].reason


def test_an_oversized_file_exempts_its_identities() -> None:
    """*Fails when* a file that grew past the read cap takes its identities down
    with it. *Matters because* the walk skipping a file is us not looking, and a
    death recorded from a look that never happened is a lie the store keeps
    forever."""
    outcome = compute(
        stored=[_stored()],
        observed=[],
        moves=MoveOutcome(),
        ran_extractors=["endpoints"],
        oversized_paths=["src/api.py"],
    )

    assert outcome.changes == ()
    assert "read cap" in outcome.exemptions[0].reason


# -- the version fence (H5) -------------------------------------------------


def test_an_extractor_upgrade_rebaselines_instead_of_reporting_a_change() -> None:
    """**The H5 fence.** *Fails when* digests are compared across extractor
    versions. *Matters because* the version is mixed into the digest, so after
    an upgrade **every** digest differs: the whole inventory would report as
    semantically changed on the day someone improves a regex. That is the change
    storm H5 exists to prevent, and it would arrive looking like real work."""
    outcome = compute(
        stored=[_stored(extractor_version="v1", digest="sha256:aaa")],
        observed=[_observed(extractor_version="v2", digest="sha256:zzz")],
        moves=MoveOutcome(),
        ran_extractors=["endpoints"],
    )

    assert outcome.changes == ()
    assert len(outcome.rebaselines) == 1
    assert outcome.rebaselines[0].from_version == "v1"
    assert outcome.rebaselines[0].to_version == "v2"
    assert outcome.rebaselines[0].digest == "sha256:zzz"


def test_two_unknown_versions_still_compare() -> None:
    """*Fails when* the fence fires on a store whose revisions predate version
    recording. *Matters because* it would re-baseline on every run and never
    compare anything -- a diff that silently stopped working while reporting
    success."""
    outcome = compute(
        stored=[_stored(extractor_version=None, digest="sha256:aaa")],
        observed=[_observed(extractor_version=None, digest="sha256:bbb")],
        moves=MoveOutcome(),
        ran_extractors=["endpoints"],
    )

    assert _classes(outcome) == [CLASS_SEMANTICS]


def test_a_referent_with_no_stored_digest_is_rebaselined_not_changed() -> None:
    """*Fails when* an identity written before digests existed reports as
    changed. *Matters because* every identity in a pre-Build-1 store would
    stale at once on first refresh, for no reason at all."""
    outcome = compute(
        stored=[_stored(digest=None, extractor_version=None)],
        observed=[_observed()],
        moves=MoveOutcome(),
        ran_extractors=["endpoints"],
    )

    assert outcome.changes == ()
    assert outcome.rebaselines[0].from_version is None


# -- ordering ---------------------------------------------------------------


def test_entries_are_ordered_totally_and_deterministically() -> None:
    """*Fails when* batch order depends on dict iteration. *Matters because* a
    queue that reshuffles between sessions makes a reviewer re-read entries they
    already triaged, and a re-run diff would show spurious churn."""
    stored = [
        _stored(uri=_REFUNDS, identity_id="idn_refunds", digest="sha256:ccc"),
        _stored(uri=_ORDERS, identity_id="idn_orders"),
    ]
    observed = [
        _observed(uri=_ORDERS, digest="sha256:bbb"),
        _observed(uri=_REFUNDS, digest="sha256:ddd"),
    ]

    first = compute(
        stored=stored, observed=observed, moves=MoveOutcome(), ran_extractors=["endpoints"]
    )
    second = compute(
        stored=list(reversed(stored)),
        observed=list(reversed(observed)),
        moves=MoveOutcome(),
        ran_extractors=["endpoints"],
    )

    assert [entry.uri for entry in first.changes] == [entry.uri for entry in second.changes]
    assert [entry.uri for entry in first.changes] == sorted(entry.uri for entry in first.changes)


def test_inactive_identities_are_never_reclassified() -> None:
    """*Fails when* an already-dead identity is reported dead again at every
    refresh. *Matters because* the queue would carry permanent entries that no
    action can clear -- the reviewer's signal that the queue is broken."""
    outcome = compute(
        stored=[_stored(status="dead"), _stored(uri=_REFUNDS, identity_id="idn_r", status="moved")],
        observed=[],
        moves=MoveOutcome(),
        ran_extractors=["endpoints"],
    )

    assert outcome.changes == ()
    assert outcome.exemptions == ()
