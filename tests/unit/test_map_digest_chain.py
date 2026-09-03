"""Which revision a diff compares against, and why it is not the creating one.

Before Build 6 every identity had exactly one `identity_revision`, so "the
creating revision", "the head" and "the latest digest-bearing revision" were the
same row and the distinction cost nothing. Refresh appends digest-recording
revisions, and from that moment the three diverge -- silently, in a direction
that re-reports one edit at every subsequent run.
"""

import pytest
from adopt_map.report import StoredRevision, chain_summary, digest_summary

pytestmark = pytest.mark.unit


def _revision(
    revision_id: str,
    *,
    created_at: str,
    source_version: str | None,
    status: str = "active",
    extractor_version: str | None = "v1",
) -> StoredRevision:
    return StoredRevision(
        identity_id="idn_one",
        extractor="endpoints",
        extractor_version=extractor_version,
        source_ref="src/api.py:1-4",
        source_version=source_version,
        status=status,
        created_at=created_at,
        revision_id=revision_id,
    )


def test_one_revision_reads_the_same_three_ways() -> None:
    """**The amendment's compatibility proof.** *Fails when* the new reading
    disagrees with the old on a store written before Build 6. *Matters because*
    every store in existence has exactly one revision per identity, and a diff
    that read them differently from `adopt map` would report phantom changes on
    the first refresh of a healthy store. *No other instrument catches it*
    because both readings are individually self-consistent."""
    only = _revision("irev_1", created_at="2026-01-01T00:00:00+00:00", source_version="sha256:aaa")

    chains = chain_summary([only])
    digests = digest_summary([only])

    assert chains["idn_one"][0].revision_id == "irev_1"
    assert chains["idn_one"][1].revision_id == "irev_1"
    assert digests["idn_one"].revision_id == "irev_1"
    assert digests["idn_one"].source_version == only.source_version


def test_the_latest_digest_wins_over_the_creating_one() -> None:
    """*Fails when* the diff keeps comparing against the first observation.
    *Matters because* the next refresh would re-report an edit already reviewed
    and resolved -- the same change surfacing at every run until someone edits
    the file back. That is a change storm produced by the instrument, which is
    the H5 failure wearing a different hat."""
    first = _revision("irev_1", created_at="2026-01-01T00:00:00+00:00", source_version="sha256:aaa")
    later = _revision("irev_2", created_at="2026-02-01T00:00:00+00:00", source_version="sha256:bbb")

    digests = digest_summary([first, later])

    assert digests["idn_one"].source_version == "sha256:bbb"
    # The creating revision is still where provenance comes from -- the two
    # readings answer different questions and both remain available.
    assert chain_summary([first, later])["idn_one"][0].revision_id == "irev_1"


def test_a_revision_without_a_digest_never_wins() -> None:
    """*Fails when* a `moved` or `dead` revision blanks the comparison. *Matters
    because* those revisions carry no `source_version`: letting one win would
    make the identity read as never-observed, and a never-observed identity
    cannot be compared -- so a moved-then-restored referent would silently stop
    being watched."""
    observed = _revision(
        "irev_1", created_at="2026-01-01T00:00:00+00:00", source_version="sha256:aaa"
    )
    moved = _revision(
        "irev_2", created_at="2026-02-01T00:00:00+00:00", source_version=None, status="moved"
    )

    digests = digest_summary([observed, moved])

    assert digests["idn_one"].source_version == "sha256:aaa"
    assert chain_summary([observed, moved])["idn_one"][1].status == "moved"


def test_the_extractor_version_travels_with_the_digest() -> None:
    """*Fails when* the fence loses the version that produced the digest.
    *Matters because* across an extractor upgrade every digest differs by
    construction (the version is mixed into it), so a diff that cannot compare
    versions reads an instrument change as every identity changing at once."""
    upgraded = _revision(
        "irev_2",
        created_at="2026-02-01T00:00:00+00:00",
        source_version="sha256:bbb",
        extractor_version="v2",
    )

    assert digest_summary([upgraded])["idn_one"].extractor_version == "v2"
