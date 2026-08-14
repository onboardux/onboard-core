"""Method -> confidence, and the degrade ladder -- `01` F9, `03` §5.8.

**The framework assigns confidence, never the extractor** (`02` §7 obligation 5).
An extractor declares how it recovered a fact -- `grammar`, `reflection`,
`declared`, `ctags`, `regex` -- and this module maps that to a band. The plugin
audit rejects a module that tries to set its own (`plugins.AUDIT_RULES`
`self_confidence`), because an extractor that could promote a regex guess to
grammar-level certainty would make the ladder advisory.

**The ladder is strict and it terminates in decline** (`01` F9.2): grammar ->
ctags -> regex -> **decline and record the gap**. Never a guess. The last rung is
the one that matters: `01` §1.6 makes *"silence beats guessing"* an invariant, and
a ladder that bottomed out in "emit it anyway at low confidence" would be a
ladder whose bottom rung is exactly the behaviour the invariant forbids.

**Every transition is recorded, and appears in three places** (`01` F9.4): the
revision's method record, the run report, and the **first screen** of
`surface.md`. A degradation below the fold is a degradation nobody acted on, so
`03` §5.9 makes the first screen normative and this module produces the row that
lands there.

**`reflection` and `declared` are not ladder rungs.** They are evidence methods
an extractor either has or does not: an OpenAPI document either exists or it
does not, and there is no degrading *into* reading one. The ladder is about what
happens when the **grammar** for a language is unavailable, which is the case
`01` F9's acceptance signal names.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

import adopt_const
from adopt_const import MAP_MIN_EMIT_CONFIDENCE
from adopt_map.schemas.surface import EvidenceMethod

__all__ = [
    "LADDER",
    "Degradation",
    "LadderOutcome",
    "LadderPolicy",
    "confidence_for",
    "emits",
    "with_counts",
]

#: Method -> the name of its `03` §3 constant. Held as names and resolved through
#: `adopt_const` at call time, so a retune in the constants table reaches every
#: call site and no band is ever inlined here (`00` §9 rule 2).
_CONFIDENCE_CONSTANTS: Final[dict[str, str]] = {
    "grammar": "MAP_CONF_GRAMMAR",
    "reflection": "MAP_CONF_REFLECTION",
    "declared": "MAP_CONF_DECLARED",
    "ctags": "MAP_CONF_CTAGS",
    "regex": "MAP_CONF_REGEX",
    "agent": "MAP_CONF_AGENT_REVIEWED",
}

#: The ladder, in order. `01` F9.2's *"strictly grammar -> ctags -> regex ->
#: decline"*. Declining is the absence of a further rung rather than a member,
#: because a member named "decline" would be one somebody could assign a
#: confidence to.
LADDER: Final[tuple[EvidenceMethod, ...]] = ("grammar", "ctags", "regex")

#: Why a rung was unavailable. Closed, because these strings reach
#: `surface.json`'s `degradations[].reason` (`02` §9.2) and an integrator reading
#: that field needs a vocabulary rather than a sentence.
_REASON_GRAMMAR: Final[str] = "grammar_unavailable"
_REASON_TOOL: Final[str] = "tool_unavailable"
_REASON_DECLINED: Final[str] = "no_method_available"


def confidence_for(method: EvidenceMethod) -> float:
    """The band for an evidence method -- `01` F9.1, `03` §3."""
    return float(getattr(adopt_const, _CONFIDENCE_CONSTANTS[method]))


def emits(method: EvidenceMethod) -> bool:
    """Whether facts from `method` are written as knowledge at all.

    Below `MAP_MIN_EMIT_CONFIDENCE` a fact is a **gap**, not knowledge (`01`
    F9.3, F3.4). Asked of the method rather than of the fact, because the band is
    a property of how the fact was recovered and nothing downstream may raise it.
    """
    return confidence_for(method) >= MAP_MIN_EMIT_CONFIDENCE


@dataclass(frozen=True, slots=True)
class Degradation:
    """One recorded ladder transition -- `02` §9.2's `degradations[]` shape.

    `affected` is a count rather than a list of URIs: the first screen needs the
    magnitude, and a run degrading 142 Kotlin symbols would otherwise put 142
    URIs on the honest headline and bury it.
    """

    kind: str
    language: str | None
    from_method: EvidenceMethod
    to_method: EvidenceMethod | None
    reason: str
    affected: int = 0

    def as_report_row(self) -> dict[str, object]:
        """The `02` §9.2 field names, which are not this dataclass's.

        `from` and `to` are Python keywords, so the dataclass cannot carry them
        and the rename happens here -- once, at the boundary, rather than in each
        of the four emitters.
        """
        return {
            "kind": self.kind,
            "language": self.language,
            "from": self.from_method,
            "to": self.to_method,
            "reason": self.reason,
            "affected": self.affected,
        }

    def headline(self) -> str:
        """The first-screen sentence (`03` §5.9 invariant 2)."""
        where = f"{self.kind}/{self.language}" if self.language else self.kind
        if self.to_method is None:
            return f"{where}: {self.reason} -- declined, {self.affected} referents recorded as gaps"
        return (
            f"{where}: {self.reason} -- {self.from_method} to {self.to_method} "
            f"({self.affected} items, confidence {confidence_for(self.to_method):.2f})"
        )


@dataclass(frozen=True, slots=True)
class LadderOutcome:
    """Where the ladder landed for one `(kind, language)`.

    `method is None` is the decline. It is a first-class outcome with its own
    recorded transition, not an error and not an empty result -- `01` F9.2's
    ladder *ends* there, and a caller that treated `None` as "nothing to do"
    would drop the gap the decline exists to produce.
    """

    kind: str
    language: str | None
    method: EvidenceMethod | None
    transitions: tuple[Degradation, ...]

    @property
    def declined(self) -> bool:
        return self.method is None

    @property
    def confidence(self) -> float | None:
        return None if self.method is None else confidence_for(self.method)


class LadderPolicy:
    """Resolves the ladder for one run, against what this machine actually has.

    Args:
        available: `(method, language) -> bool`. Injected rather than probed,
            because the ctags rung's availability is a property of the machine
            and a policy that reached for `shutil.which` itself could not be
            table-tested against the tool-absent arm -- which is the arm `05`
            S1.3 names explicitly.
    """

    __slots__ = ("_available",)

    def __init__(self, available: Callable[[EvidenceMethod, str | None], bool]) -> None:
        self._available = available

    def resolve(self, kind: str, language: str | None) -> LadderOutcome:
        """Walk the ladder for one family, recording every step.

        Returns:
            The outcome. `transitions` holds one `Degradation` per step taken
            *past* the first rung, plus a final declining row when no rung was
            available at all. An undegraded family returns an empty tuple, which
            is what lets the first screen print degradations without filtering
            for "no change".
        """
        transitions: list[Degradation] = []
        for index, rung in enumerate(LADDER):
            if self._available(rung, language):
                return LadderOutcome(
                    kind=kind, language=language, method=rung, transitions=tuple(transitions)
                )
            following = LADDER[index + 1] if index + 1 < len(LADDER) else None
            transitions.append(
                Degradation(
                    kind=kind,
                    language=language,
                    from_method=rung,
                    to_method=following,
                    reason=_REASON_GRAMMAR if rung == "grammar" else _REASON_TOOL,
                )
            )
        transitions.append(
            Degradation(
                kind=kind,
                language=language,
                from_method=LADDER[-1],
                to_method=None,
                reason=_REASON_DECLINED,
            )
        )
        return LadderOutcome(
            kind=kind, language=language, method=None, transitions=tuple(transitions)
        )


def with_counts(
    degradations: Sequence[Degradation], counts: dict[tuple[str, str | None], int]
) -> tuple[Degradation, ...]:
    """Stamp observed counts onto recorded transitions.

    The ladder resolves before extraction and the count is only known after, so
    the two are joined here rather than by making `Degradation` mutable. A
    mutable degradation is one a later stage could quietly re-point at a
    different family.
    """
    return tuple(
        Degradation(
            kind=entry.kind,
            language=entry.language,
            from_method=entry.from_method,
            to_method=entry.to_method,
            reason=entry.reason,
            affected=counts.get((entry.kind, entry.language), 0),
        )
        for entry in degradations
    )
