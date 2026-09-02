"""The elicitation agenda: the open gaps, as questions somebody can answer.

v6.1 §6 Build 9 step 2: *"gap-elicitation pass (open coverage_gap rows ->
targeted SME asks)."* v5 §7.8 says why it is the step that pays for the product:
*"ask experts only about the gaps -- this is where the saving lives: the gap list
replaces the broad knowledge-transfer interview, and an event that runs a general
interview anyway has not used the product."*

Three decisions:

**A dispositioned gap is not an open one.** `resolved` means somebody wrote the
knowledge; a `waived` gap with a live expiry is a decision not to write it. Both
are removed from the agenda -- and an **expired** waiver comes back, which is the
whole reason `waived_until` is mandatory. Existence is still derived: this
module is handed the gaps `recompute_coverage` produced and cannot invent one.

**Grouped by owner, because the agenda is a meeting.** An FDE books the SME whose
name is on the gaps; a flat list ordered by severity would have them reading out
questions for four different people. Gaps nobody has claimed sort last, under
`unassigned`, because those are the ones the session has to find an owner for.

**One templated ask per kind, and no model.** The ask names the referent and asks
the one question that kind's absence leaves open -- what an endpoint is for, what
a config key does when it changes, what a job's failure means. R3 requires the
no-model mode to be complete, and a generated question here would be a model on
the path of the step that produces the meeting agenda. If real sessions find the
templates too generic, the trigger in the sprint plan routes that through the
existing drafting module rather than a new dependency.
"""

import datetime as _dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from adopt_handover.views import PackConflict, PackGap

__all__ = [
    "ASKS_BY_KIND",
    "GENERIC_ASK",
    "UNASSIGNED",
    "Agenda",
    "AgendaGroup",
    "AgendaItem",
    "agenda",
    "is_open_gap",
    "render_agenda",
]

#: Where a gap nobody owns is grouped. Sorted last by `agenda`, because the
#: session's first job on these is to find the person, not the answer.
UNASSIGNED: Final[str] = "unassigned"

#: One ask per identity kind, `{uri}` filled in. The thirteen kinds are the
#: manifest's `identity_kind` enum; `?` is `rank_gaps`' own marker for a URI it
#: could not parse, and it gets the generic ask rather than being dropped -- a
#: gap the agenda silently omitted is the failure this whole build is against.
ASKS_BY_KIND: Final[dict[str, str]] = {
    "endpoint": "What is `{uri}` for, who calls it, and what should happen when it fails?",
    "db_field": "What does `{uri}` mean, who writes it, and what values are legitimate?",
    "state_transition": "When does `{uri}` happen, what triggers it, and what must be true first?",
    "symbol": "What is `{uri}` responsible for, and what would break if it changed?",
    "metadata_component": "What is `{uri}` for, and who maintains it?",
    "prompt": "What is `{uri}` trying to achieve, and how do you tell a bad output from a good one?",
    "tool_schema": "When should `{uri}` be called, and what are its failure modes?",
    "model_pin": "Why is `{uri}` pinned to this model, and what has to be checked before changing it?",
    "retrieval_config": "What does `{uri}` retrieve from, and how would you know it had gone stale?",
    "flag": "What does `{uri}` switch on, who may change it, and what is the safe default?",
    "job": "What does `{uri}` do, on what schedule, and what should someone do when it fails?",
    "config_key": "What does `{uri}` control, what is a safe value, and who changes it?",
    "ui_component": "Who uses `{uri}`, and what should they be able to do with it?",
}

#: For a kind no template covers -- a new `identity_kind` shipped before this map
#: was extended. It still asks a real question, so the agenda degrades to
#: "slightly blunter" rather than to "silently shorter".
GENERIC_ASK: Final[str] = "What is `{uri}` for, and what does a new owner need to know about it?"


@dataclass(frozen=True, slots=True)
class AgendaItem:
    """One thing to ask about, and why it is on the agenda."""

    uri: str
    kind: str
    gap_key: str
    ask: str
    reasons: tuple[str, ...] = ()
    #: `acknowledged` when somebody has already booked a session for it; `None`
    #: when nobody has recorded a decision at all.
    status: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class AgendaGroup:
    """The asks for one owner -- one SME session's worth."""

    owner: str
    items: tuple[AgendaItem, ...]

    @property
    def is_unassigned(self) -> bool:
        return self.owner == UNASSIGNED


@dataclass(frozen=True, slots=True)
class Agenda:
    """The whole elicitation pass: what to ask, of whom, plus the contradictions."""

    groups: tuple[AgendaGroup, ...]
    conflicts: tuple[PackConflict, ...]

    @property
    def item_count(self) -> int:
        return sum(len(group.items) for group in self.groups)

    def by_owner(self) -> dict[str, int]:
        """Owner -> how many asks. Recorded on the step's audit row."""
        return {group.owner: len(group.items) for group in self.groups}


def is_open_gap(gap: PackGap, *, now: _dt.datetime) -> bool:
    """Whether `gap` still needs asking about.

    `resolved` is done. `waived` is a decision **while it lasts**: a waiver whose
    `waived_until` has passed is back on the agenda, which is the entire purpose
    of the mandatory expiry Build 4 enforces. A waiver with no date cannot exist
    (`GAP_WAIVER_NEEDS_UNTIL`), and one that somehow did is treated as expired --
    the safe direction, because the alternative is a gap that silently never
    comes back.
    """
    if gap.status == "resolved":
        return False
    if gap.status == "waived":
        return gap.waived_until is None or gap.waived_until <= now
    return True


def ask_for(kind: str, uri: str) -> str:
    """The templated question for one uncovered referent."""
    return ASKS_BY_KIND.get(kind, GENERIC_ASK).format(uri=uri)


def agenda(
    gaps: Sequence[PackGap],
    conflicts: Sequence[PackConflict] = (),
    *,
    now: _dt.datetime,
) -> Agenda:
    """The open gaps as asks, grouped by owner; `unassigned` last.

    Args:
        gaps: Derived gaps joined to their dispositions -- `_pack_support.
            build_gaps`' output, so the agenda and the pack's gap appendix are
            reading the same rows.
        conflicts: Open conflicts (Bet 4). They are on the agenda because a
            probe contradicting confirmed knowledge is a question for the same
            people, and one that has an answer they will want to give.
        now: Injected, because waiver expiry is a comparison against a clock and
            this module holds none -- the same reason `assemble` takes a
            freshness reader rather than resolving freshness itself.
    """
    grouped: dict[str, list[AgendaItem]] = {}
    for gap in gaps:
        if not is_open_gap(gap, now=now):
            continue
        owner = gap.owner_actor_id or UNASSIGNED
        grouped.setdefault(owner, []).append(
            AgendaItem(
                uri=gap.uri,
                kind=gap.kind,
                gap_key=gap.gap_key,
                ask=ask_for(gap.kind, gap.uri),
                reasons=tuple(gap.reasons),
                status=gap.status,
                note=gap.note,
            )
        )

    groups = [
        AgendaGroup(owner=owner, items=tuple(sorted(items, key=lambda item: (item.kind, item.uri))))
        for owner, items in grouped.items()
    ]
    # Named owners alphabetically, `unassigned` last: the named groups are
    # bookable sessions, and the unassigned block is the one the FDE has to
    # triage rather than schedule.
    groups.sort(key=lambda group: (group.is_unassigned, group.owner))
    return Agenda(groups=tuple(groups), conflicts=tuple(conflicts))


def render_agenda(value: Agenda) -> str:
    """The agenda as Markdown. A pure function of `value`; no clock, no I/O.

    Byte-stable for the pack's reason: the same store elicited twice produces
    the same file, so an agenda in a repository does not show up as a change.
    """
    lines: list[str] = ["# Elicitation agenda", ""]
    if value.item_count == 0 and not value.conflicts:
        lines.extend(
            [
                "No open gaps and no open conflicts in this scope.",
                "",
                "Every mapped identity has confirmed knowledge bound to it. There is "
                "nothing to elicit -- go straight to the pack.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            f"{value.item_count} open "
            f"{'gap' if value.item_count == 1 else 'gaps'} across "
            f"{len(value.groups)} {'owner' if len(value.groups) == 1 else 'owners'}.",
            "",
            "Ask only about what is below. Anything already covered by confirmed "
            "knowledge is deliberately absent -- that is the point of the pass.",
            "",
        ]
    )

    for group in value.groups:
        heading = "Unassigned — find an owner first" if group.is_unassigned else group.owner
        lines.extend([f"## {heading}", ""])
        for item in group.items:
            lines.append(f"- **{item.uri}** ({item.kind})")
            lines.append(f"  - {item.ask}")
            if item.status:
                note = f" — {item.note}" if item.note else ""
                lines.append(f"  - _disposition: {item.status}{note}_")
        lines.append("")

    if value.conflicts:
        lines.extend(
            [
                "## Contradictions to resolve",
                "",
                "A probe observed behaviour that contradicts knowledge somebody "
                "confirmed. Ask which one is right -- the answer is a deliverable "
                "either way.",
                "",
            ]
        )
        for conflict in value.conflicts:
            lines.append(f"- **{conflict.uri}** ({conflict.kind})")
        lines.append("")

    return "\n".join(lines)
