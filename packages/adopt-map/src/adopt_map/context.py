"""What an extractor is given, and what it is given no way to reach.

`02` §7 obligation 7 -- *"Budget-aware. Calls `ctx.budget.check()` at least once
per file"* -- names a `ctx` the protocol had no parameter for. S1.1 shipped
`extract(root: str)` because the write path was the sprint's subject and a
context with nothing in it would have been a placeholder. **B1-CR-58** records the
signature change: `extract(ctx)` , with the root on the context, because an
obligation about a `ctx` cannot be honoured by an extractor that never receives
one, and an *optional* context would make obligation 7 unenforceable by
construction.

**What the context carries is the whole isolation argument.** An extractor gets
the file index (one walk, already done), a budget it can only *read*, the
archetype and the negotiated tier. It does **not** get: the resolved scope, a
store handle, a URI builder, a confidence setter, or a writable path. Those four
absences are `02` §7 obligations 2, 5 and 6 expressed as missing fields rather
than as rules somebody has to follow -- the same argument
`adopt_map.schemas.surface` makes about `SurfaceFact`, one layer out.

The context is a frozen dataclass of picklable parts, because `03` §6 runs
extractors in a **process pool** and everything crossing that boundary is
serialized. A context holding a live handle could not cross it at all, which is a
useful accident: the thing that makes isolation testable is the same thing that
makes it enforceable.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from adopt_map.fileindex import FileEntry, FileIndex, read_text
from adopt_obs import AdoptError, ErrorCode

__all__ = ["Budget", "ExtractorContext"]


@dataclass(frozen=True, slots=True)
class Budget:
    """The two `03` §3 deadlines, as absolute epoch seconds.

    **Epoch and not `monotonic`**, deliberately: a budget crosses into a worker
    process, and `time.monotonic()` has no shared origin between processes, so a
    monotonic deadline is meaningless on the other side of the pool. `now` is
    injected so a test can drive exhaustion without sleeping -- `03` §5 bans
    sleeps in tests and this is what makes that possible for the budget path.
    """

    stage1_deadline: float
    total_deadline: float
    now: Callable[[], float] = field(default=time.time)

    @classmethod
    def starting_at(cls, start: float, *, stage1_s: float, total_s: float) -> "Budget":
        """A budget measured from `start`, in seconds."""
        return cls(stage1_deadline=start + stage1_s, total_deadline=start + total_s)

    def remaining(self) -> float:
        """Seconds left before the total deadline; negative once past it."""
        return self.total_deadline - self.now()

    def stage1_remaining(self) -> float:
        return self.stage1_deadline - self.now()

    def exhausted(self) -> bool:
        return self.remaining() <= 0

    def check(self) -> None:
        """`02` §7 obligation 7. Call at least once per file.

        Raises:
            AdoptError: ``MAP_BUDGET_EXHAUSTED``. Exit code **3** -- a successful
                run with less output (`02` §8), not a failure: stage-1 artifacts
                are emitted and the transaction commits what completed. An
                extractor that lets this propagate is doing the right thing; the
                scheduler catches it and records the family as truncated.
        """
        if self.exhausted():
            raise AdoptError(
                ErrorCode.MAP_BUDGET_EXHAUSTED,
                message="the total map budget elapsed during extraction",
                hint="Raise `--budget`, narrow the tree with `.adoptignore`, or accept the "
                "partial map: exit 3 emits stage-1 artifacts and commits what "
                "completed, and `truncated_families[]` names what is missing.",
            )


@dataclass(frozen=True, slots=True)
class ExtractorContext:
    """One extractor's whole world for one run.

    Note what is **absent**: no `ResolvedScope`, no store, no `build_uri`, no
    confidence field and no writable path. See the module docstring -- those
    absences are `02` §7 obligations 2, 5 and 6, and they are why a fuzzed
    extractor cannot emit a production URI from a staging run.
    """

    root: str
    index: FileIndex
    budget: Budget
    archetype: str
    tier: str | None = None
    #: Languages a **higher ladder rung** already recovered symbols from.
    #:
    #: This is `01` F9.2's ladder made operative rather than merely reported.
    #: The ladder *"runs strictly grammar -> ctags -> regex -> decline, per
    #: identity family per language"*, which means a lower rung must not run
    #: where a higher one succeeded -- and the cost of getting that wrong is not
    #: duplicated effort, it is **two extractors minting the same URI for one
    #: referent with different digests**. The orchestrator fills this in between
    #: rungs; a lower-rung extractor asks `covers()` before reading a file.
    covered_languages: frozenset[str] = frozenset()

    def files(self, *, language: str | None = None) -> tuple[FileEntry, ...]:
        """The indexed files, optionally in one language.

        Reading through the context rather than walking is what keeps `03` §5.8's
        one-walk guarantee true: an extractor has no root to walk that the
        framework did not already index and disclose.
        """
        if language is None:
            return self.index.files
        return self.index.by_language(language)

    def covers(self, entry: FileEntry) -> bool:
        """Whether a higher ladder rung already recovered this file's language."""
        return entry.language is not None and entry.language in self.covered_languages

    def at_rung(self, covered: frozenset[str]) -> "ExtractorContext":
        """This context with a wider set of already-covered languages.

        A new instance rather than a mutation: the context crosses a process
        boundary by value, and a mutable one would let a worker's view of the
        ladder differ from the orchestrator's.
        """
        return ExtractorContext(
            root=self.root,
            index=self.index,
            budget=self.budget,
            archetype=self.archetype,
            tier=self.tier,
            covered_languages=covered,
        )

    def text(self, entry: FileEntry) -> str:
        """One indexed file's text. Bytes in, string out; **never executed**."""
        return read_text(self.index, entry)
