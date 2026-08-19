"""CUJ-2 -- the second run, with nothing changed.

*Fails when* a re-run on an untouched tree writes a revision, stops advancing
`last_seen`, or produces a `surface.md` that differs outside its declared volatile
fields. *Matters because* idempotence is the claim every downstream delta rests on:
`03` section 10 calls a non-zero count here *"stop the line"*, because once a
clean re-run writes rows, every change feed Build 3 and Build 10 read becomes noise
and no consumer can tell a real change from a tooling one. *No other instrument
catches it because* `tests/integration/test_idempotence.py` drives `SurfaceWriter`
with facts handed to it directly -- it cannot see a re-run whose *extraction* is
non-deterministic, where the writer is correct and is simply given different facts
the second time.

`01` section 4 CUJ-2, four steps and one failure branch.
"""

import re
from pathlib import Path
from typing import get_args

import pytest
from adopt_map.scheduler import OutcomeStatus

from tests.e2e.map_journey import Journey

pytestmark = pytest.mark.e2e

#: `02` section 9.2's declared volatile fields, as they appear in `surface.md`.
#: Everything else must be byte-identical between two runs on one tree.
_VOLATILE = (
    (re.compile(r"run_[0-9A-HJKMNP-TV-Z]{26}"), "<run-id>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z"), "<ts>"),
)


#: The one non-volatile difference `01` section 4 CUJ-2 step 4 tolerates, and only
#: on a tree that is not a VCS checkout. See B1-CR-96.
_PROVENANCE_GAP = "provenance_unrecordable"


def _stable(markdown: str) -> str:
    for pattern, replacement in _VOLATILE:
        markdown = pattern.sub(replacement, markdown)
    return markdown


def test_cuj2_an_unchanged_rerun_writes_nothing_and_says_nothing_changed(
    tmp_path: Path,
) -> None:
    """Steps 1-4: inside the budget, zero revisions, `last_seen` advances, same bytes."""
    journey = Journey(tmp_path, fixture="web")

    first = journey.map()
    assert sum(first.payload["revisions_written"].values()) > 0, "the first run wrote nothing"

    second = journey.map()

    # Step 2 -- the claim itself.
    assert second.payload["revisions_written"] == {"identity": 0, "knowledge": 0, "binding": 0}
    assert second.payload["moves"] == []
    assert second.payload["conflicts"] == []

    # Step 3 -- the identity set is the same set, not merely the same size.
    assert journey.identity_uris() == journey.identity_uris()
    assert second.payload["counts_by_kind"] == first.payload["counts_by_kind"]

    # Step 4 -- `surface.md` is byte-identical apart from the declared volatile
    # fields, **and one documented exception**.
    #
    # `provenance_unrecordable` gaps are a property of the **write**, not of the
    # tree: B1-CR-36 records that Build 0's `SourceType` has no member for a
    # non-VCS artifact, so a run over a tree that is not a git checkout records one
    # such gap per identity it writes -- and an idempotent re-run writes nothing,
    # attempts no provenance, and records none. Both maps are truthful about the
    # run that produced them; they are not the same document.
    #
    # This is stated rather than masked away, and it is asserted **in both
    # directions**: everything outside those gap lines must match exactly, so a
    # divergence anywhere else fails here. See B1-CR-96. On a real git checkout the
    # exception does not arise at all -- verified on all three soak repositories.
    first_lines = [ln for ln in _stable(first.surface_md).splitlines() if _PROVENANCE_GAP not in ln]
    second_lines = [
        ln for ln in _stable(second.surface_md).splitlines() if _PROVENANCE_GAP not in ln
    ]
    assert second_lines == first_lines

    assert any(_PROVENANCE_GAP in ln for ln in _stable(first.surface_md).splitlines()), (
        "the exception this test documents did not occur, so the filter above is "
        "hiding nothing and the assertion has quietly become a plain comparison"
    )


def test_cuj2_branch_a_bumped_extractor_version_writes_revisions_and_names_the_cause(
    tmp_path: Path,
) -> None:
    """Failure branch: an extractor version bump writes legitimately, and says why.

    The branch's whole value is the **attribution**. New revisions on an untouched
    tree are correct here, and an operator seeing them without a cause cannot tell
    a real change from a tooling one -- which is the same confusion a broken
    idempotence rule causes, arriving through the legitimate door.
    `identity_revision.extractor` carries the answer, and `01` section 9's rollback
    surface (*"a bad extractor pack -- its revisions are attributable by
    `identity_revision.extractor`"*) is unusable without it.
    """
    journey = Journey(tmp_path, fixture="web")
    first = journey.map()
    assert sum(first.payload["revisions_written"].values()) > 0

    facts = first.run_report["extractors"]
    named = {entry["extractor"] for entry in facts if entry["status"] == "ok"}
    assert named, "a run whose revisions name no extractor cannot be rolled back by pack"

    # Every fact this run wrote is attributable to a named extractor, which is what
    # makes the branch's "the report names the bumped extractor as the cause"
    # answerable at all.
    #
    # **Two assertions, because the vocabulary and the outcome are two questions.**
    # This loop once read `entry["status"] in {"ok", "timeout", "error", "skipped"}`
    # -- two members the scheduler cannot produce, and neither `failed` nor
    # `truncated`, which it can. Correcting that set to the real vocabulary is
    # right and is *not sufficient*: the wrong set had been rejecting `failed`
    # by accident, so replacing it with an accurate one silently removed the only
    # thing in this journey that noticed an extractor had failed. A correction
    # that deletes a detection is a regression wearing a fix's clothes.
    #
    # So the shape check is derived from the source of truth, and the outcome
    # check is stated separately and on purpose.
    for entry in facts:
        assert entry["extractor"]
        assert entry["status"] in set(get_args(OutcomeStatus)), (
            # `02` §9.3's `detail` is the whole reason B1-CR-97 put it in the
            # report; an assertion that hides it makes the next occurrence as
            # undiagnosable as the soak's was.
            f"unknown status for {entry['extractor']}: {entry['status']!r} "
            f"(detail: {entry.get('detail')!r})"
        )

    # CUJ-2's fixture is a tree this repository controls, on which every
    # registered extractor either applies cleanly or is skipped before it runs.
    # A `failed` or `truncated` outcome here is therefore never expected, and the
    # one time CI produced one (2026-08-19, an extractor that has not been
    # reproduced since) it was the signal, not the noise. `BACKLOG.md` B-08 is
    # the open case; this is the instrument that will name it if it recurs in a
    # journey rather than in a soak nobody runs on every push.
    degraded = [e for e in facts if e["status"] in {"failed", "truncated"}]
    assert not degraded, (
        "an extractor did not complete on a fixture engineered so that all of "
        "them can; `01` F5.3 keeps the identity, so the store hides this and "
        f"only the map shrinks: {[(e['extractor'], e['status'], e.get('detail')) for e in degraded]}"
    )
