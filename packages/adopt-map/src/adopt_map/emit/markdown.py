"""`surface.md` -- and its first screen is the normative part (`02` §9.1).

```
1. system + environment (name and id)      5. coverage (from recompute_coverage)
2. archetype + negotiated tier             6. degradations
3. run id + adopt version                  7. outside-VCS count, in plain language
4. counts by identity_kind                 8. -- then the inventory --
```

**A degradation or truncation that does not appear in the first screen is a
defect** (`02` §9.1). That sentence is why the order is a contract and not a
layout preference: this build exists to stop a client discovering, six weeks in,
that a family was never covered. Burying it under an inventory is the failure
mode, and an inventory is exactly what a reader stops before.

So the eight items are emitted from one ordered table (`FIRST_SCREEN`), and the
ordering test asserts against that table rather than against a rendered string --
a golden snapshot alone would let somebody reorder the sections and re-bless the
snapshot in the same commit.

**Stage 1 is a real artifact, not a preview** (`01` F11.2). It carries the same
first screen plus a "still running" notice, so the reader who opens it at minute
fifteen gets the honest headline and knows it is incomplete.
"""

from collections.abc import Callable, Sequence
from typing import Final

from adopt_const import MAP_FIRST_SCREEN_LIST_MAX, MAP_STAGE1_REQUIRED_FAMILIES
from adopt_map.report import RunResult
from adopt_obs import format_timestamp

__all__ = ["FIRST_SCREEN", "SURFACE_MD_NAME", "render_markdown", "render_stage1"]

SURFACE_MD_NAME: Final[str] = "surface.md"

#: The `02` §9.1 order, as data. Each entry is `(heading, renderer)`; the
#: renderer takes the run result and returns the section's lines.
FIRST_SCREEN: Final[tuple[str, ...]] = (
    "System and environment",
    "Archetype and tier",
    "Run",
    "Counts by kind",
    "Coverage",
    "Degradations",
    "Outside version control",
)


def _system(result: RunResult) -> list[str]:
    scope = result.resolved
    return [
        f"- System `{scope.system_id}`",
        f"- Environment **{scope.environment_slug}** (`{scope.environment_id}`)",
        f"- Engagement `{scope.engagement_id}` · Firm `{scope.firm_id}`",
    ]


def _archetype(result: RunResult) -> list[str]:
    # `tier` is never absent here: `02` §2 rule 4 aborts with
    # `MAP_BOUNDARY_MISSING` before a run reaches an emitter, so a fallback
    # string would be a branch describing a state the scope resolver forbids.
    tier = result.resolved.tier
    return [
        f"- Archetype **{result.resolved.archetype}** · negotiated tier **{tier}**",
        f"- Every coverage figure below was produced under tier {tier} (`01` F13.4).",
    ]


def _run(result: RunResult) -> list[str]:
    lines = [
        f"- Run `{result.run_id}` · adopt {result.adopt_version}",
        f"- Generated {format_timestamp(result.generated_at)}",
        f"- {len(result.index.files)} files indexed of {result.index.discovered} discovered",
    ]
    disclosure = result.index.disclosure()
    if disclosure:
        # Sampling is a first-screen fact, not an appendix. A sampled run that
        # reads as a complete one is the most expensive thing this file could do.
        lines.append(f"- **SAMPLED:** {disclosure}")
    if result.dry_run:
        lines.append("- **Dry run:** nothing was written to the store.")
    return lines


def _counts(result: RunResult) -> list[str]:
    counts = result.counts_by_kind()
    if not counts:
        return ["- No referents were emitted."]
    return [f"- `{kind}` — {count}" for kind, count in counts.items()]


def _coverage(result: RunResult) -> list[str]:
    if result.coverage is None:
        return ["- Coverage was not computed (dry run)."]
    lines = [f"- {result.coverage.headline()}"]
    blockers = result.coverage.blockers()
    if blockers and result.coverage.covered < result.coverage.discovered:
        # B1-CR-62. A bare ratio says *how much*; only the reasons say what to do
        # about it, and today every identity is blocked on one input this build
        # is forbidden to supply.
        lines.append("- Uncovered because: " + ", ".join(f"`{reason}`" for reason in blockers))
    if not result.coverage.cache_agreement:
        lines.append(
            "- **Cache drift is a defect signal.** The cache disagreed with "
            "`recompute_coverage()` before this run rebuilt it; every figure above "
            "comes from the recompute, never from the cache."
        )
    return lines


def _degradations(result: RunResult) -> list[str]:
    if not result.degradations and not result.truncated_families:
        return ["- None. Every planned family was covered at its declared method."]
    lines = [f"- {entry.headline()}" for entry in result.degradations]
    if result.truncated_families:
        lines.append(
            "- **Truncated by budget:** "
            + ", ".join(f"`{family}`" for family in result.truncated_families)
            + ". This map is incomplete and does not claim otherwise."
        )
    for extractor, reason in result.skipped:
        lines.append(f"- `{extractor}` did not run — {reason}")
    return lines


def _outside_vcs(result: RunResult) -> list[str]:
    uris = result.outside_vcs()
    total = result.total_facts()
    if not uris:
        return [
            f"- 0 of {total} behaviour-bearing settings live outside version control.",
        ]
    return [
        f"- **{len(uris)} of {total} behaviour-bearing settings live outside version "
        "control; changes there produce no commit.**",
        *[f"  - `{uri}`" for uri in uris[:MAP_FIRST_SCREEN_LIST_MAX]],
    ]


_RENDERERS: Final[dict[str, Callable[[RunResult], list[str]]]] = {
    "System and environment": _system,
    "Archetype and tier": _archetype,
    "Run": _run,
    "Counts by kind": _counts,
    "Coverage": _coverage,
    "Degradations": _degradations,
    "Outside version control": _outside_vcs,
}


def _first_screen(result: RunResult) -> list[str]:
    lines: list[str] = ["# System surface map", ""]
    for index, heading in enumerate(FIRST_SCREEN, start=1):
        lines.append(f"## {index}. {heading}")
        lines.extend(_RENDERERS[heading](result))
        lines.append("")
    return lines


def render_stage1(result: RunResult) -> str:
    """The stage-1 map -- `01` F11.2, emitted before the remaining families run.

    Same first screen, plus a notice naming which of
    `MAP_STAGE1_REQUIRED_FAMILIES` this stage covers. A stage-1 artifact that did
    not say it was partial would be indistinguishable from a complete one that
    happened to find less.
    """
    covered = [
        family for family in MAP_STAGE1_REQUIRED_FAMILIES if family in result.counts_by_kind()
    ]
    lines = _first_screen(result)
    lines.extend(
        [
            "## Still running",
            "",
            "This is the **stage-1 map**. It covers the required families present in this "
            "tree: " + (", ".join(f"`{family}`" for family in covered) or "none yet") + ".",
            "",
            "The full map replaces this file when extraction completes. Nothing here "
            "claims to be a complete inventory.",
            "",
        ]
    )
    return "\n".join(lines)


def render_markdown(result: RunResult) -> str:
    """The complete `surface.md`: the normative first screen, then the inventory."""
    lines = _first_screen(result)
    lines.extend(["## 8. Inventory", ""])
    minted = result.minted()
    if not minted:
        lines.extend(["No referents were emitted by this run.", ""])
        return "\n".join(lines)

    current_kind = ""
    for entry in minted:
        if entry.fact.identity_kind != current_kind:
            current_kind = entry.fact.identity_kind
            lines.extend([f"### `{current_kind}`", ""])
        flags = _flags(entry.fact.outside_vcs, entry.fact.opaque)
        lines.append(
            f"- `{entry.uri}` — {entry.fact.title} ({entry.method} {entry.confidence:.2f}{flags})"
        )
    lines.append("")
    if result.write_result is not None and result.write_result.gaps:
        lines.extend(["### Gaps", ""])
        lines.extend(
            f"- `{gap.identity_uri}` — {gap.reason}: {gap.detail}"
            for gap in result.write_result.gaps
        )
        lines.append("")
    return "\n".join(lines)


def _flags(outside_vcs: bool, opaque: bool) -> str:
    marks: Sequence[str] = [
        *(["outside-VCS"] if outside_vcs else []),
        *(["opaque"] if opaque else []),
    ]
    return f", {', '.join(marks)}" if marks else ""
