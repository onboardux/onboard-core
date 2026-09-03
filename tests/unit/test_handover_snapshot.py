"""The acceptance digest: the one string both parties can recompute.

*Fails when* the digest starts depending on anything that differs between two
exports of the same rows -- `written_at` above all. *Matters because* "both
parties hold it" is only a promise if the client can check theirs: they run
`adopt import` then `adopt export` on their own machine and compare. A digest
that moved would tell them the handover had been altered, which is the one
alarm this record exists to be able to raise truthfully. *No other instrument
catches it because* the delivering side would still agree with itself perfectly:
the disagreement only appears on somebody else's copy, days later, with no way
left to tell a real substitution from a self-inflicted one.
"""

import pytest
from adopt_handover import acceptance_digest

from adopt_export import BundleManifest, BundleScope, TableEntry

pytestmark = pytest.mark.unit

_TABLES = [("identity", "aaa"), ("knowledge_item", "bbb"), ("binding", "ccc")]


def _manifest(*, written_at: str, tables: list[tuple[str, str]] | None = None) -> BundleManifest:
    return BundleManifest(
        export_version=4,
        schema_version=4,
        scope=BundleScope(firm="northwind", engagement="acme-erp"),
        written_by="adopt-core/0.3.1",
        written_at=written_at,
        tables=[
            TableEntry(name=name, rows=1, sha256=sha, omitted_columns=[])
            for name, sha in (tables or _TABLES)
        ],
        blobs={"count": 0, "total_bytes": 0},  # type: ignore[arg-type]
    )


def test_the_digest_is_order_independent() -> None:
    """Two realizations may return their tables in different orders."""
    assert acceptance_digest(_TABLES) == acceptance_digest(list(reversed(_TABLES)))


def test_the_digest_changes_when_a_table_digest_changes() -> None:
    """The positive control: it is a digest, not a constant.

    Without this, a function returning one fixed string would pass every other
    test in this file.
    """
    altered = [("identity", "aaa"), ("knowledge_item", "CHANGED"), ("binding", "ccc")]

    assert acceptance_digest(_TABLES) != acceptance_digest(altered)


def test_the_digest_changes_when_a_table_appears_or_vanishes() -> None:
    """A bundle missing a table is a different bundle."""
    fewer = _TABLES[:-1]
    more = [*_TABLES, ("provenance", "ddd")]

    assert acceptance_digest(_TABLES) != acceptance_digest(fewer)
    assert acceptance_digest(_TABLES) != acceptance_digest(more)


def test_two_manifests_differing_only_in_written_at_digest_identically() -> None:
    """The defect this module exists to prevent, against the real manifest shape.

    `written_at` is a wall-clock string the writer stamps on every export, so a
    client re-exporting their imported copy always has a different one. If it
    reached the digest, every verification would fail and the alarm would mean
    nothing.
    """
    first = _manifest(written_at="2026-09-02T12:00:00.000Z")
    second = _manifest(written_at="2027-01-14T08:31:07.412Z")

    def digest_of(manifest: BundleManifest) -> str:
        return acceptance_digest((entry.name, entry.sha256) for entry in manifest.tables)

    assert digest_of(first) == digest_of(second)
    # And the same value the plain pairs produce -- so the client needs nothing
    # from this repository beyond the two commands to check their copy.
    assert digest_of(first) == acceptance_digest(_TABLES)


def test_an_empty_bundle_still_digests() -> None:
    """A degenerate but real case: a scope with nothing exportable in it."""
    assert acceptance_digest([]) == acceptance_digest(())
    assert len(acceptance_digest([])) == 64
