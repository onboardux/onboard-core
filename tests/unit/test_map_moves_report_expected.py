"""Move detection, the store-rendered report, and the recall floor.

The three S1.2 units that decide what a map *says* rather than what it finds.
Two of them have a way of being wrong that looks exactly like working: move
detection that writes nothing reports an empty `moves` list, which is also what
a repository with no moves reports; and a recall check over a list that failed
to load reports perfect recall, which is CR-67's `0/0 covered (100%)` again.
Both are given a negative case that only passes when the code actually runs.
"""

from pathlib import Path

import pytest
from adopt_map import (
    ObservedIdentity,
    StoredIdentity,
    StoredRevision,
    build_report,
    detect_moves,
    load_expected,
    missing_identities,
)


def _stored(uri: str, digest: str | None = "sha256:a", status: str = "active") -> StoredIdentity:
    return StoredIdentity(identity_id=f"idn_{uri[-6:]}", uri=uri, digest=digest, status=status)


def _observed(uri: str, digest: str = "sha256:a") -> ObservedIdentity:
    return ObservedIdentity(
        uri=uri,
        kind="metadata_component",
        namespace="file",
        key=tuple(uri.split("/")),
        digest=digest,
    )


@pytest.mark.unit
def test_a_unique_digest_match_is_a_move(tmp_path: Path) -> None:
    """*Fails when* one referent disappearing and one identical one appearing is
    not paired. *Matters because* an unrecorded move detaches every binding,
    probe and piece of bound knowledge hanging off that URI, permanently -- a
    URI is never rewritten, so nothing later reattaches them. *No other
    instrument catches it because* both halves are individually correct: the old
    identity is genuinely absent and the new one is genuinely new.
    """
    outcome = detect_moves(observed=[_observed("uri://new")], stored=[_stored("uri://old")])

    assert len(outcome.moves) == 1
    assert outcome.moves[0].from_uri == "uri://old"
    assert outcome.moves[0].to.uri == "uri://new"
    assert outcome.absent == ()
    assert outcome.ambiguous == ()


@pytest.mark.unit
def test_two_identical_candidates_are_reported_and_nothing_is_written() -> None:
    """*Fails when* an ambiguous pairing is guessed. *Matters because* three
    identical `__init__.py` stubs genuinely share a digest, and a guess mints a
    permanent alias between two unrelated referents that no later build can
    detect as wrong. *No other instrument catches it because* a guessed move is
    a well-formed `moved` revision -- valid chain, valid alias, wrong answer.
    """
    outcome = detect_moves(
        observed=[_observed("uri://new-a"), _observed("uri://new-b")],
        stored=[_stored("uri://old-a"), _stored("uri://old-b")],
    )

    assert outcome.moves == ()
    assert len(outcome.ambiguous) == 1
    # Both sides named: "ambiguous" alone sends a reader looking.
    assert set(outcome.ambiguous[0]) == {"uri://old-a", "uri://old-b", "uri://new-a", "uri://new-b"}


@pytest.mark.unit
def test_absence_is_reported_and_never_retired() -> None:
    """*Fails when* a disappeared referent with no counterpart is written as
    dead. *Matters because* absence is not death (plan decision D6): a new
    ignore rule, a narrowed root and an extractor that failed this run all look
    identical from here, and retirement belongs to Build 6, which owns the
    `change_event` a retirement would be reported through. *No other instrument
    catches it because* retiring on absence would look correct on the one tree
    where the file really was deleted.
    """
    outcome = detect_moves(observed=[], stored=[_stored("uri://gone")])

    assert outcome.moves == ()
    assert outcome.absent == ("uri://gone",)


@pytest.mark.unit
def test_an_already_moved_identity_is_not_paired_again() -> None:
    """*Fails when* an identity whose head is `moved` is treated as a candidate.
    *Matters because* it has already been accounted for, and pairing it again
    chains an alias onto an alias -- a second `moved` revision on a row whose
    referent left. *No other instrument catches it because* the second run's
    output looks like the first's, and the extra revision is only visible in the
    chain.
    """
    outcome = detect_moves(
        observed=[_observed("uri://new")],
        stored=[_stored("uri://old", status="moved")],
    )

    assert outcome.moves == ()
    assert outcome.absent == ()


@pytest.mark.unit
def test_a_digest_from_a_different_extractor_version_cannot_pair() -> None:
    """*Fails when* two referents pair across extractor versions. *Matters
    because* the version is mixed into the digest precisely so that "we changed
    how we look" can never read as "the referent moved". *No other instrument
    catches it because* the digest tests prove two versions differ, not that
    move detection respects the difference.
    """
    outcome = detect_moves(
        observed=[_observed("uri://new", digest="sha256:v2")],
        stored=[_stored("uri://old", digest="sha256:v1")],
    )

    assert outcome.moves == ()
    assert outcome.absent == ("uri://old",)


class _Row:
    """The two fields `build_report` reads off an identity row."""

    def __init__(self, identity_id: str, uri: str, kind: str, namespace: str | None) -> None:
        self.id = identity_id
        self.uri = uri
        self.identity_kind = kind
        self.namespace = namespace
        self.local_key = uri.rsplit("/", 1)[-1]


def _revision(
    identity_id: str,
    created_at: str,
    status: str = "active",
    extractor: str | None = "web.endpoints",
) -> StoredRevision:
    return StoredRevision(
        identity_id=identity_id,
        extractor=extractor,
        extractor_version="1" if extractor else None,
        source_ref="app/api.py:10-12" if extractor else None,
        source_version="sha256:a",
        status=status,
        created_at=created_at,
        revision_id=f"idr_{created_at}",
    )


@pytest.mark.unit
def test_provenance_comes_from_the_creating_revision_and_status_from_the_head() -> None:
    """*Fails when* the report reads provenance off the head revision. *Matters
    because* a moved identity's head is a `moved` revision carrying an alias and
    **no extractor at all**, so reading it blanks the provenance of exactly the
    identities whose history matters most -- the ones that moved. *No other
    instrument catches it because* every unmoved identity has one revision, so
    head and creating are the same row and the bug is invisible until a move
    happens.
    """
    row = _Row("idn_1", "onboard-v1://f/e/s/env/endpoint/-/GET%20%2Fx", "endpoint", None)

    payload = build_report(
        identities=[row],
        revisions=[
            _revision("idn_1", "2026-08-21T10:00:00Z"),
            _revision("idn_1", "2026-08-21T11:00:00Z", status="moved", extractor=None),
        ],
        files_walked=10,
        files_unmapped=4,
    )

    listed = payload["listing"][0]
    assert listed["extractor"] == "web.endpoints"
    assert listed["source_ref"] == "app/api.py:10-12"
    assert listed["status"] == "moved"


@pytest.mark.unit
def test_the_listing_is_totally_ordered_by_kind_then_uri() -> None:
    """*Fails when* the report's row order depends on the order rows arrived.
    *Matters because* a report whose order shifts between runs shows a diff on
    every run and teaches its reader to ignore diffs -- which is the same
    property that makes the export bundle byte-stable. *No other instrument
    catches it because* an unordered listing is complete and correct, just
    unusable for comparison.
    """
    rows = [
        _Row("idn_2", "onboard-v1://f/e/s/env/endpoint/-/b", "endpoint", None),
        _Row("idn_1", "onboard-v1://f/e/s/env/config_key/env/Z", "config_key", "env"),
        _Row("idn_3", "onboard-v1://f/e/s/env/endpoint/-/a", "endpoint", None),
    ]

    payload = build_report(identities=rows, revisions=[], files_walked=3, files_unmapped=0)

    assert [row["kind"] for row in payload["listing"]] == ["config_key", "endpoint", "endpoint"]
    assert [row["uri"][-1] for row in payload["listing"]] == ["Z", "a", "b"]
    assert payload["counts_by_kind"] == {"config_key": 1, "endpoint": 2}
    assert payload["identities"] == 3


@pytest.mark.unit
def test_an_expected_list_skips_comments_and_collapses_duplicates() -> None:
    """*Fails when* a `#` comment or a blank line is checked as a URI. *Matters
    because* the curated list's whole value is that it explains **why** each
    entry is on it, and a format that cannot carry that explanation becomes a
    list nobody can honestly prune. *No other instrument catches it because* a
    comment treated as a URI fails the check, which reads as a map defect.
    """
    text = "# why this list exists\n\nuri://a\nuri://b\nuri://a\n   \n"

    assert load_expected(text) == ("uri://a", "uri://b")


@pytest.mark.unit
def test_misses_are_named_in_file_order() -> None:
    """*Fails when* the check reports a count instead of the URIs, or reorders
    them. *Matters because* "3 identities missing" sends a reader looking and
    the three URIs tell them what to fix -- and file order keeps them grouped
    the way the person who curated them grouped them. *No other instrument
    catches it because* a count and a list agree on every green run.
    """
    expected = ("uri://a", "uri://b", "uri://c")

    assert missing_identities(expected, ["uri://b"]) == ("uri://a", "uri://c")
    assert missing_identities(expected, ["uri://a", "uri://b", "uri://c"]) == ()


@pytest.mark.unit
def test_an_unreadable_expected_list_is_refused_rather_than_treated_as_empty(
    tmp_path: Path,
) -> None:
    """*Fails when* `--check-expected` against a missing file passes. *Matters
    because* an empty list passes every check, so a typo in the path would
    report a perfect recall floor over nothing -- the exact shape of CR-67's
    `0/0 covered (100%)`, in a gate whose blind state was indistinguishable from
    its passing state. *No other instrument catches it because* the green case
    and the blind case produce the same exit code and the same absence of
    findings.
    """
    from adopt_cli.commands.map_command import _read_expected
    from adopt_obs import AdoptError, ErrorCode, ExitCode, exit_code_for

    with pytest.raises(AdoptError) as raised:
        _read_expected(tmp_path / "no-such-list.txt")

    assert raised.value.code is ErrorCode.MAP_EXPECTED_LIST_UNREADABLE
    # Exit 2, never 4: a missing *file* is the operator's mistake, and reporting
    # it as "degraded with findings" would file it beside a real recall miss.
    assert exit_code_for(raised.value.code) == ExitCode.USAGE_ERROR
