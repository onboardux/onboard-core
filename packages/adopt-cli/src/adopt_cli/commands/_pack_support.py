"""The composition root for `adopt pack`: store rows -> `adopt_handover` views.

`adopt_handover` declares what it needs structurally and holds no dialect;
`adopt_store` holds the SQL and knows nothing about packs. This module is the
one place the two meet, which is CR-36's exemption used exactly as intended --
it maps values and contains no SQL of its own.

**Freshness is resolved here, per item, and passed in.** `adopt_handover` takes
a `FreshnessReader` rather than a freshness value because a section stamps per
revision, and the resolution is `adopt_freshness`'s to make. Caching it per item
matters: a pack with forty sections over a dozen items would otherwise resolve
the same item's freshness repeatedly, and `resolve_freshness` reads sensors.
"""

import hashlib
from pathlib import Path
from typing import Any

from adopt_handover import PackBoundary, PackConflict, PackGap, PackIdentity, PackKnowledge

__all__ = [
    "FreshnessCache",
    "assemble_pack",
    "build_boundary",
    "build_conflicts",
    "build_drafts",
    "build_gaps",
    "build_identities",
    "build_knowledge",
    "write_pack",
]


class FreshnessCache:
    """`FreshnessReader` over `adopt_freshness.resolve_freshness`, memoized.

    Returns the state name as a plain string, which is all `stamp_for` compares
    against -- keeping `adopt_handover` free of `adopt_freshness`'s types, so
    the pack renders what it is told and the authority stays where it is.
    """

    def __init__(self, handle: Any) -> None:
        self._handle = handle
        self._cache: dict[str, str] = {}

    def freshness_of(self, item_id: str) -> str:
        cached = self._cache.get(item_id)
        if cached is not None:
            return cached

        from adopt_freshness import resolve_freshness

        resolution = resolve_freshness(self._handle.freshness_records(), item_id)
        state = str(resolution.state)
        self._cache[item_id] = state
        return state


class _Reader:
    """A `KnowledgeReader`/`IdentityReader`/`BoundaryReader` over a fixed tuple.

    The readers are protocols with one method each, and everything they return
    was computed once by the builders below. A class per protocol would be three
    classes to say "hand back what I was given".
    """

    def __init__(self, rows: Any) -> None:
        self._rows = rows

    def knowledge_for_pack(self) -> Any:
        return self._rows

    def identities_for_pack(self) -> Any:
        return self._rows

    def boundary_for_pack(self) -> Any:
        return self._rows


def build_knowledge(handle: Any, *, system_id: str, environment_id: str | None) -> _Reader:
    """Every item in scope as a `PackKnowledge`, joined to audiences and URIs."""
    records = handle.pack_records()
    uris = records.uris_by_item()
    audiences = records.audiences_by_item()

    rows = [
        PackKnowledge(
            item_id=str(item.id),
            revision_id=str(revision.id),
            title=str(item.title),
            kind=str(item.kind),
            body_md=str(revision.body_md or ""),
            verification=None if revision.verification is None else str(revision.verification),
            created_at=revision.created_at,
            identity_uris=uris.get(str(item.id), ()),
            audiences=audiences.get(str(item.id), ()),
        )
        for item, revision in records.knowledge_heads(
            system_id=system_id, environment_id=environment_id
        )
    ]
    return _Reader(tuple(rows))


def build_drafts(handle: Any, *, system_id: str, environment_id: str | None) -> tuple[Any, ...]:
    """The **unverified drafts** in scope, for the pack's draft sections.

    A subset of `build_knowledge`'s rows rather than a second query: the pack
    renders drafts through the same view type, the same stamp rule and the same
    renderer as confirmed content, and the only thing that differs is which set
    a revision is in.

    **Membership is a provenance question**, and that is why it is answered here
    rather than in `adopt_handover`. `verification != verified` alone would sweep
    in every harvest candidate -- unconfirmed commits mined from local history,
    which are review fodder and have no business in a client's handover
    document. What distinguishes a draft is the `draft:` provenance row a
    drafting run wrote in the same transaction as the revision.
    """
    from adopt_cli.commands._draft_support import draft_revision_ids

    drafts = draft_revision_ids(handle)
    return tuple(
        row
        for row in build_knowledge(
            handle, system_id=system_id, environment_id=environment_id
        ).knowledge_for_pack()
        if row.revision_id in drafts and row.verification != "verified"
    )


def build_identities(
    handle: Any, *, system_id: str, environment_id: str | None, covered: frozenset[str]
) -> _Reader:
    """The inventory, with coverage taken from the recompute rather than the cache.

    `identity.covered_cache` is a cache that alarms rather than self-heals, and
    a pack must not be the thing that reads it: the recompute result the caller
    already holds is the authority (Build 0's rule, unchanged).
    """
    rows = [
        PackIdentity(uri=str(row.uri), kind=str(row.identity_kind), covered=str(row.id) in covered)
        for row in handle.pack_records().identities_in_scope(
            system_id=system_id, environment_id=environment_id
        )
    ]
    return _Reader(tuple(rows))


def build_boundary(handle: Any, *, system_id: str, environment_id: str | None) -> _Reader:
    row = handle.pack_records().boundary_for(system_id=system_id, environment_id=environment_id)
    if row is None:
        return _Reader(None)
    categories = row.permitted_outbound_categories
    return _Reader(
        PackBoundary(
            tier=str(row.tier),
            declared_at=row.declared_at,
            contractual=bool(row.contractual),
            covered=None if row.covered is None else str(row.covered),
            not_covered=None if row.not_covered is None else str(row.not_covered),
            permitted_outbound_categories=tuple(str(value) for value in categories or ()),
        )
    )


def build_gaps(ranked: tuple[Any, ...], dispositions: dict[str, Any]) -> tuple[PackGap, ...]:
    """Derived gaps joined to their dispositions -- the join, never a read.

    A disposition whose `gap_key` no longer appears in `ranked` contributes
    nothing, which is the whole reason existence stays derived: covering an
    identity removes its gap from the pack even though the row recording what
    somebody once decided about it is still there.
    """
    joined: list[PackGap] = []
    for gap in ranked:
        row = dispositions.get(gap.gap_key)
        joined.append(
            PackGap(
                uri=gap.uri,
                kind=gap.kind,
                gap_key=gap.gap_key,
                reasons=tuple(gap.reasons),
                status=None if row is None else str(row.status),
                owner_actor_id=None if row is None else row.owner_actor_id,
                note=None if row is None else row.note,
                waived_until=None if row is None else row.waived_until,
            )
        )
    return tuple(joined)


def build_conflicts(handle: Any, *, uris: dict[str, str]) -> tuple[PackConflict, ...]:
    """Open conflicts for the identities in scope, joined to their URIs.

    Read through `table_rows` (Build 4's pattern), so the gap appendix gaining
    Bet 4's deliverable adds no query path to any realized port and leaves the
    plane's escape-coverage denominator exactly where this build found it.

    Scoped by the `uris` mapping rather than by a filter here: a conflict row
    carries only an identity id, and which identities belong to this pack is a
    question the composition root has already answered for the inventory.
    """
    from adopt_knowledge import rank_conflicts

    from adopt_model import Conflict

    rows = handle.export_records().table_rows("conflict", Conflict)
    return tuple(
        PackConflict(
            uri=conflict.uri,
            kind=conflict.kind,
            intent_revision_id=conflict.intent_revision_id,
            detected_at=conflict.detected_at,
        )
        for conflict in rank_conflicts(rows, uris)
    )


# -- the two halves `adopt pack` and `adopt handover pack` share (Build 9) ---
#
# Build 9's third step is *"pack emission per audience (Build 4)"* -- the same
# packs, from the same store, by the same rules. Two callers of one function
# rather than a second assembly path, because two paths eventually disagree
# about what a pack contains and the disagreement would surface as a client
# receiving a document that does not match the one the FDE reviewed. The
# handover journey asserts byte-equality with a direct `adopt pack` for exactly
# this reason.


def assemble_pack(handle: Any, *, audience: str, system_id: str, environment_id: str | None) -> Any:
    """Read the store and assemble one audience's pack.

    Every read a pack needs, in the order `adopt pack` has always done them --
    coverage first (the authority on what is covered), then the eight builders
    above. Returns `adopt_handover.AssembledPack`, which carries no clock, so
    rendering it is a pure function and the caller may close the store first.
    """
    from adopt_handover import assemble
    from adopt_knowledge import rank_gaps

    from adopt_coverage import recompute_coverage

    coverage = recompute_coverage(handle.coverage_records(), system_id, environment_id)
    covered = frozenset(row.identity_id for row in coverage.identities if row.covered)
    ranked = rank_gaps(coverage.identities)
    # Every identity the recompute evaluated, by URI. The conflict join needs
    # it, and it is the same population the inventory renders -- so a conflict
    # can never name an identity this pack does not list.
    uris = {row.identity_id: row.uri for row in coverage.identities}

    return assemble(
        audience=audience,
        knowledge=build_knowledge(handle, system_id=system_id, environment_id=environment_id),
        identities=build_identities(
            handle, system_id=system_id, environment_id=environment_id, covered=covered
        ),
        freshness=FreshnessCache(handle),
        boundary=build_boundary(handle, system_id=system_id, environment_id=environment_id),
        gaps=build_gaps(ranked, handle.governance().gap_dispositions()),
        conflicts=build_conflicts(handle, uris=uris),
        drafts=build_drafts(handle, system_id=system_id, environment_id=environment_id),
    )


def write_pack(
    assembled: Any,
    out: Path,
    *,
    audience: str,
    selected: tuple[str, ...] | None = None,
    converter: Any = None,
) -> dict[str, Any]:
    """Render `assembled` and write it under `out`. Returns what landed where.

    **A scoped render never overwrites the pack**, and the separate name is the
    whole of why. The fragment is the part of a document that changed -- no
    title, no preamble, no gap appendix -- so writing it over `{audience}.md`
    would replace a deliverable with a piece of one, and the loss would be
    silent: the file would still be well-formed Markdown.

    **The sidecar is the whole pack's lineage and is written only with the whole
    pack.** A sidecar naming two sections would be read by the next scoped run as
    the complete lineage of a pack, and every section it did not mention would
    then look like a section no change could ever touch.

    `sha256` is over the Markdown's bytes as written. `adopt pack` does not
    report it; `adopt handover pack` records it, because the acceptance record
    has to be able to say which document went out.
    """
    from adopt_handover import convert, render, render_sections, render_sidecar

    document = render(assembled) if selected is None else render_sections(assembled, selected)
    lineage = render_sidecar(assembled)

    out.mkdir(parents=True, exist_ok=True)
    markdown_path = out / (f"{audience}.md" if selected is None else f"{audience}.sections.md")
    sidecar_path = out / f"{audience}.lineage.json"
    # `newline="\n"` on both: a pack diffed across a Windows checkout and a Linux
    # runner must not differ in every line, and CRLF is a recorded failure class
    # in this repository's own release pipeline.
    markdown_path.write_text(document, encoding="utf-8", newline="\n")
    if selected is None:
        sidecar_path.write_text(lineage, encoding="utf-8", newline="\n")

    written: dict[str, Any] = {
        "markdown_path": markdown_path,
        "sidecar_path": sidecar_path if selected is None else None,
        "sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
        "derived_path": None,
        "derived_with": None,
    }
    if converter is not None:
        derived_path = out / f"{audience}.{converter.format}"
        written["derived_path"] = derived_path
        written["derived_with"] = convert(markdown_path, derived_path, converter)
    return written
