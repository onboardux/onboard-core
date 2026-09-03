"""Build 8's scoped regeneration: the section a change touched, and only it.

v6.1 section 6 Build 8's demo line -- *"pack sections regenerate scoped, not
whole-pack"* -- is two claims, and they fail in opposite directions:

* re-render **too little** and the pack is stale in a section nobody re-read;
* re-render **differently** and the FDE has two documents that disagree while
  both look right.

The second is the one no reader can catch, so it is the one asserted here by
bytes rather than by shape. `sections_affected` answers the first from the
sidecar Build 4 already writes; `render_sections` answers the second by sharing
`section_blocks` with `render`, and the test below is what proves the sharing is
real rather than a docstring.
"""

import datetime as _dt

import pytest
from adopt_handover import (
    PackKnowledge,
    assemble,
    parse_sidecar,
    render,
    render_sections,
    render_sidecar,
    sections_affected,
)

pytestmark = pytest.mark.unit

_WHEN = _dt.datetime(2026, 8, 28, 9, 0, 0, tzinfo=_dt.UTC)
_URI = "onboard-v1://f/e/orders-api/prod/endpoint/-/POST %2Fv1%2Forders"
_OTHER = "onboard-v1://f/e/orders-api/prod/job/-/nightly-reconcile"


def _knowledge(
    *,
    item_id: str,
    revision_id: str,
    title: str,
    kind: str,
    uri: str,
) -> PackKnowledge:
    return PackKnowledge(
        item_id=item_id,
        revision_id=revision_id,
        title=title,
        kind=kind,
        body_md=f"How {title} is run, and why it was set up that way.",
        verification="verified",
        created_at=_WHEN,
        identity_uris=(uri,),
        audiences=("technical",),
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
    def freshness_of(self, item_id: str) -> str:
        return "fresh"


def _pack():  # type: ignore[no-untyped-def]
    rows = (
        _knowledge(
            item_id="ki_runbook",
            revision_id="krev_01RUNBOOK",
            title="Order submission",
            kind="procedure",
            uri=_URI,
        ),
        _knowledge(
            item_id="ki_decision",
            revision_id="krev_01DECISION",
            title="Why reconciliation runs nightly",
            kind="rationale",
            uri=_OTHER,
        ),
    )
    return assemble(
        audience="technical",
        knowledge=_Reader(rows),
        identities=_Reader(()),
        freshness=_Freshness(),
        boundary=_Reader(None),
        gaps=(),
    )


def test_a_scoped_render_is_byte_identical_to_that_section_of_a_full_render() -> None:
    """The correctness contract, asserted on bytes and nothing softer.

    Fails when a scoped re-render and a full one diverge for the same store;
    matters because the FDE hands the client one document and keeps the other,
    and two renderings of the same knowledge that differ by a space make every
    later diff unreadable; no other instrument catches it because both outputs
    are valid Markdown and every shape assertion passes on each of them
    independently.
    """
    pack = _pack()
    full = render(pack)
    fragment = render_sections(pack, ["runbook"])

    assert fragment.strip(), "a section with confirmed knowledge rendered nothing"
    # The fragment appears verbatim inside the full document. `render` joins the
    # same blocks with one blank line, so containment is the whole claim.
    assert fragment.rstrip("\n") in full


def test_an_unaffected_section_is_not_re_rendered() -> None:
    """The other half: scoping has to actually scope.

    Fails when the selection is ignored and the whole pack comes back; matters
    because "scoped, not whole-pack" is the demo line, and a re-render that
    quietly did everything would pass the byte-equality test above perfectly.
    """
    pack = _pack()
    fragment = render_sections(pack, ["runbook"])

    assert "Order submission" in fragment
    assert "Why reconciliation runs nightly" not in fragment
    assert "Decisions and rationale" not in fragment
    # No title and no preamble: a fragment is part of a document, not a smaller
    # one, and a reader handed a second `# Handover pack` heading has two packs.
    assert not fragment.startswith("# ")


def test_sections_render_in_pack_order_however_they_were_asked_for() -> None:
    """Order is the reader's, never the caller's collection order."""
    pack = _pack()
    forward = render_sections(pack, ["runbook", "decisions"])
    backward = render_sections(pack, ["decisions", "runbook"])
    assert forward == backward
    assert forward.index("Runbook and how-to") < forward.index("Decisions and rationale")


def test_the_sidecar_selects_the_section_a_changed_referent_appears_in() -> None:
    """`sections_affected` over the sidecar the same pack wrote.

    Fails when lineage selection stops matching what a pack rendered; matters
    because the plane's review resolution hands the FDE a URI and a revision id
    and nothing else -- the sidecar is the only thing that turns those into
    section names; no other instrument catches it because a wrong selection
    re-renders a real section perfectly.
    """
    pack = _pack()
    sidecar = parse_sidecar(render_sidecar(pack))

    assert sections_affected(sidecar, [], [_URI]) == ("runbook",)
    assert sections_affected(sidecar, ["krev_01DECISION"], []) == ("decisions",)
    assert sections_affected(sidecar, ["krev_01RUNBOOK"], [_OTHER]) == ("runbook", "decisions")


def test_a_change_touching_nothing_selects_nothing() -> None:
    """An empty selection is an answer, not a fallback to everything.

    Fails when an unmatched change quietly selects every section; matters
    because that is the exact shape of "scoped regeneration" silently becoming
    whole-pack regeneration -- the output would be correct every time and the
    feature would be gone.
    """
    pack = _pack()
    sidecar = parse_sidecar(render_sidecar(pack))
    assert (
        sections_affected(sidecar, ["krev_absent"], ["onboard-v1://f/e/s/prod/table/-/nope"]) == ()
    )
    assert sections_affected(sidecar, [], []) == ()


def test_derived_sections_are_never_selected_by_lineage() -> None:
    """The overview, gaps and boundary have no lineage and are not guessed at.

    Fails when a section with empty lineage starts matching everything (the
    natural bug: an empty set intersects nothing, but an empty *check* passes);
    matters because every change would then re-render every derived section and
    the selection would be decorative.
    """
    pack = _pack()
    sidecar = parse_sidecar(render_sidecar(pack))
    selected = sections_affected(sidecar, ["krev_01RUNBOOK"], [_URI])
    assert "overview" not in selected
    assert "gaps" not in selected
    assert "boundary" not in selected


def test_an_unreadable_sidecar_selects_nothing_rather_than_guessing() -> None:
    """A truncated build artifact must not look like a complete one."""
    assert sections_affected(parse_sidecar("not json"), ["krev_1"], []) == ()
    assert sections_affected(parse_sidecar('{"sections": "wrong"}'), ["krev_1"], []) == ()
