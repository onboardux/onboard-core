"""Build 4's assembly rules: what a pack selects, and what it tells the reader.

The stamp tests are the ones that matter. v6.1 §6 Build 4: *"a client reading a
draft as verified truth is this build's worst failure."* Everything below is
about making that failure impossible to reach by editing one file.
"""

import datetime as _dt
from pathlib import Path

import adopt_handover
import pytest
from adopt_handover import (
    FRESH,
    SECTIONS,
    STALE,
    UNVERIFIED,
    AssembledPack,
    AssembledSection,
    PackBoundary,
    PackGap,
    PackIdentity,
    PackKnowledge,
    StampedRevision,
    assemble,
    render,
    render_sidecar,
    select,
    stamp_for,
)

pytestmark = pytest.mark.unit

_WHEN = _dt.datetime(2026, 8, 5, 12, 0, 0, tzinfo=_dt.UTC)


def _knowledge(
    *,
    title: str = "Refund approvals",
    kind: str = "procedure",
    verification: str | None = "verified",
    audiences: tuple[str, ...] = ("client_ops",),
    revision_id: str = "krev_01AAAAAAAAAAAAAAAAAAAAAAAA",
    item_id: str = "ki_01AAA",
) -> PackKnowledge:
    return PackKnowledge(
        item_id=item_id,
        revision_id=revision_id,
        title=title,
        kind=kind,
        body_md="The approval step exists because chargebacks were disputed.",
        verification=verification,
        created_at=_WHEN,
        identity_uris=("onboard-v1://f/e/s/prod/endpoint/-/POST %2Fv1%2Frefunds",),
        audiences=audiences,
    )


class _Reader:
    def __init__(self, rows: object) -> None:
        self._rows = rows

    def knowledge_for_pack(self) -> object:
        return self._rows

    def identities_for_pack(self) -> object:
        return self._rows

    def boundary_for_pack(self) -> object:
        return self._rows


class _Freshness:
    def __init__(self, state: str) -> None:
        self._state = state

    def freshness_of(self, item_id: str) -> str:
        return self._state


def _assemble(
    rows: tuple[PackKnowledge, ...], *, freshness: str = "fresh", audience: str = "client_ops"
):  # type: ignore[no-untyped-def]
    return assemble(
        audience=audience,
        knowledge=_Reader(rows),
        identities=_Reader(()),
        freshness=_Freshness(freshness),
        boundary=_Reader(None),
        gaps=(),
    )


# -- the stamp rule ---------------------------------------------------------


@pytest.mark.parametrize("marker", ["unverified", "conflicted", None])
def test_a_revision_no_human_confirmed_is_unverified_however_fresh_it_is(
    marker: str | None,
) -> None:
    """Verification outranks freshness, and a missing marker is not a confirmation.

    *Fails when* the stamp is computed from freshness first. *Matters because* a
    draft written seconds ago resolves `fresh`, so that ordering renders an
    unconfirmed draft as verified truth -- the sentence v6.1 calls this build's
    worst failure. *No other instrument catches it because* the pack still looks
    perfectly well-formed; only the one word differs.
    """
    assert stamp_for(_knowledge(verification=marker), FRESH) == UNVERIFIED


def test_never_observed_knowledge_is_unverified_rather_than_stale() -> None:
    """A store with no sensor reports `unverified` freshness, which is not staleness.

    *Fails when* every non-`fresh` state collapses to `stale`. *Matters because*
    the stale banner claims *the system changed after this was confirmed* -- and
    printing that over knowledge nothing has ever checked is a false statement
    about the client's system, and precisely the false-staleness noise H5 names
    as what makes reviewers stop trusting the queue.
    """
    assert stamp_for(_knowledge(), "unverified") == UNVERIFIED


@pytest.mark.parametrize("state", ["stale", "observation_stale", "retired"])
def test_a_real_change_under_confirmed_knowledge_is_stale(state: str) -> None:
    assert stamp_for(_knowledge(), state) == STALE


def test_confirmed_and_fresh_is_fresh() -> None:
    assert stamp_for(_knowledge(), FRESH) == FRESH


def _rendered_with_stamp(stamp: str) -> str:
    """One section rendered around a revision carrying `stamp`.

    Built directly rather than through `assemble`, deliberately. `select`
    admits only confirmed knowledge, so today no unverified revision can reach
    a section at all -- and a test that went through `select` would assert the
    banner over an empty document and pass for the wrong reason. That is the
    measurement-with-nothing-to-measure failure this repository has now found
    eight times, so the renderer is handed the value it must handle.

    Build 4's drafting (S4.2) is what starts putting unverified revisions into
    sections; the rule they will be rendered under is the one asserted here,
    and it is in place before the content that needs it.
    """
    section = next(section for section in SECTIONS if section.key == "runbook")
    pack = AssembledPack(
        audience="client_ops",
        sections=(
            AssembledSection(
                section=section,
                revisions=(StampedRevision(revision=_knowledge(), stamp=stamp),),
            ),
        ),
    )
    return render(pack)


def test_an_unverified_revision_can_never_render_without_its_banner() -> None:
    """The safety property, asserted on the bytes a client actually reads.

    *Fails when* a rendering path emits a body without the stamp its revision
    earned -- a new section type, an "executive summary" that quotes bodies, any
    shortcut. *Matters because* the stamp is the only thing separating a draft
    from a claim in the reader's hands, and v6.1 calls a client reading a draft
    as verified truth this build's worst failure. *No other instrument catches
    it because* `stamp_for` can be perfectly correct while the renderer ignores
    what it returned.
    """
    body = "The approval step exists because chargebacks were disputed."
    document = _rendered_with_stamp(UNVERIFIED)

    assert body in document
    assert "**unverified**" in document
    assert "UNVERIFIED — nothing has confirmed this section." in document
    # The banner precedes the body: a reader who stops half way through has
    # already been told.
    assert document.index("UNVERIFIED") < document.index(body)


def test_a_stale_section_names_the_change_and_a_fresh_one_carries_no_banner() -> None:
    stale = _rendered_with_stamp(STALE)
    assert "STALE — the system changed" in stale

    fresh = _rendered_with_stamp(FRESH)
    assert "**fresh**" in fresh
    assert "UNVERIFIED" not in fresh
    assert "STALE" not in fresh


# -- selection --------------------------------------------------------------


def test_only_confirmed_knowledge_is_selected() -> None:
    """B2's honesty invariant, enforced where a pack could quietly break it."""
    runbook = next(section for section in SECTIONS if section.key == "runbook")
    rows = (_knowledge(verification="verified"), _knowledge(verification="unverified"))

    chosen = select(runbook, rows, "client_ops")

    assert [row.verification for row in chosen] == ["verified"]


def test_a_section_selects_only_its_own_kind_and_audience() -> None:
    runbook = next(section for section in SECTIONS if section.key == "runbook")
    rows = (
        _knowledge(kind="procedure", audiences=("client_ops",), title="A"),
        _knowledge(kind="rationale", audiences=("client_ops",), title="B"),
        _knowledge(kind="procedure", audiences=("technical",), title="C"),
    )

    assert [row.title for row in select(runbook, rows, "client_ops")] == ["A"]


def test_an_empty_section_says_so_rather_than_vanishing() -> None:
    """The gap is the deliverable; a pack that drops it looks complete instead.

    This is also the whole of R3's no-model mode: with no adapter configured,
    every uncovered section renders its note and the pack is still complete.
    """
    document = render(_assemble(()))

    for section in SECTIONS:
        assert section.heading in document
    assert "No confirmed procedures yet" in document


# -- byte stability ---------------------------------------------------------


def test_the_same_revisions_render_identical_bytes() -> None:
    """*Fails when* a clock, a set iteration or a dict order reaches the output.

    *Matters because* v6.1 requires the canonical Markdown to be byte-stable
    given the same revisions: a pack that differs on every run cannot be
    diffed, reviewed, or checked into a client's repository. *No other
    instrument catches it because* a single rendering always looks correct.
    """
    rows = (
        _knowledge(title="Zeta", revision_id="krev_01ZZZ", item_id="ki_z"),
        _knowledge(title="Alpha", revision_id="krev_01AAA", item_id="ki_a"),
    )
    first = _assemble(rows)
    second = _assemble(tuple(reversed(rows)))

    assert render(first) == render(second)
    assert render_sidecar(first) == render_sidecar(second)


def test_no_wall_clock_reaches_the_document() -> None:
    """Dates come from revisions, so nothing in the output moves on its own."""
    document = render(_assemble((_knowledge(),)))
    assert "2026-08-05T12:00:00.000Z" in document


def test_the_renderer_reads_no_clock_at_all() -> None:
    """Structural, because rendering twice cannot prove this.

    *Fails when* any clock call enters the renderer. *Matters because* a
    "generated at" line is the single field that makes two runs over an
    unchanged store differ, and it is the most natural thing in the world for
    someone to add to a report.

    *No other instrument catches it because* -- and this was verified by
    planting the defect -- rendering twice in one process does **not** catch a
    wall clock: the platform clock's granularity is coarser than the gap between
    two adjacent calls, so both renders stamp the identical timestamp and the
    byte-comparison passes. Only the end-to-end journey, whose two runs are
    separate processes seconds apart, went red. A structural check does not
    depend on how fast the machine is.
    """
    # By path, not by attribute: `adopt_handover.render` resolves to the
    # re-exported *function*, which has no `__file__`.
    package = Path(adopt_handover.__file__ or "").parent
    source = (package / "render.py").read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("#", "*"))
    )
    for call in ("datetime.now", "date.today", "time.time", "utcnow", "time.monotonic"):
        assert call not in body, (
            f"{call} appears in the renderer. Every date in a pack comes from a "
            "revision's own timestamp; a clock here makes the output unstable."
        )


def test_the_sidecar_maps_every_section_to_its_sources() -> None:
    """Build 8 reads this to regenerate a section without regenerating the pack."""
    import json

    payload = json.loads(render_sidecar(_assemble((_knowledge(),))))

    assert payload["audience"] == "client_ops"
    sections = {entry["section"]: entry for entry in payload["sections"]}
    assert set(sections) == {section.key for section in SECTIONS}
    assert sections["runbook"]["revision_ids"] == ["krev_01AAAAAAAAAAAAAAAAAAAAAAAA"]
    assert sections["runbook"]["identity_uris"] == [
        "onboard-v1://f/e/s/prod/endpoint/-/POST %2Fv1%2Frefunds"
    ]
    # A derived section cites nothing, and says so rather than being absent.
    assert sections["overview"]["revision_ids"] == []


# -- the gap appendix and the boundary --------------------------------------


def test_the_gap_appendix_shows_each_disposition_and_defaults_to_open() -> None:
    pack = assemble(
        audience="client_ops",
        knowledge=_Reader(()),
        identities=_Reader(()),
        freshness=_Freshness(FRESH),
        boundary=_Reader(None),
        gaps=(
            PackGap(
                uri="onboard-v1://f/e/s/prod/config_key/-/API_KEY",
                kind="config_key",
                gap_key="k1",
                status="acknowledged",
                owner_actor_id="alice",
                note="SME session booked",
            ),
            PackGap(
                uri="onboard-v1://f/e/s/prod/endpoint/-/GET %2Fhealth",
                kind="endpoint",
                gap_key="k2",
            ),
        ),
    )
    document = render(pack)

    assert "acknowledged" in document
    assert "alice" in document
    assert "SME session booked" in document
    # No disposition means `open`, because the recompute derived the gap and
    # nobody has decided anything about it yet.
    assert "| open |" in document


def test_a_pack_without_a_boundary_says_it_is_unbounded() -> None:
    """A pack must state its own limits; silence would read as "no limits"."""
    document = render(_assemble(()))
    assert "No observability boundary is declared" in document


def test_a_declared_boundary_is_embedded() -> None:
    pack = assemble(
        audience="client_ops",
        knowledge=_Reader(()),
        identities=_Reader(()),
        freshness=_Freshness(FRESH),
        boundary=_Reader(
            PackBoundary(
                tier="T2",
                declared_at=_WHEN,
                contractual=True,
                covered="Artifacts and deploy signals.",
                not_covered="Runtime traces.",
                permitted_outbound_categories=("metadata_only",),
            )
        ),
        gaps=(),
    )
    document = render(pack)

    assert "**Tier:** T2" in document
    assert "metadata_only" in document
    assert "Runtime traces." in document


def test_the_overview_counts_identities_by_kind() -> None:
    pack = assemble(
        audience="client_ops",
        knowledge=_Reader(()),
        identities=_Reader(
            (
                PackIdentity(
                    uri="onboard-v1://f/e/s/prod/endpoint/-/a", kind="endpoint", covered=True
                ),
                PackIdentity(
                    uri="onboard-v1://f/e/s/prod/endpoint/-/b", kind="endpoint", covered=False
                ),
                PackIdentity(
                    uri="onboard-v1://f/e/s/prod/config_key/-/c", kind="config_key", covered=False
                ),
            )
        ),
        freshness=_Freshness(FRESH),
        boundary=_Reader(None),
        gaps=(),
    )
    document = render(pack)

    assert "Identities mapped: **3**" in document
    assert "| endpoint | 2 |" in document
    assert "Covered by confirmed knowledge: **1** of **3**." in document
