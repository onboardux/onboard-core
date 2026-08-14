"""Module-level extractors the scheduler suite can actually run in a subprocess.

**They live in a module rather than inside a test function, and that is the
point.** `03` §6 runs extractors in a `spawn` process pool, and `spawn` pickles
the callable and re-imports the module in the child. An extractor defined inside
a test body is unpicklable, so a suite that defined its fakes locally could only
ever test the sequential path -- and would report the process pool as covered.

Each one exists to produce a *specific* scheduler outcome:

* `HangingExtractor` -- the watchdog (`01` F7.3). Blocks forever; only a process
  can be stopped, which is why the pool is processes and not threads.
* `CrashingExtractor` -- failure isolation (`01` F7.4, `02` §7 obligation 8).
* `KindViolatingExtractor` -- obligation 4, at *emission* rather than at
  registration, which is the case the writer's manifest check catches.
* `BudgetRespectingExtractor` -- obligation 7. Yields, then hits an exhausted
  budget, so `truncated` keeps what it produced.
* `QuietExtractor` -- the control. Without one, a scheduler that failed every
  extractor would pass every failure test above.
"""

import time
from collections.abc import Iterator
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SurfaceFact

__all__ = [
    "BudgetRespectingExtractor",
    "CrashingExtractor",
    "HangingExtractor",
    "KindViolatingExtractor",
    "QuietExtractor",
]

_HANG_SECONDS: Final[int] = 3600


def _manifest(identifier: str, *, kinds: list[str] | None = None) -> ExtractorManifest:
    return ExtractorManifest(
        id=identifier,
        version="1.0.0",
        pack="common",
        archetypes=[],
        kinds=kinds or ["symbol"],  # type: ignore[arg-type]
        method="regex",
        fallback="common.regex",
    )


def _symbol(key: str) -> SurfaceFact:
    return SurfaceFact(
        identity_kind="symbol", namespace="python", local_key=key, title=key, attributes={}
    )


class QuietExtractor:
    """Emits one fact and returns. The control case."""

    def manifest(self) -> ExtractorManifest:
        return _manifest("common.quiet")

    def applies_to(self, root: str) -> bool:
        del root
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        ctx.budget.check()
        yield _symbol("quiet.one")


class HangingExtractor:
    """Blocks past any watchdog. The reason the pool runs processes."""

    def manifest(self) -> ExtractorManifest:
        return _manifest("common.hanging")

    def applies_to(self, root: str) -> bool:
        del root
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        del ctx
        # A real hang, not a `pytest` marker: `03` §5 bans sleeps in *tests*, and
        # this is the subject under test rather than a test waiting for one. The
        # watchdog is what ends it, and the watchdog ending it is the assertion.
        time.sleep(_HANG_SECONDS)
        yield _symbol("never.reached")  # pragma: no cover -- terminated first


class CrashingExtractor:
    """Raises a plain exception. Isolation, not a typed error."""

    def manifest(self) -> ExtractorManifest:
        return _manifest("common.crashing")

    def applies_to(self, root: str) -> bool:
        del root
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        del ctx
        raise RuntimeError("this extractor is broken")
        yield  # pragma: no cover -- unreachable, kept so the body is a generator


class KindViolatingExtractor:
    """Declares `symbol` and emits `endpoint`. Obligation 4 at emission time."""

    def manifest(self) -> ExtractorManifest:
        return _manifest("common.kind_violating")

    def applies_to(self, root: str) -> bool:
        del root
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        ctx.budget.check()
        yield SurfaceFact(
            identity_kind="endpoint",
            namespace="http",
            local_key="GET /undeclared",
            title="GET /undeclared",
            attributes={"http_method": "GET", "path": "/undeclared"},
        )


class BudgetRespectingExtractor:
    """Yields one fact, then checks an exhausted budget.

    The order matters: `02` §8 exit 3 emits *"stage-1 artifacts at minimum"*, so a
    truncated extractor must keep what it produced before the budget stopped it.
    """

    def manifest(self) -> ExtractorManifest:
        return _manifest("common.budgeted")

    def applies_to(self, root: str) -> bool:
        del root
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        yield _symbol("budgeted.first")
        ctx.budget.check()
        yield _symbol("budgeted.second")  # pragma: no cover -- budget stops first
